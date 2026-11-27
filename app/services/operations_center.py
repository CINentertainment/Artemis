"""Durable local daily briefing jobs, bounded retries and worker visibility."""
import json
import logging
from datetime import datetime,timedelta,timezone,time
from pathlib import Path
from threading import Event,Thread,RLock
from uuid import uuid4,UUID,uuid5,NAMESPACE_URL
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from typing import Literal
from sqlalchemy import String,Text,Float,Integer,select,update,or_,and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.config import get_settings
from app.services.daily_report import generate,selected_sources

LOG=logging.getLogger(__name__);LOCK=RLock();LEASE_SECONDS=120;MAX_ATTEMPTS=3
class OperationsSettings(Base):
    __tablename__='operations_brief_settings'
    id:Mapped[str]=mapped_column(String(20),primary_key=True)
    payload:Mapped[str]=mapped_column(Text,nullable=False)
class BriefJob(Base):
    __tablename__='operations_brief_jobs'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    status:Mapped[str]=mapped_column(String(20),index=True)
    due:Mapped[float]=mapped_column(Float,index=True)
    lease_until:Mapped[float]=mapped_column(Float,default=0)
    owner:Mapped[str]=mapped_column(String(36),default='')
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    payload:Mapped[str]=mapped_column(Text)

def now():return datetime.now(timezone.utc)
def defaults():return dict(enabled=False,hour=7,minute=0,timezone='America/Chicago',hours=24,sources=['notes','risk_tracker','market_lens'],next_at=None)
def config(db):
    row=db.get(OperationsSettings,'default');return {**defaults(),**(json.loads(row.payload)if row else{})}
def save_config(db,value):
    row=db.get(OperationsSettings,'default')
    if row:row.payload=json.dumps(value)
    else:db.add(OperationsSettings(id='default',payload=json.dumps(value)))

def next_slot(current,settings):
    """First strictly future local wall-clock slot; spring-forward skips the gap."""
    zone=ZoneInfo(settings['timezone']);local=current.astimezone(zone)
    for offset in range(3):
        day=local.date()+timedelta(days=offset)
        candidate=datetime.combine(day,time(settings['hour'],settings['minute']),tzinfo=zone)
        # Resolve a nonexistent spring-forward wall time by moving forward through the gap.
        candidate=candidate.astimezone(timezone.utc).astimezone(zone)
        if candidate>local:return candidate.astimezone(timezone.utc)
    raise ValueError('No future daily slot.')

def job_data(job):return dict(id=job.id,status=job.status,attempts=job.attempts,next_attempt=datetime.fromtimestamp(job.due,timezone.utc).isoformat()if job.status in {'queued','retry'}else None,**json.loads(job.payload))
def enqueue(db,settings,end,kind='manual',identity=None):
    key=identity or str(uuid4())
    if db.get(BriefJob,key):return key
    payload=dict(kind=kind,created_at=now().isoformat(),window_end=end.isoformat(),hours=settings['hours'],timezone=settings['timezone'],sources=selected_sources(settings['sources']),report_id=None,error=None,finished_at=None)
    db.add(BriefJob(id=key,status='queued',due=now().timestamp(),lease_until=0,owner='',attempts=0,payload=json.dumps(payload)))
    return key

def schedule_due(current=None):
    current=current or now()
    with LOCK,SessionLocal()as db:
        settings=config(db)
        if not settings['enabled']:return
        due=datetime.fromisoformat(settings['next_at'])if settings['next_at']else next_slot(current,settings)
        if due>current:return
        # One most-recent due slot after downtime, never an unbounded backlog.
        # Calendar arithmetic must be local across DST, not a fixed 24h subtraction.
        local=current.astimezone(ZoneInfo(settings['timezone']))
        today=datetime.combine(local.date(),time(settings['hour'],settings['minute']),tzinfo=local.tzinfo)
        today=today.astimezone(timezone.utc).astimezone(local.tzinfo)
        if today>local:
            today=datetime.combine(local.date()-timedelta(days=1),time(settings['hour'],settings['minute']),tzinfo=local.tzinfo).astimezone(timezone.utc).astimezone(local.tzinfo)
        cutoff=max(due,today.astimezone(timezone.utc))
        key=str(uuid5(NAMESPACE_URL,'artemis-daily-brief:'+cutoff.isoformat()))
        enqueue(db,settings,cutoff,'scheduled',key)
        settings['next_at']=next_slot(current,settings).isoformat();save_config(db,settings)
        try:db.commit()
        except IntegrityError:db.rollback()

