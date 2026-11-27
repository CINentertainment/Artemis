"""Explicit collection priorities, daily admission limits and explained planning.

Budget units are attempts, not tokens, source counts, verified events or returns.
Direct operator research remains outside these automatic-work limits.
"""
import json,re,hashlib,logging
from datetime import datetime,timezone,timedelta
from threading import RLock,Event,Thread
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field,model_validator
from typing import Literal
from sqlalchemy import String,Text,Float,Integer,select,func,update
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.research_models import ResearchRun,ResearchRequisition
from app.services.encryption import protect_text,unprotect_text
LOCK=RLock();LOG=logging.getLogger(__name__)
class CollectionSettings(Base):
    __tablename__='collection_planner_settings'
    id:Mapped[str]=mapped_column(String(20),primary_key=True)
    payload:Mapped[str]=mapped_column(Text)
class CollectionPlan(Base):
    __tablename__='collection_plans'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    kind:Mapped[str]=mapped_column(String(16),index=True)
    target:Mapped[str]=mapped_column(String(80),index=True)
    payload:Mapped[str]=mapped_column(Text)
class CollectionDay(Base):
    __tablename__='collection_daily_allowances'
    id:Mapped[str]=mapped_column(String(40),primary_key=True)
    used:Mapped[int]=mapped_column(Integer,default=0)
