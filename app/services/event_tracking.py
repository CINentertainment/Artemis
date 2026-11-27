"""Reviewable event dossiers, literal timelines and bounded monitoring.

Candidate notes are not automatically confirmed event membership. No model is
used to establish identity, dates, conflicts or source independence here.
"""
import hashlib,json,re,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
from threading import RLock,Event,Thread
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field,model_validator
from typing import Literal
from sqlalchemy import String,Text,Float,select,func
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.services.encryption import protect_text,unprotect_text
from app.research_models import ResearchRequisition,ResearchRun
LOG=logging.getLogger(__name__);LOCK=RLock()
class EventDossier(Base):
    __tablename__='event_dossiers'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    state:Mapped[str]=mapped_column(String(16),index=True)
    payload:Mapped[str]=mapped_column(Text)
class EventEvidence(Base):
    __tablename__='event_dossier_evidence'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    dossier_id:Mapped[str]=mapped_column(String(36),index=True)
    state:Mapped[str]=mapped_column(String(16),index=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    payload:Mapped[str]=mapped_column(Text)
class EventNotice(Base):
    __tablename__='event_dossier_notices'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    dossier_id:Mapped[str]=mapped_column(String(36),index=True)
    state:Mapped[str]=mapped_column(String(16),index=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    reviewed_at:Mapped[float|None]=mapped_column(Float,nullable=True,index=True)
    payload:Mapped[str]=mapped_column(Text)
class EventJob(Base):
    __tablename__='event_dossier_jobs'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    dossier_id:Mapped[str]=mapped_column(String(36),index=True)
    state:Mapped[str]=mapped_column(String(24),index=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    started:Mapped[float|None]=mapped_column(Float,nullable=True,index=True)
    payload:Mapped[str]=mapped_column(Text)
def now():return datetime.now(timezone.utc)
def pack(value):return protect_text(json.dumps(value,ensure_ascii=False))
def unpack(row):return json.loads(unprotect_text(row.payload))
def hashed(*values):return hashlib.sha256('\n'.join(map(str,values)).encode()).hexdigest()
def normalized(text):return re.sub(r'\s+',' ',text).strip().casefold()
def get(db,key):
    row=db.get(EventDossier,str(key))
    if row is None:raise HTTPException(404,'Event dossier not found.')
    return row

def narrative(body):
    from app.services.intelligence_entity_policy import narrative_spans
    return [s for s in narrative_spans(body)if s.strip()]
def when_literal(body):
    match=re.search(r'(?mi)^When:\s*(.+)$',body)
    literal=match[1].strip()if match else''
    if literal.casefold()in {'unknown','none','not established','n/a'}:return None,None
    # A complete ISO date only when the entire saved When field is that date.
    try:full=datetime.strptime(literal,'%Y-%m-%d').date().isoformat()if re.fullmatch(r'\d{4}-\d{2}-\d{2}',literal)else None
    except ValueError:full=None
    if full and not any(literal in span for span in narrative(body)):full=None
    return literal[:300]or None,full

def notice(db,dossier_id,kind,identity,title,details):
    key=hashed(dossier_id,kind,identity)
    if db.get(EventNotice,key)is None:db.add(EventNotice(id=key,dossier_id=dossier_id,state='proposed',created=now().timestamp(),reviewed_at=None,payload=pack({'kind':kind,'title':title,**details})))

def ingest(db,dossier,note,quote,run_id=None):
    nid=note.get('id');body=note.get('body')
    if type(nid)is not int or not 0<nid<=2147483647 or not isinstance(body,str)or len(body)>200000:raise HTTPException(422,'A valid saved Intel Note with a readable body is required.')
    spans=narrative(body)
    if not quote.strip()or len(quote)>1000 or not any(quote in s for s in spans):raise HTTPException(422,'Use an exact quote from the note narrative, not its title or metadata.')
    saved={k:note.get(k)for k in ('id','title','body','classification','discipline','location')};revision=hashed(json.dumps(saved,sort_keys=True,ensure_ascii=False));key=hashed(dossier.id,nid,revision)
    existing=db.get(EventEvidence,key)
    if existing:return existing
    count=db.scalar(select(func.count()).select_from(EventEvidence).where(EventEvidence.dossier_id==dossier.id))or 0
    if count>=200:raise HTTPException(409,'This dossier has reached its 200 evidence-snapshot bound.')
    literal,full=when_literal(body);urls=list(dict.fromkeys(re.findall(r'(?m)^URL:\s*(https://\S+)',body)))[:8]
    payload={'note_id':nid,'record':saved,'quote':quote[:5000],'revision':revision,'run_id':run_id,'when_literal':literal,'complete_event_date':full,'source_urls':urls,'grouping_reason':None,'location':note.get('location'),'classification':note.get('classification')or'Not recorded','imported_at':now().isoformat()}
    row=EventEvidence(id=key,dossier_id=dossier.id,state='pending',created=now().timestamp(),payload=pack(payload));db.add(row)
    notice(db,dossier.id,'candidate_note',key,'Note awaiting event grouping',{'evidence_id':key,'note_id':nid,'quote':quote[:1000],'classification':payload['classification'],'source_urls':urls,'reason':'This saved note was proposed for this dossier. Membership, event recency and source independence are not established.'})
    return row

def evaluate(db,dossier,row):
    data=unpack(row);policy=unpack(dossier)['policy'];previous=db.scalars(select(EventEvidence).where(EventEvidence.dossier_id==dossier.id,EventEvidence.state=='reviewed',EventEvidence.id!=row.id).order_by(EventEvidence.created.desc()).limit(200)).all()
    # The same wording is one reported claim, regardless of note ID or host.
    if any(normalized(unpack(p)['quote'])==normalized(data['quote'])for p in previous):
        data['repeated_wording']=True;row.payload=pack(data);return
    same_note=next((p for p in previous if unpack(p)['note_id']==data['note_id']),None)
    common={'evidence_id':row.id,'note_id':data['note_id'],'quote':data['quote'][:1000],'classification':data['classification'],'source_urls':data['source_urls'],'when_literal':data['when_literal']}
    if same_note:
        from app.services.change_followup import number_pairs
        before=unpack(same_note)['quote'];pairs=number_pairs(before,data['quote'])
        notice(db,dossier.id,'numeric_review'if pairs else'source_revision',hashed(same_note.id,row.id),'Possible numerical discrepancy'if pairs else'Saved note wording revised',{**common,'before':before[:1000],'numeric_pairs':pairs,'reason':'A reviewed note snapshot differs from an earlier reviewed snapshot of that note. A source revision, extraction difference or conflicting claim requires review; no contradiction is verified.'})
    else:
        hits=[word for word in policy['impact_terms']if re.search(r'(?<!\w)'+re.escape(word)+r'(?!\w)',data['quote'],re.I)]
        if hits:notice(db,dossier.id,'reported_impact',hashed(normalized(data['quote'])),'Reported impact cue',{**common,'matched_terms':hits,'reason':'The copied narrative matches a configured impact cue. This is attributed reporting requiring review, not proof of a new impact or an increased risk level.'})
        else:notice(db,dossier.id,'additional_reporting',hashed(normalized(data['quote'])),'Additional grouped reporting',{**common,'reason':'An analyst grouped this note with the dossier. New grouping is not a new world event or independent corroboration.'})

def effective_interval(data,current=None):
    stamp=(current or now()).timestamp();p=data['policy']
    if data.get('accelerated_until',0)>stamp:return p['accelerated_minutes'],'accelerated'
    if stamp-data.get('last_escalation',data['created_at'])>=p['quiet_after_hours']*3600:return p['quiet_minutes'],'quiet'
    return p['normal_minutes'],'normal'
def day_start():
    return now().astimezone(ZoneInfo('America/Chicago')).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
def budget(db,dossier,data):
    started=EventJob.started>=day_start()
    total=db.scalar(select(func.count()).select_from(EventJob).where(started))or 0
    event=db.scalar(select(func.count()).select_from(EventJob).where(started,EventJob.dossier_id==dossier.id))or 0
    return total<12 and event<data['policy']['daily_limit']

def enqueue(db,dossier,identity,manual=False):
    key=str(identity);existing=db.get(EventJob,key)
    if existing:
        if existing.dossier_id!=dossier.id:raise HTTPException(409,'Request ID belongs to a different dossier.')
        return existing
    if dossier.state!='active':raise HTTPException(409,'This dossier is archived.')
    if (db.scalar(select(func.count()).select_from(EventEvidence).where(EventEvidence.dossier_id==dossier.id))or 0)>=200:raise HTTPException(409,'The dossier evidence limit is reached. Archive it and create a continuation dossier before more research.')
    active=db.scalars(select(EventJob).where(EventJob.dossier_id==dossier.id,EventJob.state.in_(['queued','running']))).first()
    if active:return active
    if (db.scalar(select(func.count()).select_from(EventJob).where(EventJob.state.in_(['queued','running'])))or 0)>=20:raise HTTPException(409,'The event research queue is full.')
    row=EventJob(id=key,dossier_id=dossier.id,state='queued',created=now().timestamp(),started=None,payload=pack({'manual':manual,'error':None,'ingest_attempts':0,'ingest_due':0,'ingest_status':'pending'}));db.add(row);return row

class Policy(BaseModel):
    enabled:bool=False
    normal_minutes:int=Field(default=360,ge=60,le=10080)
    accelerated_minutes:int=Field(default=60,ge=60,le=10080)
    quiet_minutes:int=Field(default=1440,ge=60,le=10080)
    quiet_after_hours:int=Field(default=72,ge=12,le=720)
    acceleration_hours:int=Field(default=24,ge=1,le=168)
    daily_limit:int=Field(default=3,ge=1,le=12)
    impact_terms:list[str]=Field(default_factory=lambda:['evacuation','disrupted','closure','outage','displaced','casualties'],max_length=12)
    @model_validator(mode='after')
    def order(self):
        if not self.accelerated_minutes<=self.normal_minutes<=self.quiet_minutes:raise ValueError('Use accelerated interval <= normal interval <= quiet interval.')
        self.impact_terms=list(dict.fromkeys(w.strip().casefold()for w in self.impact_terms if 2<=len(w.strip())<=40))
        return self

def same_origin(request:Request):
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(request.headers.get('origin')and request.headers['origin']!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use this Artemis origin for event changes.')
router=APIRouter(prefix='/event-tracking',dependencies=[Depends(same_origin)])
class Create(BaseModel):
    title:str=Field(min_length=3,max_length=150)
    scope:str=Field(min_length=5,max_length=280)
    grouping_reason:str=Field(min_length=5,max_length=1000)
@router.post('')
def create(body:Create):
    with LOCK,SessionLocal()as db:
        if (db.scalar(select(func.count()).select_from(EventDossier).where(EventDossier.state=='active'))or 0)>=50:raise HTTPException(409,'Fifty active dossiers are already present. Archive one before adding another.')
        key=str(uuid4());req_id=str(uuid5(NAMESPACE_URL,'artemis-event:'+key));stamp=now().timestamp()
        db.add(ResearchRequisition(id=req_id,topic=body.scope,questions_json=json.dumps(['Identify current reporting specifically about this event, distinguishing event dates from publication and collection times. Preserve attribution and unknowns; save supported event notes.'])))
        row=EventDossier(id=key,state='active',payload=pack({'title':body.title,'scope':body.scope,'grouping_reason':body.grouping_reason,'created_at':stamp,'requisition_id':req_id,'policy':Policy().model_dump(),'next_at':None,'accelerated_until':0,'last_escalation':stamp,'case_link':None,'board_id':None}));db.add(row);db.commit();return {'id':key}
@router.get('')
def listing():
    with SessionLocal()as db:return [{'id':r.id,'state':r.state,**unpack(r),'monitoring':effective_interval(unpack(r))[1]}for r in db.scalars(select(EventDossier).limit(100)).all()]
@router.get('/{dossier_id}')
def details(dossier_id:UUID):
    with SessionLocal()as db:
        row=get(db,dossier_id);data=unpack(row)
        items=db.scalars(select(EventEvidence).where(EventEvidence.dossier_id==row.id).order_by(EventEvidence.created).limit(200)).all();notices=db.scalars(select(EventNotice).where(EventNotice.dossier_id==row.id).order_by(EventNotice.created.desc()).limit(100)).all();jobs=db.scalars(select(EventJob).where(EventJob.dossier_id==row.id).order_by(EventJob.created.desc()).limit(30)).all()
        return {'id':row.id,'state':row.state,'data':data,'interval':effective_interval(data),'evidence':[{'id':i.id,'state':i.state,'created':i.created,**{k:v for k,v in unpack(i).items()if k!='record'},'record':{k:v for k,v in unpack(i)['record'].items()if k!='body'}}for i in items],'notices':[{'id':n.id,'state':n.state,'created':n.created,'reviewed_at':n.reviewed_at,**unpack(n)}for n in notices],'jobs':[{'id':j.id,'state':j.state,'created':j.created,'started':j.started,**unpack(j)}for j in jobs]}
@router.get('/{dossier_id}/evidence/{item_id}')
def evidence_snapshot(dossier_id:UUID,item_id:str):
    with SessionLocal()as db:
        get(db,dossier_id);row=db.get(EventEvidence,item_id)
        if row is None or row.dossier_id!=str(dossier_id):raise HTTPException(404,'Evidence snapshot not found.')
        return unpack(row)['record']
class Import(BaseModel):note_id:int=Field(gt=0,le=2147483647);quote:str=Field(min_length=1,max_length=1000)
@router.post('/{dossier_id}/notes')
def import_note(dossier_id:UUID,body:Import):
    from app.config import get_settings
    from app.connectors.intel_notes import IntelNotesConnector
    with IntelNotesConnector(base_url=str(get_settings().intel_notes_url))as client:note=client.get_note(body.note_id)
    if note.get('id')!=body.note_id:raise HTTPException(422,'Note ID mismatch.')
    with LOCK,SessionLocal()as db:row=ingest(db,get(db,dossier_id),note,body.quote);db.commit();return {'id':row.id,'state':row.state}
class Review(BaseModel):state:Literal['reviewed','rejected'];reason:str=Field(min_length=5,max_length=1000)
@router.put('/{dossier_id}/evidence/{item_id}')
def review_evidence(dossier_id:UUID,item_id:str,body:Review):
    with LOCK,SessionLocal()as db:
        dossier=get(db,dossier_id);row=db.get(EventEvidence,item_id)
        if not row or row.dossier_id!=dossier.id:raise HTTPException(404,'Evidence not found.')
        if row.state!='pending'and not(row.state=='reviewed'and body.state=='rejected'):raise HTTPException(409,'This snapshot already has a decision.')
        old_state=row.state;row.state=body.state;data=unpack(row);data.setdefault('review_history',[]).append({'previous_state':old_state,'state':body.state,'reason':body.reason,'at':now().isoformat()});data.update(grouping_reason=body.reason,reviewed_at=now().isoformat());row.payload=pack(data)
        if body.state=='reviewed':evaluate(db,dossier,row)
        else:
            for n in db.scalars(select(EventNotice).where(EventNotice.dossier_id==dossier.id)).all():
                value=unpack(n)
                if value.get('evidence_id')==row.id:
                    value['withdrawn_reason']=body.reason;n.payload=pack(value);n.state='withdrawn'
            data=unpack(dossier);ends=[]
            for n in db.scalars(select(EventNotice).where(EventNotice.dossier_id==dossier.id)).all():
                value=unpack(n)
                if n.state=='reviewed'and value.get('escalates')and n.reviewed_at:ends.append(n.reviewed_at+data['policy']['acceleration_hours']*3600)
            data['accelerated_until']=max(ends,default=0);dossier.payload=pack(data)
        db.commit();return {'state':row.state}
class NoticeReview(Review):escalate:bool=False
@router.put('/{dossier_id}/notices/{notice_id}')
def review_notice(dossier_id:UUID,notice_id:str,body:NoticeReview):
    with LOCK,SessionLocal()as db:
        dossier=get(db,dossier_id);row=db.get(EventNotice,notice_id)
        if not row or row.dossier_id!=dossier.id:raise HTTPException(404,'Notice not found.')
        if row.state!='proposed':raise HTTPException(409,'This notice already has a review decision.')
        value=unpack(row);item=db.get(EventEvidence,value['evidence_id'])
        if body.state=='reviewed'and(not item or item.state!='reviewed'):raise HTTPException(409,'Confirm the evidence grouping before reviewing this as a dossier change.')
        if body.escalate and(body.state!='reviewed'or value['kind']not in {'reported_impact','numeric_review','source_revision'}):raise HTTPException(422,'Only a reviewed impact or wording discrepancy can accelerate monitoring.')
        row.state=body.state;row.reviewed_at=now().timestamp();value.update(review_reason=body.reason,escalates=body.escalate);row.payload=pack(value)
        if body.escalate:
            data=unpack(dossier);stamp=now().timestamp();data.update(last_escalation=stamp,accelerated_until=stamp+data['policy']['acceleration_hours']*3600)
            if data['policy']['enabled']:data['next_at']=min(data.get('next_at')or stamp+3600,stamp+data['policy']['accelerated_minutes']*60)
            dossier.payload=pack(data)
        db.commit();return {'state':row.state}
@router.put('/{dossier_id}/policy')
def policy(dossier_id:UUID,body:Policy):
    with LOCK,SessionLocal()as db:
        row=get(db,dossier_id);data=unpack(row)
        if row.state!='active'and body.enabled:raise HTTPException(409,'An archived dossier cannot be monitored.')
        data['policy']=body.model_dump();interval,_=effective_interval(data);data['next_at']=now().timestamp()+interval*60 if body.enabled else None;row.payload=pack(data)
        if not body.enabled:
            for job in db.scalars(select(EventJob).where(EventJob.dossier_id==row.id,EventJob.state=='queued')).all():
                if not unpack(job)['manual']:job.state='cancelled'
        db.commit();return data['policy']
class Run(BaseModel):request_id:UUID=Field(default_factory=uuid4)
@router.post('/{dossier_id}/run')
def run(dossier_id:UUID,body:Run):
    with LOCK,SessionLocal()as db:row=enqueue(db,get(db,dossier_id),body.request_id,True);db.commit();return {'id':row.id,'state':row.state}
@router.post('/{dossier_id}/archive')
def archive(dossier_id:UUID):
    with LOCK,SessionLocal()as db:
        row=get(db,dossier_id);row.state='archived';data=unpack(row);data['policy']['enabled']=False;data['next_at']=None;row.payload=pack(data)
        for job in db.scalars(select(EventJob).where(EventJob.dossier_id==row.id,EventJob.state=='queued')).all():job.state='cancelled'
        db.commit();return {'state':'archived'}

class Links(BaseModel):
    case_id:int|None=Field(default=None,gt=0)
    board_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
@router.put('/{dossier_id}/links')
def links(dossier_id:UUID,body:Links):
    case=None
    if body.case_id:
        from app.services.risk_map import tracker
        with tracker(False)as client:record=client.get_case(body.case_id)
        if record.get('id')!=body.case_id:raise HTTPException(422,'Risk Tracker case ID mismatch.')
        case={k:record.get(k)for k in ('id','title','case_number','status')};case['linked_at']=now().isoformat()
    with LOCK,SessionLocal()as db:
        row=get(db,dossier_id)
        if body.board_id:
            from app.services.intelligence_workspace import read_board
            try:read_board(db,body.board_id)
            except LookupError:raise HTTPException(404,'Board not found.')from None
        data=unpack(row);data.update(case_link=case,board_id=body.board_id);row.payload=pack(data);db.commit();return {'saved':True}

def schedule_due():
    with LOCK,SessionLocal()as db:
        rows=db.scalars(select(EventDossier).where(EventDossier.state=='active').limit(50)).all()
        for row in rows:
            data=unpack(row)
            if not data['policy']['enabled']or not data.get('next_at')or data['next_at']>now().timestamp():continue
            if not budget(db,row,data):continue
            if (db.scalar(select(func.count()).select_from(EventEvidence).where(EventEvidence.dossier_id==row.id))or 0)>=200:
                data['policy']['enabled']=False;data['next_at']=None;data['intake_warning']='Evidence snapshot limit reached; automatic monitoring paused. Create a continuation dossier to collect more evidence.';row.payload=pack(data);continue
            try:enqueue(db,row,uuid5(NAMESPACE_URL,'artemis-event-slot:'+row.id+':'+str(data['next_at'])))
            except HTTPException as error:
                if error.status_code==409:continue
                raise
            interval,_=effective_interval(data);data['next_at']=now().timestamp()+interval*60;row.payload=pack(data)
        db.commit()

def reconcile():
    with LOCK,SessionLocal()as db:
        for job in db.scalars(select(EventJob).where(EventJob.state=='running')).all():
            run=db.get(ResearchRun,job.id);data=unpack(job)
            if run is None:job.state='queued';job.started=None;data['error']='Interrupted before a saved research attempt existed; the same request ID is queued.'
            elif run.status=='completed':job.state='completed';data['error']=None
            else:job.state='blocked_review';data['error']='The saved research run may contain partial external writes. Review it before initiating new research.'
            job.payload=pack(data)
        db.commit()

def ingest_completed():
    from app.config import get_settings
    from app.connectors.intel_notes import IntelNotesConnector
    from app.services.research_workspace_bridge import confirmed_note_ids
    with SessionLocal()as db:
        jobs=db.scalars(select(EventJob).where(EventJob.state=='completed').order_by(EventJob.created.desc()).limit(100)).all()
        job=next((j for j in jobs if unpack(j).get('ingest_status')=='pending'and unpack(j).get('ingest_due',0)<=now().timestamp()),None)
        if not job:return
        key=job.id;run=db.get(ResearchRun,key)
        if not run or not run.result_json:return
        ids=confirmed_note_ids(json.loads(run.result_json));dossier_id=job.dossier_id
    failures=[]
    try:
        with IntelNotesConnector(base_url=str(get_settings().intel_notes_url))as client:
            for nid in ids:
                try:
                    note=client.get_note(nid)
                    if note.get('id')!=nid:raise ValueError('Note ID mismatch')
                    spans=narrative(note.get('body')or'');quote=next((s[:1000]for s in spans if s.strip()),'')
                    with LOCK,SessionLocal()as db:ingest(db,get(db,dossier_id),note,quote,key);db.commit()
                except Exception:failures.append('Intel Note '+str(nid)+' could not be imported. Retry its read from the saved note if needed.')
    except Exception:failures.append('Intel Notes connection unavailable during dossier intake.')
    with LOCK,SessionLocal()as db:
        job=db.get(EventJob,key);data=unpack(job);data['ingest_attempts']+=1;data['intake_errors']=failures
        data['ingest_status']='pending'if failures and data['ingest_attempts']<3 else'partial'if failures else'completed'
        data['ingest_due']=now().timestamp()+[30,120,600][min(data['ingest_attempts']-1,2)];job.payload=pack(data);db.commit()

class Worker:
    def __init__(self):self.event=Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        reconcile();self.event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-event-tracking');self.thread.start()
    def stop(self):
        self.event.set()
        if self.thread:self.thread.join()
    def tick(self):
        from app.services.operations_recovery import ready
        from app.routes.research import run_requisition,RunRequest,research_lock
        schedule_due()
        if ready(['notes'],'event dossier intake'):ingest_completed()
        if self.event.is_set()or not ready(['ollama','search','notes'],'event monitoring')or research_lock.locked()or research_lock.pending():return
        with LOCK,SessionLocal()as db:
            jobs=db.scalars(select(EventJob).where(EventJob.state=='queued').order_by(EventJob.created).limit(20)).all()
            from app.services.collection_planning import rank
            ordered=rank('events',[j.dossier_id for j in jobs]);positions={key:i for i,key in enumerate(ordered)}
            jobs.sort(key=lambda j:positions[j.dossier_id])
            job=None
            for candidate in jobs:
                dossier=get(db,candidate.dossier_id);data=unpack(dossier)
                if dossier.state!='active':candidate.state='cancelled';continue
                if not unpack(candidate)['manual']and not data['policy']['enabled']:candidate.state='cancelled';continue
                if budget(db,dossier,data):job=candidate;break
            if job is None:db.commit();return
            old=db.get(ResearchRun,job.id)
            if old:
                job.state='completed'if old.status=='completed'else'blocked_review';value=unpack(job);value['error']=None if old.status=='completed'else'Review the existing run; it may contain partial writes.';job.payload=pack(value);db.commit();return
            job.state='running';job.started=now().timestamp();key=job.id;req_id=data['requisition_id'];dossier_target=job.dossier_id;db.commit()
        try:
            with research_lock.background(lane='events',target=dossier_target):result=run_requisition(req_id,RunRequest(request_id=UUID(key)))
            with LOCK,SessionLocal()as db:
                job=db.get(EventJob,key);job.state='completed'if result.get('status')=='completed'else'blocked_review';value=unpack(job);value.update(error=result.get('error'),finished_at=now().isoformat());job.payload=pack(value);db.commit()
        except Exception as error:
            with LOCK,SessionLocal()as db:
                job=db.get(EventJob,key);saved=db.get(ResearchRun,key);value=unpack(job)
                if isinstance(error,HTTPException)and error.status_code==409 and saved is None:job.state='queued';job.started=None;value['error']='Worker deferred without starting a research run.'
                elif saved and saved.status=='completed':job.state='completed';value['error']=None
                else:job.state='blocked_review';value['error']='Research interrupted or unavailable. Review the saved run and possible partial writes.'
                job.payload=pack(value);db.commit()
    def loop(self):
        while not self.event.is_set():
            try:self.tick()
            except Exception:LOG.exception('Event monitoring worker failed')
            self.event.wait(10)
worker=Worker()

def brief_updates(start,end):
    """Reviewed notices only, bounded to the brief's selected collection window."""
    with SessionLocal()as db:
        notices=db.scalars(select(EventNotice).where(EventNotice.state=='reviewed',EventNotice.reviewed_at>start.timestamp(),EventNotice.reviewed_at<=end.timestamp()).order_by(EventNotice.reviewed_at).limit(100)).all()
        updates=[]
        for row in notices:
            data=unpack(row)
            if data['kind']=='candidate_note':continue
            item=db.get(EventEvidence,data['evidence_id']);dossier=db.get(EventDossier,row.dossier_id)
            if not item or item.state!='reviewed'or not dossier:continue
            updates.append({'event':unpack(dossier)['title'],'kind':data['kind'],'title':data['title'],'note_id':data['note_id'],'quote':data['quote'],'before':data.get('before'),'reason':data['reason'],'classification':data.get('classification','Not recorded'),'reviewed_at':datetime.fromtimestamp(row.reviewed_at,timezone.utc).isoformat(),'when_literal':data.get('when_literal'),'source_urls':data.get('source_urls',[])})
        return updates

def previous_brief_end(folder,end,identity=None):
    candidates=[]
    for path in Path(folder).glob('*.json'):
        try:
            data=json.loads(path.read_text(encoding='utf-8'));key=str(UUID(data['id']));stamp=datetime.fromisoformat(data['end'])
            if key!=identity and 'notes'in data.get('sources',[])and stamp<end and(Path(folder)/(key+'.pdf')).is_file():candidates.append(stamp)
        except (OSError,ValueError,TypeError,KeyError):continue
    return max(candidates)if candidates else None

def focus(dossier_id):
    from app.services.voice_hub import STATE,atomic
    with SessionLocal()as db:row=get(db,dossier_id);data=unpack(row)
    atomic(STATE/'active-event.json',{'id':str(dossier_id)});return {'speech':'Selected event dossier '+data['title']+'.','link':'/static/events.html#'+str(dossier_id)}
@router.post('/{dossier_id}/focus')
def select_voice_focus(dossier_id:UUID):return focus(str(dossier_id))

def voice(text):
    from app.services.voice_hub import STATE,read_json,normalize
    clean=normalize(text);match=re.match(r'^open event (.+)$',clean)
    if match:
        with SessionLocal()as db:matches=[r.id for r in db.scalars(select(EventDossier).where(EventDossier.state=='active').limit(50)).all()if normalize(unpack(r)['title'])==match[1]]
        if len(matches)!=1:return {'speech':'Choose one event dossier on Event tracking, then use Select for voice. Matching names alone do not identify an event.','link':'/static/events.html'}
        return focus(matches[0])
    key=read_json(STATE/'active-event.json',{}).get('id')
    if not key:return {'speech':'Select an event dossier first on Event tracking, or say open event followed by its exact title.','link':'/static/events.html'}
    with LOCK,SessionLocal()as db:
        row=get(db,key);data=unpack(row)
        if row.state!='active':return {'speech':'That dossier is archived. Select an active event dossier.','link':'/static/events.html'}
        if clean=='keep monitoring this':
            data['policy']['enabled']=True;interval,mode=effective_interval(data);data['next_at']=now().timestamp()+interval*60;row.payload=pack(data);db.commit()
            return {'speech':'Monitoring enabled for '+data['title']+'. The current interval is '+str(interval)+' minutes, within the saved daily research budget.','link':'/static/events.html#'+key}
        if clean=='pause monitoring this':
            data['policy']['enabled']=False;data['next_at']=None;row.payload=pack(data)
            for job in db.scalars(select(EventJob).where(EventJob.dossier_id==key,EventJob.state=='queued')).all():
                if not unpack(job)['manual']:job.state='cancelled'
            db.commit()
            return {'speech':'Monitoring paused for '+data['title']+'. An active run may finish.','link':'/static/events.html#'+key}
        notices=db.scalars(select(EventNotice).where(EventNotice.dossier_id==key,EventNotice.state=='reviewed').order_by(EventNotice.reviewed_at.desc()).limit(5)).all()
        passages=[unpack(n)for n in notices if unpack(n)['kind']!='candidate_note']
        speech='Reviewed dossier notices for '+data['title']+'. '+(' '.join(p['title']+'. Intel Note '+str(p['note_id'])+': '+p['quote'][:240]for p in passages)if passages else'No reviewed change notices are available. Pending notes still need event-grouping review.')
        return {'speech':speech+' These are source-reported review cues, not independently verified changes.','link':'/static/events.html#'+key}