def claim(owner,current=None):
    stamp=(current or now()).timestamp()
    eligible=or_(and_(BriefJob.status.in_(['queued','retry']),BriefJob.due<=stamp),and_(BriefJob.status=='running',BriefJob.lease_until<stamp))
    with SessionLocal()as db:
        row=db.scalars(select(BriefJob).where(eligible).order_by(BriefJob.due).limit(1)).first()
        if row is None:return None
        if row.attempts>=MAX_ATTEMPTS:
            payload=json.loads(row.payload);existing=recovered_report(job_data(row),allow_partial=True)
            row.status=('partial'if existing.get('warnings')else'completed')if existing else'failed'
            payload['error']=None if existing else'Retry budget exhausted after an interrupted attempt.'
            if existing:payload.update(report_id=existing['id'],entries=existing['entries'],warnings=existing.get('warnings',[]))
            payload['finished_at']=now().isoformat();row.owner='';row.lease_until=0;row.payload=json.dumps(payload);db.commit();return None
        key=row.id
        result=db.execute(update(BriefJob).where(BriefJob.id==key,eligible).values(status='running',owner=owner,lease_until=stamp+LEASE_SECONDS,attempts=BriefJob.attempts+1))
        db.commit()
        if result.rowcount!=1:return None
        db.expire_all();return job_data(db.get(BriefJob,key))

def heartbeat(key,owner):
    with SessionLocal()as db:
        db.execute(update(BriefJob).where(BriefJob.id==key,BriefJob.owner==owner,BriefJob.status=='running').values(lease_until=now().timestamp()+LEASE_SECONDS));db.commit()

def transient(warnings):return any(any(word in w.casefold()for word in ('unavailable','could not be retrieved'))for w in warnings)
def recovered_report(job,allow_partial=False):
    if job.get('force_refresh')and not allow_partial:return None
    folder=Path(get_settings().data_directory)/'reports';key=job['id']
    try:
        manifest=json.loads((folder/(key+'.json')).read_text(encoding='utf-8'))
        if (folder/(key+'.pdf')).is_file()and manifest.get('end')==job['window_end']and set(manifest.get('sources',[]))==set(job['sources'])and(allow_partial or not transient(manifest.get('warnings',[]))):return manifest
    except (OSError,ValueError):pass
    return None

def finish(key,owner,manifest=None,error=None):
    with SessionLocal()as db:
        row=db.get(BriefJob,key)
        if row is None or row.owner!=owner or row.status!='running':return
        payload=json.loads(row.payload);payload['error']=error;payload['finished_at']=now().isoformat()
        if manifest:payload.update(report_id=manifest['id'],entries=manifest['entries'],warnings=manifest.get('warnings',[]))
        retry=bool(error)and row.attempts<MAX_ATTEMPTS
        row.status='retry'if retry else('partial'if manifest and (error or manifest.get('warnings'))else'failed'if error else'completed')
        row.due=now().timestamp()+[30,120,300][min(row.attempts-1,2)];row.owner='';row.lease_until=0;row.payload=json.dumps(payload);db.commit()

class Worker:
    def __init__(self):self.stop_event=Event();self.thread=None;self.owner=str(uuid4())
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-daily-briefs');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=5)
    def execute(self,job):
        done=Event()
        def renew():
            while not done.wait(20):
                try:heartbeat(job['id'],self.owner)
                except Exception:LOG.exception('Briefing lease heartbeat failed')
        pulse=Thread(target=renew,daemon=True);pulse.start()
        try:
            manifest=recovered_report(job)
            if manifest is None:
                with SessionLocal()as db:manifest=generate(db,job['hours'],job['timezone'],job['sources'],report_id=job['id'],end=datetime.fromisoformat(job['window_end']))
            error='Selected service retrieval is incomplete; a partial PDF is available.'if transient(manifest.get('warnings',[]))else None
            finish(job['id'],self.owner,manifest,error)
        except Exception as error:
            LOG.exception('Daily brief failed');finish(job['id'],self.owner,error='Brief generation failed: '+type(error).__name__+'. Check the selected services and PDF dependencies.')
        finally:done.set();pulse.join(timeout=1)
    def loop(self):
        while not self.stop_event.is_set():
            try:
                schedule_due()
                from app.services.operations_recovery import ready
                with SessionLocal()as db:
                    pending=db.scalars(select(BriefJob).where(BriefJob.status.in_(['queued','retry']),BriefJob.due<=now().timestamp()).order_by(BriefJob.due).limit(1)).first()
                    required=json.loads(pending.payload)['sources']if pending else[]
                job=claim(self.owner)if ready(required,'brief generation')else None
                if job:self.execute(job);continue
            except Exception:LOG.exception('Operations briefing worker error')
            self.stop_event.wait(10)
