"""Evidence comparisons and bounded, non-recursive follow-up research."""
import hashlib,json,re,logging
from datetime import datetime,timedelta,timezone
from difflib import SequenceMatcher
from threading import Event,Thread,RLock
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from sqlalchemy import String,Text,Float,Boolean,select,func
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.research_models import ResearchRun,ResearchRequisition
from app.services.encryption import protect_text,unprotect_text

LOG=logging.getLogger(__name__);LOCK=RLock()
class ChangeSettings(Base):
    __tablename__='change_followup_settings'
    id:Mapped[str]=mapped_column(String(20),primary_key=True)
    payload:Mapped[str]=mapped_column(Text)
class ChangeCursor(Base):
    __tablename__='change_followup_cursors'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    run_id:Mapped[str]=mapped_column(String(36))
class ChangeNotice(Base):
    __tablename__='change_followup_notices'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    requisition_id:Mapped[str]=mapped_column(String(36),index=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    reviewed:Mapped[bool]=mapped_column(Boolean,default=False)
    payload:Mapped[str]=mapped_column(Text)
class FollowupJob(Base):
    __tablename__='change_followup_jobs'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    requisition_id:Mapped[str]=mapped_column(String(36),index=True)
    run_id:Mapped[str]=mapped_column(String(36),unique=True)
    status:Mapped[str]=mapped_column(String(24),index=True)
    automatic:Mapped[bool]=mapped_column(Boolean)
    due:Mapped[float]=mapped_column(Float,index=True)
    started:Mapped[float|None]=mapped_column(Float,nullable=True)
    payload:Mapped[str]=mapped_column(Text)

def now():return datetime.now(timezone.utc)
def defaults():return dict(enabled=False,automatic=False,watched_ids=[],daily_limit=3,cooldown_hours=12)
def config(db):
    row=db.get(ChangeSettings,'default');return {**defaults(),**(json.loads(row.payload)if row else{})}
def packed(value):return protect_text(json.dumps(value,ensure_ascii=False))
def unpack(value):return json.loads(unprotect_text(value))
def normalize(text):return re.sub(r'\s+',' ',str(text or'')).strip()
def narrative(text):
    lines=[line for line in str(text or'').splitlines()if not line.startswith(('Source URL:','Publisher date metadata','Artemis source record:'))]
    return normalize('\n'.join(lines))
def digest(*values):return hashlib.sha256('\n'.join(values).encode()).hexdigest()
def sources(result):return {s['url'].strip():s for s in result.get('sources',[])if isinstance(s,dict)and isinstance(s.get('url'),str)and s['url'].startswith('https://')}
def notes(result):return {str(n['intel_note_id']):n for n in result.get('event_notes',[])if isinstance(n,dict)and type(n.get('intel_note_id'))is int and n['intel_note_id']>0 and not n.get('error')}
def needs(result):
    raw=(result.get('gap_collection')or{}).get('information_needed',[])
    return sorted({normalize(x)[:500]for x in raw if isinstance(x,str)and normalize(x)})[:10]if isinstance(raw,list)else[]
def number_pairs(before,after):
    # A review cue only: similar source sentences with different literal numeric strings.
    pairs=[]
    a=re.split(r'(?<=[.!?])\s+',before);b=re.split(r'(?<=[.!?])\s+',after)
    for x in a[:30]:
        if len(x)>600 or not re.search(r'\d',x):continue
        for y in b[:30]:
            if len(y)>600 or not re.search(r'\d',y):continue
            nx=re.findall(r'\d[\d,.]*',x);ny=re.findall(r'\d[\d,.]*',y)
            if nx==ny:continue
            ax=re.sub(r'\d[\d,.]*','#',x).casefold();by=re.sub(r'\d[\d,.]*','#',y).casefold()
            if SequenceMatcher(None,ax,by).ratio()>=.88 and len(set(ax.split())&set(by.split()))>=6:
                pairs.append({'before':x,'after':y});break
        if len(pairs)>=3:break
    return pairs

def compare(before,after,texts):
    """Never treat changed IDs, retrieval times or missing URLs as world-event changes."""
    result=[];old=sources(before);new=sources(after)
    for url,s in new.items():
        if url not in old:
            result.append(dict(kind='new_source',key=url,title='Additional source collected',priority='information',reason='This URL was absent from the comparison run; it may describe an older or already known event.',url=url,source_title=s.get('title','Source'),record_ids=[s.get('record_id')],eligible=False));continue
        previous=narrative(texts.get(str(old[url].get('record_id')),''));current=narrative(texts.get(str(s.get('record_id')),''))
        if previous and current and previous!=current:
            pairs=number_pairs(previous,current)
            result.append(dict(kind='numeric_review'if pairs else'source_text_changed',key=digest(url,previous,current),title='Possible numerical discrepancy'if pairs else'Saved source text changed',priority='review',reason='Collected wording differs at the same URL. This can reflect extraction differences, page edits or reporting changes; no factual contradiction or event change is established.',url=url,source_title=s.get('title','Source'),record_ids=[old[url].get('record_id'),s.get('record_id')],before_excerpt=previous[:700],after_excerpt=current[:700],numeric_pairs=pairs,eligible=True))
    previous_notes=notes(before)
    for key,note in notes(after).items():
        if key not in previous_notes:result.append(dict(kind='new_note',key=key,title='Newly saved note in comparison',priority='information',reason='A confirmed saved-note ID appears in the target run. This is not proof of a newly occurring event or independent corroboration.',note_id=int(key),note_title=note.get('title','Intel Note '+key),eligible=False))
    for gap in needs(after):result.append(dict(kind='evidence_gap',key=gap.casefold(),title='Reported evidence gap',priority='review',reason='The saved research result identifies an unresolved collection question. Its importance and factual premise require review.',gap=gap,eligible=True))
    return result[:35]

def queue(db,notice,automatic=False):
    row=db.get(FollowupJob,notice.id)
    if row:
        if row.status=='cancelled'and db.get(ResearchRun,row.run_id)is None:
            row.status='queued';row.automatic=automatic;row.due=now().timestamp()
        return row
    if (db.scalar(select(func.count()).select_from(FollowupJob).where(FollowupJob.status.in_(['queued','deferred','running'])))or 0)>=20:raise ValueError('The follow-up queue has reached its 20-job bound. Finish or cancel pending work first.')
    content=unpack(notice.payload)
    if not content.get('eligible'):raise ValueError('This notice is informational; use a direct research requisition for a new investigation.')
    question=('Investigate the following reported evidence gap, preserving source attribution and unknowns: '+content['gap'])if content['kind']=='evidence_gap'else('Check current reporting at '+content['url'][:170]+'. Earlier: '+content.get('before_excerpt','')[:90]+' Later: '+content.get('after_excerpt','')[:90]+'. Assess whether this reflects a reporting revision or extraction difference; preserve uncertainty.')
    row=FollowupJob(id=notice.id,requisition_id=notice.requisition_id,run_id=str(uuid5(NAMESPACE_URL,'artemis-followup:'+notice.id)),status='queued',automatic=automatic,due=now().timestamp(),started=None,payload=packed(dict(question=question[:500],error=None,notice_id=notice.id,created_at=now().isoformat())))
    db.add(row);return row

def scan():
    from app.models import InformationRecord
    with LOCK,SessionLocal()as db:
        settings=config(db)
        if not settings['enabled']:return
        for req_id in settings['watched_ids']:
            req=db.get(ResearchRequisition,req_id)
            if not req:continue
            # Own follow-ups are excluded to stop recursive research chains.
            latest=db.scalars(select(ResearchRun).where(ResearchRun.requisition_id==req_id,ResearchRun.status=='completed',ResearchRun.id.not_in(select(FollowupJob.run_id))).order_by(ResearchRun.finished_at.desc(),ResearchRun.id.desc()).limit(1)).first()
            if latest is None:continue
            cursor=db.get(ChangeCursor,req_id)
            if cursor is None:db.add(ChangeCursor(id=req_id,run_id=latest.id));db.commit();continue
            if cursor.run_id==latest.id:continue
            previous=db.get(ResearchRun,cursor.run_id)
            if previous is None:cursor.run_id=latest.id;db.commit();continue
            try:
                before=json.loads(previous.result_json or'{}');after=json.loads(latest.result_json or'{}')
                record_ids={str(s.get('record_id'))for value in (before,after)for s in sources(value).values()}
                texts={r.id:r.original_content for r in db.scalars(select(InformationRecord).where(InformationRecord.id.in_(record_ids))).all()}
                signals=compare(before,after,texts)
                for signal in signals:
                    identity=digest(req_id,signal['kind'],signal['key'])
                    if db.get(ChangeNotice,identity):continue
                    signal.update(topic=req.topic,baseline_run=previous.id,target_run=latest.id,detected_at=now().isoformat())
                    row=ChangeNotice(id=identity,requisition_id=req_id,created=now().timestamp(),reviewed=False,payload=packed(signal));db.add(row);db.flush()
                    if settings['automatic']and signal['eligible']:
                        try:queue(db,row,True)
                        except ValueError:pass
                cursor.run_id=latest.id;db.commit()
            except Exception:db.rollback();LOG.exception('Change comparison failed for %s',req_id)

def reconcile():
    """An interrupted external-write workflow is reviewed, never blindly replayed."""
    with SessionLocal()as db:
        for row in db.scalars(select(FollowupJob).where(FollowupJob.status=='running')).all():
            run=db.get(ResearchRun,row.run_id);payload=unpack(row.payload)
            if run and run.status=='completed':row.status='completed';payload['error']=None
            else:row.status='blocked_review';payload['error']='The previous attempt was interrupted. Review its saved run and any partial writes before starting another investigation.'
            row.payload=packed(payload)
        db.commit()

def execute_one():
    from app.routes.research import RunRequest,run_requisition,research_lock
    from app.services.operations_recovery import ready
    if research_lock.locked()or not ready(['ollama','search','notes'],'follow-up research'):return
    with LOCK,SessionLocal()as db:
        settings=config(db)
        for expired in db.scalars(select(FollowupJob).where(FollowupJob.automatic.is_(True),FollowupJob.status.in_(['queued','deferred']))).all():
            if datetime.fromisoformat(unpack(expired.payload)['created_at'])<now()-timedelta(days=7):expired.status='cancelled'
        db.commit()
        rows=db.scalars(select(FollowupJob).where(FollowupJob.status.in_(['queued','deferred']),FollowupJob.due<=now().timestamp()).order_by(FollowupJob.due).limit(20)).all()
        row=next((r for r in rows if not r.automatic or(settings['enabled']and settings['automatic']and r.requisition_id in settings['watched_ids'])),None)
        if row is None:return
        today=now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        total=db.scalar(select(func.count()).select_from(FollowupJob).where(FollowupJob.started>=today))or 0
        last=db.scalar(select(func.max(FollowupJob.started)).where(FollowupJob.requisition_id==row.requisition_id))
        if total>=settings['daily_limit']or(last and last+settings['cooldown_hours']*3600>now().timestamp()):
            row.status='deferred';row.due=max(today+86400 if total>=settings['daily_limit']else 0,(last or 0)+settings['cooldown_hours']*3600);db.commit();return
        # Recover a recorded request without issuing another external workflow.
        existing=db.get(ResearchRun,row.run_id)
        if existing:
            row.status='completed'if existing.status=='completed'else'blocked_review';payload=unpack(row.payload);payload['error']=None if existing.status=='completed'else'Review the existing saved run; it may contain partial writes.';row.payload=packed(payload);db.commit();return
        row.status='running';row.started=now().timestamp();db.commit();key=row.id;req_id=row.requisition_id;run_id=row.run_id;question=unpack(row.payload)['question'];automatic=row.automatic
    try:
        with research_lock.background(lane='world'if automatic else None,target=req_id):result=run_requisition(req_id,RunRequest(request_id=UUID(run_id),followup_questions=[question]))
        with SessionLocal()as db:
            row=db.get(FollowupJob,key);payload=unpack(row.payload);row.status='completed'if result.get('status')=='completed'else'blocked_review';payload['error']=result.get('error');payload['finished_at']=now().isoformat();row.payload=packed(payload);db.commit()
    except HTTPException as error:
        with SessionLocal()as db:
            row=db.get(FollowupJob,key);payload=unpack(row.payload)
            if error.status_code==409 and db.get(ResearchRun,run_id)is None:row.status='deferred';row.due=now().timestamp()+60;row.started=None;payload['error']='Another research run is active; deferred without spending the daily budget.'
            else:row.status='blocked_review';payload['error']='Research request could not complete. Review any saved run before retrying.'
            row.payload=packed(payload);db.commit()
    except Exception:
        LOG.exception('Follow-up request failed')
        with SessionLocal()as db:
            row=db.get(FollowupJob,key);row.status='blocked_review';payload=unpack(row.payload);payload['error']='Request interrupted or unavailable. Review the saved run before starting another investigation.';row.payload=packed(payload);db.commit()

class Worker:
    def __init__(self):self.stop_event=Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        reconcile();self.stop_event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-change-followup');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join()
    def loop(self):
        while not self.stop_event.is_set():
            try:
                scan()
                if not self.stop_event.is_set():execute_one()
            except Exception:LOG.exception('Change/follow-up worker failed')
            self.stop_event.wait(30)
worker=Worker()

def same_origin(request:Request):
    origin=request.headers.get('origin')
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(origin and origin!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use this Artemis origin for change-monitoring actions.')
router=APIRouter(prefix='/changes',dependencies=[Depends(same_origin)])
class Preferences(BaseModel):
    enabled:bool=False
    automatic:bool=False
    watched_ids:list[UUID]=Field(default_factory=list,max_length=100)
    daily_limit:int=Field(default=3,ge=1,le=10)
    cooldown_hours:int=Field(default=12,ge=1,le=168)
@router.put('/settings')
def settings(body:Preferences):
    if body.enabled and not body.watched_ids:raise HTTPException(422,'Choose at least one research requisition.')
    if body.automatic and not body.enabled:raise HTTPException(422,'Enable change monitoring before automatic follow-ups.')
    value=body.model_dump();value['watched_ids']=list(dict.fromkeys(str(x)for x in body.watched_ids))
    with LOCK,SessionLocal()as db:
        if any(db.get(ResearchRequisition,key)is None for key in value['watched_ids']):raise HTTPException(404,'A selected requisition no longer exists.')
        row=db.get(ChangeSettings,'default')
        if row:row.payload=json.dumps(value)
        else:db.add(ChangeSettings(id='default',payload=json.dumps(value)))
        for job in db.scalars(select(FollowupJob).where(FollowupJob.automatic.is_(True),FollowupJob.status.in_(['queued','deferred']))).all():
            if not value['enabled']or not value['automatic']or job.requisition_id not in value['watched_ids']:job.status='cancelled'
        db.commit();return value
@router.get('/status')
def status():
    with SessionLocal()as db:
        notices=[dict(id=r.id,reviewed=r.reviewed,requisition_id=r.requisition_id,**unpack(r.payload))for r in db.scalars(select(ChangeNotice).order_by(ChangeNotice.created.desc()).limit(100)).all()]
        jobs=[dict(id=r.id,status=r.status,run_id=r.run_id,requisition_id=r.requisition_id,automatic=r.automatic,next_attempt=datetime.fromtimestamp(r.due,timezone.utc).isoformat(),**unpack(r.payload))for r in db.scalars(select(FollowupJob).order_by(FollowupJob.due.desc()).limit(100)).all()]
        cursors={r.id:r.run_id for r in db.scalars(select(ChangeCursor)).all()}
        return dict(settings=config(db),notices=notices,jobs=jobs,baselines=cursors,worker_alive=bool(worker.thread and worker.thread.is_alive()))
@router.post('/notices/{notice_id}/review')
def review(notice_id:str):
    with LOCK,SessionLocal()as db:
        row=db.get(ChangeNotice,notice_id)
        if row is None:raise HTTPException(404,'Notice not found.')
        row.reviewed=True;db.commit();return {'reviewed':True}
@router.post('/notices/{notice_id}/followup')
def followup(notice_id:str):
    with LOCK,SessionLocal()as db:
        row=db.get(ChangeNotice,notice_id)
        if row is None:raise HTTPException(404,'Notice not found.')
        try:job=queue(db,row,False)
        except ValueError as error:raise HTTPException(422,str(error))from error
        db.commit();return {'id':job.id,'status':job.status}

@router.post('/jobs/{job_id}/cancel')
def cancel(job_id:str):
    with LOCK,SessionLocal()as db:
        row=db.get(FollowupJob,job_id)
        if row is None:raise HTTPException(404,'Follow-up job not found.')
        if row.status not in {'queued','deferred'}:raise HTTPException(409,'Only waiting work can be cancelled; an active run may finish.')
        row.status='cancelled';db.commit();return {'status':'cancelled'}