class CollectionCharge(Base):
    __tablename__='collection_attempts'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    day:Mapped[str]=mapped_column(String(10),index=True)
    lane:Mapped[str]=mapped_column(String(16),index=True)
    target:Mapped[str]=mapped_column(String(80),index=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    payload:Mapped[str]=mapped_column(Text)
class CollectionDecision(Base):
    __tablename__='collection_decisions'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    payload:Mapped[str]=mapped_column(Text)
class CollectionJob(Base):
    __tablename__='collection_jobs'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    plan_id:Mapped[str]=mapped_column(String(36),index=True)
    state:Mapped[str]=mapped_column(String(24),index=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    payload:Mapped[str]=mapped_column(Text)
def now():return datetime.now(timezone.utc)
def day():return now().astimezone(ZoneInfo('America/Chicago')).date().isoformat()
def pack(value):return protect_text(json.dumps(value,ensure_ascii=False))
def unpack(row):return json.loads(unprotect_text(row.payload))
def digest(*args):return hashlib.sha256('\n'.join(map(str,args)).encode()).hexdigest()
def config(db):
    row=db.get(CollectionSettings,'default');return {'enabled':False,'world':6,'events':3,'markets':6,**(unpack(row)if row else{})}
def plan(db,key):
    row=db.get(CollectionPlan,str(key))
    if not row:raise HTTPException(404,'Collection priority not found.')
    return row

def decision(db,lane,target,reason,details=None):
    key=digest(day(),lane,target,reason);row=db.get(CollectionDecision,key);stamp=now().timestamp()
    if row:value=unpack(row);value.update(last_seen=stamp,occurrences=value.get('occurrences',1)+1);value.update(details or{});row.payload=pack(value)
    else:db.add(CollectionDecision(id=key,created=stamp,payload=pack({'lane':lane,'target':target,'reason':reason,'last_seen':stamp,'occurrences':1,**(details or{})})))
def matching(db,lane,target):return db.scalars(select(CollectionPlan).where(CollectionPlan.kind==lane,CollectionPlan.target==str(target))).first()
def target_label(db,lane,target):
    row=matching(db,lane,target)
    if row:return unpack(row)['name']
    if lane=='world':
        req=db.get(ResearchRequisition,str(target))
        if req:return req.topic
    if lane=='events':
        from app.services.event_tracking import EventDossier,unpack as event_data
        event=db.get(EventDossier,str(target))
        if event:return event_data(event)['title']
    return str(target)

def score(db,lane,target):
    row=matching(db,lane,target)
    if not row:return 100
    value=unpack(row)
    if not value['enabled']:return -10000
    due=value.get('next_at')or now().timestamp();age=max(0,min((now().timestamp()-due)/3600,72));open_count=sum(q['state']=='open'for q in value['questions'])
    used=db.scalar(select(func.count()).select_from(CollectionCharge).where(CollectionCharge.day==day(),CollectionCharge.lane==lane,CollectionCharge.target==str(target)))or 0
    if used>=value['daily_limit']:return -10000
    return value['weight']*100+age*2+open_count*5-used*40

def rank(lane,targets):
    with SessionLocal()as db:
        if not config(db)['enabled']:return list(targets)
        # Stable sort preserves rotation/order among equally ranked targets.
        return sorted(targets,key=lambda target:score(db,lane,str(target)),reverse=True)

def mission_permitted(db,value):
    if not value.get('mission_id'):return True
    from app.services.mission_control import permitted
    return permitted(db,value)

def admit(db,lane,target,request_id,run_id=None):
    """Called under the shared research mutex, in the workflow's save transaction."""
    settings=config(db)
    if lane in {'world','events','markets'}:
        owned=matching(db,lane,target);owned_data=unpack(owned)if owned else{}
        if owned_data.get('mission_id')and(not settings['enabled']or not mission_permitted(db,owned_data)):
            raise HTTPException(409,'Mission or shared collection planner is paused.')
    if not settings['enabled']or lane not in {'world','events','markets'}:return None
    key=digest(lane,request_id);existing=db.get(CollectionCharge,key)
    if existing:return unpack(existing)
    row=matching(db,lane,target);value=unpack(row)if row else None
    if value and not value['enabled']:raise HTTPException(409,'This target is paused by its collection priority.')
    allowance=settings[lane];bucket_key=day()+':'+lane;bucket=db.get(CollectionDay,bucket_key)
    if bucket is None:bucket=CollectionDay(id=bucket_key,used=0);db.add(bucket);db.flush()
    if value:
        used=db.scalar(select(func.count()).select_from(CollectionCharge).where(CollectionCharge.day==day(),CollectionCharge.lane==lane,CollectionCharge.target==str(target)))or 0
        if used>=value['daily_limit']:raise HTTPException(409,'The collection priority daily attempt limit is reached.')
    result=db.execute(update(CollectionDay).where(CollectionDay.id==bucket_key,CollectionDay.used<allowance).values(used=CollectionDay.used+1))
    if result.rowcount!=1:raise HTTPException(409,'The '+lane+' collection allowance is exhausted for this US Central day.')
    questions=[]
    if value:
        remaining=[q['text']for q in value['questions']if q['state']=='open'];cursor=value.get('question_cursor',0)
        questions=[remaining[(cursor+i)%len(remaining)]for i in range(min(2,len(remaining)))]if remaining else[]
        value['question_cursor']=cursor+len(questions);row.payload=pack(value)
    data={'request_id':str(request_id),'run_id':str(run_id)if run_id else None,'plan_id':row.id if row else None,'name':target_label(db,lane,target),'score':score(db,lane,target),'reason':'Eligible automatic attempt within the saved allowance'+('; explicit priority weight '+str(value['weight'])if value else'; default priority for an unlisted target'),'questions':questions,'domains':value['domains']if value else[],'regions':value['regions']if value else[],'entities':value['entities']if value else[],'outcome':'started','created_at':now().isoformat()}
    db.add(CollectionCharge(id=key,day=day(),lane=lane,target=str(target),created=now().timestamp(),payload=pack(data)));decision(db,lane,str(target),'Automatic research admitted',{'plan_name':data['name'],'score':data['score']});return data

def admit_research(db,req_id,request_id):
    from app.services.work_priority import research_lock
    context=research_lock.collection_context();lane=context.get('lane');target=context.get('target')or req_id
    data=admit(db,lane,target,request_id,request_id)
    if data:research_lock.set_collection_hints(data)
    return data

def lane_available(lane):
    with SessionLocal()as db:
        settings=config(db);bucket=db.get(CollectionDay,day()+':'+lane)
        return not settings['enabled']or(bucket.used if bucket else 0)<settings[lane]

def try_market(symbol,identity):
    with LOCK,SessionLocal()as db:
        try:value=admit(db,'markets',symbol,identity);db.commit();return True,value
        except HTTPException as error:
            db.rollback();decision(db,'markets',symbol,str(error.detail),{'plan_name':target_label(db,'markets',symbol)});db.commit();return False,None

def finish_market(identity,status,record_id=None):
    with LOCK,SessionLocal()as db:
        row=db.get(CollectionCharge,digest('markets',identity))
        if row:value=unpack(row);value.update(outcome=status,record_id=record_id,finished_at=now().isoformat());row.payload=pack(value);db.commit()

def research_deferred(lane,target,reason):
    with LOCK,SessionLocal()as db:decision(db,lane,str(target),reason,{'plan_name':target_label(db,lane,target)});db.commit()

def observe():
    with LOCK,SessionLocal()as db:
        for row in db.scalars(select(CollectionCharge).where(CollectionCharge.lane.in_(['world','events'])).order_by(CollectionCharge.created.desc()).limit(200)).all():
            value=unpack(row)
            if value.get('outcome')!='started'or not value.get('run_id'):continue
            run=db.get(ResearchRun,value['run_id'])
            if not run or run.status=='running':continue
            try:result=json.loads(run.result_json)if run.result_json else{}
            except (ValueError,TypeError):result={}
            if not isinstance(result,dict):result={}
            notes={n['intel_note_id']for n in result.get('event_notes')or[]if isinstance(n,dict)and type(n.get('intel_note_id'))is int and not n.get('error')}
            value.update(outcome=run.status,confirmed_note_ids=sorted(notes),source_count=len(result.get('sources')or[]),finished_at=now().isoformat(),limits='Saved note IDs and source counts are not independent event or corroboration counts. Questions remain open until reviewed.')
            gap_data=result.get('gap_collection');gaps=(gap_data.get('information_needed')or[])if isinstance(gap_data,dict)else[]
            if not isinstance(gaps,list):gaps=[]
            value['reported_gaps']=[g[:500]for g in gaps[:6]if isinstance(g,str)];row.payload=pack(value)
        db.commit()

DOMAIN=re.compile(r'^(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
class Preferences(BaseModel):
    name:str=Field(min_length=3,max_length=120)
    enabled:bool=True
    weight:int=Field(default=3,ge=1,le=5)
    interval_minutes:int=Field(default=720,ge=60,le=10080)
    daily_limit:int=Field(default=2,ge=1,le=12)
    domains:list[str]=Field(default_factory=list,max_length=6)
    regions:list[str]=Field(default_factory=list,max_length=8)
    entities:list[str]=Field(default_factory=list,max_length=8)
    @model_validator(mode='after')
    def clean(self):
        self.name=self.name.strip()
        if len(self.name)<3:raise ValueError('Enter a priority name of at least three characters.')
        self.domains=list(dict.fromkeys(d.strip().lower().rstrip('.')for d in self.domains))
        if any(not DOMAIN.fullmatch(d)for d in self.domains):raise ValueError('Preferred sources must be public domain names, without URLs or paths.')
        if any(not 1<=len(v.strip())<=60 for v in self.regions+self.entities):raise ValueError('Region and entity labels must contain one to sixty characters.')
        self.regions=[v.strip()for v in self.regions];self.entities=[v.strip()for v in self.entities];return self
class Create(Preferences):
    kind:Literal['world','events','markets']='world'
    target:str=Field(default='',max_length=80)
    scope:str=Field(default='',max_length=300)
    questions:list[str]=Field(min_length=1,max_length=6)
    @model_validator(mode='after')
    def question_bounds(self):
        if any(not 5<=len(q.strip())<=300 for q in self.questions):raise ValueError('Each research question must contain five to three hundred characters.')
        self.questions=list(dict.fromkeys(q.strip()for q in self.questions));return self

def same_origin(request:Request):
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(request.headers.get('origin')and request.headers['origin']!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use this Artemis origin for collection planning.')
router=APIRouter(prefix='/collection-planning',dependencies=[Depends(same_origin)])
@router.post('/plans')
def create(body:Create):
    with LOCK,SessionLocal()as db:
        if (db.scalar(select(func.count()).select_from(CollectionPlan))or 0)>=100:raise HTTPException(409,'One hundred priorities already exist. Reuse an existing priority.')
        key=str(uuid4());target=body.target;standalone=False
        if body.kind=='world':
            if target:
                try:target=str(UUID(target))
                except ValueError:raise HTTPException(422,'Use the requisition UUID from the target list.')from None
                req=db.get(ResearchRequisition,target)
                if not req:raise HTTPException(404,'Research requisition not found.')
                scope=req.topic
            else:
                if len(body.scope.strip())<5:raise HTTPException(422,'Enter a research scope for a new standalone plan.')
                target=str(uuid5(NAMESPACE_URL,'artemis-collection:'+key));scope=body.scope.strip();standalone=True
                db.add(ResearchRequisition(id=target,topic=scope,questions_json=json.dumps(body.questions)))
        elif body.kind=='events':
            from app.services.event_tracking import EventDossier,unpack as event_data
            try:target=str(UUID(target))
            except ValueError:raise HTTPException(422,'Choose an event dossier from the target list.')from None
            dossier=db.get(EventDossier,target)
            if not dossier or dossier.state!='active':raise HTTPException(404,'Active event dossier not found.')
            scope=event_data(dossier)['scope']
        else:
            from app.services.market_automation import load
            target=target.strip().upper()
            if target not in load()['symbols']:raise HTTPException(422,'Choose an existing provider-confirmed tracked symbol.')
            scope=target
        if matching(db,body.kind,target):raise HTTPException(409,'This target already has a priority. Edit that priority instead.')
        board_id=None
        if standalone:
            from app.services.intelligence_workspace import IntelligenceBoard
            board_id=uuid5(NAMESPACE_URL,'artemis-collection-board:'+key).hex
            board={'id':board_id,'title':body.name,'notes':{},'entities':{},'links':[],'history':[],'research_bindings':{target:{'topic':scope,'bound_at':now().isoformat()}}}
            db.add(IntelligenceBoard(id=board_id,version=1,payload=pack(board)))
        value=body.model_dump(exclude={'kind','target','scope','questions'});value.update(scope=scope,standalone=standalone,board_id=board_id,board_title=body.name if standalone else None,created_at=now().isoformat(),next_at=now().timestamp(),question_cursor=0,questions=[{'id':str(uuid4()),'text':q.strip(),'state':'open','history':[]}for q in body.questions])
        db.add(CollectionPlan(id=key,kind=body.kind,target=target,payload=pack(value)));db.commit();return {'id':key}
@router.put('/plans/{plan_id}')
def settings(plan_id:UUID,body:Preferences):
    with LOCK,SessionLocal()as db:
        row=plan(db,plan_id);value=unpack(row);was=value['enabled'];value.update(body.model_dump())
        if body.enabled and not was:value['next_at']=now().timestamp()
        row.payload=pack(value)
        if not body.enabled:
            for job in db.scalars(select(CollectionJob).where(CollectionJob.plan_id==row.id,CollectionJob.state=='queued')).all():job.state='cancelled'
        db.commit();return {'saved':True}
class Question(BaseModel):text:str=Field(min_length=5,max_length=300)
@router.post('/plans/{plan_id}/questions')
def add_question(plan_id:UUID,body:Question):
    with LOCK,SessionLocal()as db:
        row=plan(db,plan_id);value=unpack(row)
        if len(value['questions'])>=12:raise HTTPException(409,'This plan has twelve retained questions. Reopen or reuse an existing question.')
        value['questions'].append({'id':str(uuid4()),'text':body.text.strip(),'state':'open','history':[]});row.payload=pack(value);db.commit();return {'saved':True}
class Review(BaseModel):
    state:Literal['open','hold','answered']
    reason:str=Field(min_length=5,max_length=1000)
    note_id:int|None=Field(default=None,gt=0,le=2147483647)
    quote:str=Field(default='',max_length=2000)
@router.put('/plans/{plan_id}/questions/{question_id}')
def review_question(plan_id:UUID,question_id:UUID,body:Review):
    evidence=None
    if body.state=='answered':
        if not body.note_id or not body.quote:raise HTTPException(422,'Attach a saved note and exact narrative quote to mark a question answered.')
        from app.config import get_settings
        from app.connectors.intel_notes import IntelNotesConnector
        from app.services.intelligence_entity_policy import narrative_spans
        with IntelNotesConnector(base_url=str(get_settings().intel_notes_url))as client:note=client.get_note(body.note_id)
        if note.get('id')!=body.note_id or not any(body.quote in s for s in narrative_spans(note.get('body')or'')):raise HTTPException(422,'The answer quote must be copied from the requested note narrative.')
        evidence={'note_id':body.note_id,'quote':body.quote,'classification':note.get('classification')or'Not recorded'}
    with LOCK,SessionLocal()as db:
        row=plan(db,plan_id);value=unpack(row);question=next((q for q in value['questions']if q['id']==str(question_id)),None)
        if question is None:raise HTTPException(404,'Research question not found.')
        question['history'].append({'state':body.state,'reason':body.reason,'at':now().isoformat(),'evidence':evidence});question.update(state=body.state,evidence=evidence);row.payload=pack(value);db.commit();return {'saved':True}
class Budget(BaseModel):
    enabled:bool=False
    world:int=Field(default=6,ge=0,le=100)
    events:int=Field(default=3,ge=0,le=100)
    markets:int=Field(default=6,ge=0,le=100)
    @model_validator(mode='after')
    def total(self):
        if self.world+self.events+self.markets>100:raise ValueError('Keep the combined daily allowance at one hundred attempts or fewer.')
        return self
@router.put('/settings')
def budget_settings(body:Budget):
    with LOCK,SessionLocal()as db:
        row=db.get(CollectionSettings,'default')
        if row:row.payload=pack(body.model_dump())
        else:db.add(CollectionSettings(id='default',payload=pack(body.model_dump())))
        db.commit();return body.model_dump()
@router.get('/status')
def status():
    with SessionLocal()as db:
        settings=config(db);used={lane:(db.get(CollectionDay,day()+':'+lane).used if db.get(CollectionDay,day()+':'+lane)else 0)for lane in ['world','events','markets']}
        plans=[{'id':r.id,'kind':r.kind,'target':r.target,'score':score(db,r.kind,r.target),**unpack(r)}for r in db.scalars(select(CollectionPlan).limit(100)).all()]
        charges=[{'id':r.id,'lane':r.lane,'target':r.target,'created':r.created,**unpack(r)}for r in db.scalars(select(CollectionCharge).order_by(CollectionCharge.created.desc()).limit(100)).all()]
        decisions=[{'id':r.id,'created':r.created,**unpack(r)}for r in db.scalars(select(CollectionDecision).order_by(CollectionDecision.created.desc()).limit(100)).all()]
        jobs=[{'id':r.id,'plan_id':r.plan_id,'state':r.state,**unpack(r)}for r in db.scalars(select(CollectionJob).order_by(CollectionJob.created.desc()).limit(50)).all()]
        return {'settings':settings,'day':day(),'used':used,'plans':plans,'attempts':charges,'decisions':decisions,'jobs':jobs,'worker_alive':bool(worker.thread and worker.thread.is_alive())}

@router.get('/targets')
def targets():
    result={'world':[],'events':[],'markets':[]}
    with SessionLocal()as db:
        result['world']=[{'id':r.id,'label':r.topic}for r in db.scalars(select(ResearchRequisition).order_by(ResearchRequisition.created_at.desc()).limit(100)).all()]
        from app.services.event_tracking import EventDossier,unpack as event_data
        result['events']=[{'id':r.id,'label':event_data(r)['title']}for r in db.scalars(select(EventDossier).where(EventDossier.state=='active').limit(50)).all()]
    from app.services.market_automation import load
    result['markets']=[{'id':s,'label':s}for s in load()['symbols']];return result

def schedule_due():
    with LOCK,SessionLocal()as db:
        if not config(db)['enabled']:return
        rows=db.scalars(select(CollectionPlan).where(CollectionPlan.kind=='world').limit(100)).all()
        for row in sorted(rows,key=lambda r:score(db,r.kind,r.target),reverse=True):
            value=unpack(row)
            if not value['standalone']or not value['enabled']or not mission_permitted(db,value)or value.get('next_at',0)>now().timestamp():continue
            questions=[q for q in value['questions']if q['state']=='open']
            if not questions:
                decision(db,'world',row.target,'No open collection questions; standalone plan waits for review or a new question.');continue
            # Never create another scheduler for a requisition already scheduled elsewhere.
            from app.services.research_scheduler import ResearchSchedule
            scheduled=db.get(ResearchSchedule,row.target)
            if scheduled and scheduled.enabled:
                decision(db,'world',row.target,'Existing research schedule controls this requisition cadence.');continue
            if db.scalars(select(CollectionJob).where(CollectionJob.plan_id==row.id,CollectionJob.state.in_(['queued','running']))).first():continue
            if (db.scalar(select(func.count()).select_from(CollectionJob).where(CollectionJob.state.in_(['queued','running'])))or 0)>=20:break
            cursor=value.get('question_cursor',0);chosen=[questions[(cursor+i)%len(questions)]['text']for i in range(min(2,len(questions)))];identity=str(uuid5(NAMESPACE_URL,'artemis-collection-slot:'+row.id+':'+str(value['next_at'])))
            if db.get(CollectionJob,identity)is None:db.add(CollectionJob(id=identity,plan_id=row.id,state='queued',created=now().timestamp(),payload=pack({'questions':chosen,'error':None,'created_at':now().isoformat()})))
            value['next_at']=now().timestamp()+value['interval_minutes']*60;row.payload=pack(value)
        db.commit()

def reconcile():
    with LOCK,SessionLocal()as db:
        for job in db.scalars(select(CollectionJob).where(CollectionJob.state=='running')).all():
            run=db.get(ResearchRun,job.id);value=unpack(job)
            if run is None:job.state='queued';value['error']='Interrupted before a saved attempt; the same request ID remains queued.'
            elif run.status=='completed':job.state='completed';value['error']=None
            else:job.state='blocked_review';value['error']='Review the saved run and possible partial external writes. No replay was issued.'
            job.payload=pack(value)
        db.commit()

class Worker:
    def __init__(self):self.event=Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        reconcile();self.event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-collection-planning');self.thread.start()
    def stop(self):
        self.event.set()
        if self.thread:self.thread.join()
    def tick(self):
        from app.services.operations_recovery import ready
        from app.routes.research import run_requisition,RunRequest,research_lock
        observe();schedule_due()
        with SessionLocal()as db:
            if not config(db)['enabled']:return
        if not ready(['ollama','search','notes'],'collection plans')or research_lock.locked()or research_lock.pending():return
        with LOCK,SessionLocal()as db:
            jobs=db.scalars(select(CollectionJob).where(CollectionJob.state=='queued').limit(20)).all()
            jobs=sorted(jobs,key=lambda j:score(db,'world',plan(db,j.plan_id).target),reverse=True)
            selected=None
            for job in jobs:
                row=plan(db,job.plan_id);value=unpack(row)
                if not value['enabled']or not mission_permitted(db,value):job.state='cancelled';continue
                active_questions={q['text']for q in value['questions']if q['state']=='open'};payload=unpack(job);payload['questions']=[q for q in payload['questions']if q in active_questions]
                if not payload['questions']:job.state='cancelled';continue
                # Suppress repeated quota attempts while retaining the original queued request.
                bucket=db.get(CollectionDay,day()+':world');used=bucket.used if bucket else 0
                target_used=db.scalar(select(func.count()).select_from(CollectionCharge).where(CollectionCharge.day==day(),CollectionCharge.lane=='world',CollectionCharge.target==row.target))or 0
                if used>=config(db)['world']or target_used>=value['daily_limit']:
                    decision(db,'world',row.target,'Waiting for the next daily collection allowance.');continue
                selected=(job,row,payload);break
            if selected is None:db.commit();return
            job,row,payload=selected;key=job.id;target=row.target
            saved=db.get(ResearchRun,key)
            if saved:
                job.state='completed'if saved.status=='completed'else'blocked_review';db.commit();return
            job.state='running';job.payload=pack(payload);db.commit()
        try:
            with research_lock.background(lane='world',target=target):result=run_requisition(target,RunRequest(request_id=UUID(key),followup_questions=payload['questions']))
            with LOCK,SessionLocal()as db:
                job=db.get(CollectionJob,key);value=unpack(job);job.state='completed'if result.get('status')=='completed'else'blocked_review';value.update(error=result.get('error'),finished_at=now().isoformat());job.payload=pack(value);db.commit()
        except Exception as error:
            with LOCK,SessionLocal()as db:
                job=db.get(CollectionJob,key);saved=db.get(ResearchRun,key);value=unpack(job)
                if isinstance(error,HTTPException)and error.status_code==409 and saved is None:job.state='queued';value['error']=str(error.detail)
                elif saved and saved.status=='completed':job.state='completed';value['error']=None
                else:job.state='blocked_review';value['error']='Research interrupted or unavailable. Review the saved run before initiating another request.'
                job.payload=pack(value);db.commit()
    def loop(self):
        while not self.event.is_set():
            try:self.tick()
            except Exception:LOG.exception('Collection planning worker error')
            self.event.wait(30)
worker=Worker()

def voice(text):
    from app.services.voice_hub import normalize,STATE,read_json
    clean=normalize(text)
    if clean=='what needs my attention':
        with SessionLocal()as db:
            rows=db.scalars(select(CollectionPlan).limit(100)).all();rows=sorted(rows,key=lambda r:score(db,r.kind,r.target),reverse=True)
            open_rows=[r for r in rows if unpack(r)['enabled']and any(q['state']=='open'for q in unpack(r)['questions'])][:3]
            return {'speech':('Highest-ranked open collection priorities: '+'. '.join(unpack(r)['name']+' has '+str(sum(q['state']=='open'for q in unpack(r)['questions']))+' open questions'for r in open_rows)+'.'if open_rows else'No enabled collection priorities with open questions were found.')+' Rank reflects your saved weights and review state, not an independently verified threat level.','link':'/static/priorities.html'}
    if clean=='prioritize this event':
        event_id=read_json(STATE/'active-event.json',{}).get('id')
        if not event_id:return {'speech':'Select an event dossier for voice first.','link':'/static/events.html'}
        with LOCK,SessionLocal()as db:
            row=matching(db,'events',event_id)
            if not row:return {'speech':'Add a collection priority for the selected event on Collection planning first.','link':'/static/priorities.html'}
            data=unpack(row);data['weight']=5;row.payload=pack(data);db.commit();return {'speech':'Raised '+data['name']+' to the highest collection weight. Its existing monitoring settings and daily limits still apply.','link':'/static/priorities.html'}
    match=re.match(r'^focus on (.+)$',clean)
    if match:
        with LOCK,SessionLocal()as db:
            matches=[r for r in db.scalars(select(CollectionPlan).limit(100)).all()if normalize(unpack(r)['name'])==match[1]]
            if len(matches)!=1:return {'speech':'Use the exact name of one saved priority. Create or select the priority on Collection planning if its name is ambiguous.','link':'/static/priorities.html'}
            row=matches[0];data=unpack(row);data['weight']=5;row.payload=pack(data);db.commit();return {'speech':'Raised '+data['name']+' to the highest collection weight. This does not enable a paused worker or increase its budget.','link':'/static/priorities.html'}
    return {'speech':'Open Collection planning to review saved priorities.','link':'/static/priorities.html'}