worker=Worker()

def same_origin(request:Request):
    origin=request.headers.get('origin')
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(origin and origin!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use this Artemis origin for Operations Center changes.')
router=APIRouter(prefix='/operations',dependencies=[Depends(same_origin)])
class Preferences(BaseModel):
    enabled:bool=False
    hour:int=Field(default=7,ge=0,le=23)
    minute:int=Field(default=0,ge=0,le=59)
    timezone:Literal['America/Chicago','UTC']='America/Chicago'
    hours:int=Field(default=24,ge=1,le=168)
    sources:list[Literal['notes','risk_tracker','market_lens']]=Field(default_factory=lambda:['notes','risk_tracker','market_lens'],min_length=1,max_length=3)
@router.put('/settings')
def settings(body:Preferences):
    with LOCK,SessionLocal()as db:
        old=config(db);new=body.model_dump();new['sources']=selected_sources(new['sources'])
        unchanged=all(old.get(k)==new[k]for k in new)
        new['next_at']=old['next_at']if unchanged else(next_slot(now(),new).isoformat()if new['enabled']else None)
        save_config(db,new)
        if not new['enabled']:
            for row in db.scalars(select(BriefJob).where(BriefJob.status.in_(['queued','retry']))).all():
                payload=json.loads(row.payload)
                if payload['kind']=='scheduled':row.status='cancelled';payload['error']='Scheduled briefing paused before its next attempt.';row.payload=json.dumps(payload)
        db.commit();return new
@router.post('/run')
def run():
    with LOCK,SessionLocal()as db:
        active=db.scalars(select(BriefJob).where(BriefJob.status.in_(['queued','running','retry']))).first()
        if active:return {'id':active.id,'already_pending':True}
        key=enqueue(db,config(db),now());db.commit();return {'id':key,'already_pending':False}
@router.post('/jobs/{job_id}/retry')
def retry(job_id:UUID):
    with LOCK,SessionLocal()as db:
        row=db.get(BriefJob,str(job_id))
        if row is None:raise HTTPException(404,'Briefing job not found.')
        if row.status not in {'failed','partial','cancelled'}:raise HTTPException(409,'This job is already active or completed.')
        row.status='queued';row.attempts=0;row.due=now().timestamp();row.owner='';row.lease_until=0
        payload=json.loads(row.payload);payload['error']=None;payload['finished_at']=None;payload['force_refresh']=True;row.payload=json.dumps(payload);db.commit();return job_data(row)
@router.get('/status')
def status():
    with SessionLocal()as db:
        settings=config(db);jobs=[job_data(r)for r in db.scalars(select(BriefJob).order_by(BriefJob.due.desc()).limit(40)).all()]
    for job in jobs:
        job['report_available']=bool(job.get('report_id')and(Path(get_settings().data_directory)/'reports'/(job['report_id']+'.pdf')).is_file())
    return {'settings':settings,'jobs':jobs,'worker_alive':bool(worker.thread and worker.thread.is_alive()),'now':now().isoformat()}
@router.get('/workers')
def workers():
    result={}
    try:
        from app.services.market_automation import load
        value=load();result['markets']={k:value.get(k)for k in ('enabled','status','last_started','last_finished','next_at','errors')}
    except Exception:result['markets']={'status':'unavailable'}
    try:
        from app.services.risk_map import state
        value=state();result['risk_tracker']={k:value.get(k)for k in ('enabled','status','last_finished','next_at','errors')}
    except Exception:result['risk_tracker']={'status':'unavailable'}
    try:
        from app.services.research_scheduler import ResearchSchedule,schedule_data
        with SessionLocal()as db:result['research']=[schedule_data(r)for r in db.scalars(select(ResearchSchedule).limit(100)).all()]
    except Exception:result['research']=[{'last_status':'unavailable'}]
    return result
