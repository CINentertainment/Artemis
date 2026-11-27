"""Local service preflight, bounded dead-worker recovery and saved activity recap.

Never replays a failed research workflow or restarts an external service process.
"""
import hashlib,json,logging
from datetime import datetime,timedelta,timezone
from importlib import import_module
from pathlib import Path
from threading import RLock,Event,Thread
from urllib.parse import urlsplit
from uuid import UUID
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel
from sqlalchemy import String,Text,Float,select,delete,func
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.config import get_settings
from app.services.encryption import protect_text,unprotect_text

LOG=logging.getLogger(__name__);LOCK=RLock();DELAYS=(15,60,180,600)
class RecoveryState(Base):
    __tablename__='operations_recovery_state'
    id:Mapped[str]=mapped_column(String(100),primary_key=True)
    payload:Mapped[str]=mapped_column(Text)
class ActivityEvent(Base):
    __tablename__='operations_activity_events'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    kind:Mapped[str]=mapped_column(String(30),index=True)
    payload:Mapped[str]=mapped_column(Text)
def now():return datetime.now(timezone.utc)
def encode(value):return protect_text(json.dumps(value,ensure_ascii=False))
def decode(row):return json.loads(unprotect_text(row.payload))
def read(key,default=None):
    with SessionLocal()as db:
        row=db.get(RecoveryState,key);return decode(row)if row else(default if default is not None else{})
def write(key,value):
    with LOCK,SessionLocal()as db:
        row=db.get(RecoveryState,key)
        if row:row.payload=encode(value)
        else:db.add(RecoveryState(id=key,payload=encode(value)))
        db.commit()
def preferences():return {'enabled':True,'recover_workers':True,**read('preferences')}
def record(identity,kind,title,details=None,stamp=None):
    key=hashlib.sha256(identity.encode()).hexdigest()
    with LOCK,SessionLocal()as db:
        if db.get(ActivityEvent,key)is None:db.add(ActivityEvent(id=key,kind=kind,created=stamp or now().timestamp(),payload=encode({'title':title,**(details or{})})));db.commit()

def targets():
    s=get_settings();key=s.market_lens_api_key.get_secret_value()if s.market_lens_api_key else''
    return {'ollama':('Ollama',str(s.ollama_url),'/api/tags',{}),'search':('SearXNG','http://127.0.0.1:8888','/',{}),'notes':('Intel Notes Manager',str(s.intel_notes_url),'/health',{}),'risk_tracker':('Risk Tracker',str(s.risk_tracker_url),'/health',{}),'market_lens':('Market Lens',str(s.market_lens_url),'/integration/artemis/health',{'X-Artemis-Key':key})}
def probe(key,target):
    title,base,path,headers=target
    parts=urlsplit(base)
    if parts.scheme not in {'http','https'}or parts.hostname not in {'localhost','127.0.0.1','::1'}or parts.username or parts.password:return 'configuration_required','Configure a loopback service URL.'
    if key=='market_lens'and not headers['X-Artemis-Key']:return 'configuration_required','Configure the Market Lens API key in Artemis.'
    try:
        with httpx.Client(timeout=2,trust_env=False,follow_redirects=False)as client:response=client.get(base.rstrip('/')+path,headers=headers)
        if response.status_code in {401,403}:return 'authentication_required','Service rejected the configured access.'
        if response.status_code!=200:return 'unavailable','Service health request returned HTTP '+str(response.status_code)+'.'
        if key=='ollama':
            data=response.json()
            if not isinstance(data,dict):return 'unavailable','Ollama returned an invalid model listing.'
            models=data.get('models')
            if not isinstance(models,list):return 'unavailable','Ollama returned an invalid model listing.'
            if not any(m.get('name')=='qwen3:8b'for m in models if isinstance(m,dict)):return 'configuration_required','Install qwen3:8b in the existing Ollama instance.'
        return 'healthy','HTTP health check passed.'if key!='search'else'Search interface reachable; engine results are not tested.'
    except (httpx.HTTPError,ValueError):return 'unavailable','Local health request failed or timed out.'

def check_service(key,target,force=False):
    with LOCK:
        old=read('service:'+key);stamp=now().timestamp()
        if not force and old.get('next_check',0)>stamp:return
        state,message=probe(key,target);failures=0 if state=='healthy'else old.get('failures',0)+1
        changed=old.get('state')!=state
        value=dict(key=key,name=target[0],state=state,message=message,checked_at=stamp,last_healthy=stamp if state=='healthy'else old.get('last_healthy'),failures=failures,next_check=stamp+(60 if state=='healthy'else DELAYS[min(failures-1,3)]),acknowledged=False if changed else old.get('acknowledged',False))
        write('service:'+key,value)
        if changed:record('service:'+key+':'+str(stamp),'service',target[0]+': '+state,{'service':key,'state':state})

def ready(names,work_key=None):
    if not preferences()['enabled']:return True
    stamp=now().timestamp();missing=[n for n in names if (lambda s:s.get('state')!='healthy'or stamp-s.get('checked_at',0)>180)(read('service:'+n))]
    if work_key:
        old=read('waiting:'+work_key);value={'waiting':bool(missing),'services':missing,'updated_at':stamp}
        if old.get('waiting')!=value['waiting']or old.get('services')!=missing:
            write('waiting:'+work_key,value)
            record('waiting:'+work_key+':'+str(stamp),'queue',work_key+(' waiting for '+', '.join(missing)if missing else' dependencies recovered'),{'work':work_key,'waiting':bool(missing)})
    return not missing

WORKERS={'backups':('app.services.continuity','worker','thread'),'specialists':('app.services.specialist_team','worker','thread'),'missions':('app.services.mission_control','worker','thread'),'assessments':('app.services.evidence_assessment','worker','thread'),'delivery':('app.services.proactive_delivery','worker','thread'),'planning':('app.services.collection_planning','worker','thread'),'events':('app.services.event_tracking','worker','thread'),'research':('app.services.research_scheduler','research_scheduler','_thread'),'markets':('app.services.market_automation','worker','thread'),'risk_map':('app.services.risk_map','worker','thread'),'briefs':('app.services.operations_center','worker','thread'),'followups':('app.services.change_followup','worker','thread'),'workspace':('app.services.research_workspace_bridge','research_workspace_worker','thread'),'voice_answers':('app.services.voice_sessions','worker','thread'),'voice_research':('app.services.voice_hub','worker','thread')}
def inspect_worker(key,spec,recover=True):
    stamp=now().timestamp();old=read('worker:'+key)
    try:
        value=getattr(import_module(spec[0]),spec[1]);thread=getattr(value,spec[2]);alive=bool(thread and thread.is_alive())
        result=dict(key=key,alive=alive,checked_at=stamp,attempts=[t for t in old.get('attempts',[])if t>stamp-3600],next_attempt=old.get('next_attempt',0),acknowledged=old.get('acknowledged',False),message='Worker thread is running.'if alive else'Worker thread is stopped.')
        if alive and not old.get('alive',True):result['acknowledged']=False;record('worker-up:'+key+':'+str(stamp),'recovery',key+' worker recovered')
        if not alive and recover and len(result['attempts'])<3 and result['next_attempt']<=stamp:
            result['attempts'].append(stamp);result['next_attempt']=stamp+DELAYS[min(len(result['attempts'])-1,3)];result['acknowledged']=False
            try:value.start();result['alive']=bool(getattr(value,spec[2])and getattr(value,spec[2]).is_alive());result['message']='Worker restart '+('completed.'if result['alive']else'did not produce a running thread.')
            except Exception:result['message']='Worker restart failed. Check its logs and settings.'
            record('restart:'+key+':'+str(stamp),'recovery',key+': '+result['message'])
        if not result['alive']and len(result['attempts'])>=3:result['message']='Three restart attempts within an hour. Manual attention required; no job was replayed by the supervisor.'
    except Exception:result={**old,'key':key,'alive':False,'checked_at':stamp,'message':'Worker inspection unavailable. Check local application logs.'}
    write('worker:'+key,result)

def scan_activity():
    """Sample durable completion markers. Not a complete historic transaction ledger."""
    for key,module,loader in [('markets','app.services.market_automation','load'),('risk_map','app.services.risk_map','state')]:
        try:
            state=getattr(import_module(module),loader)();stamp=state.get('last_finished')
            if not stamp:continue
            when=datetime.fromisoformat(stamp).timestamp()
            if when<now().timestamp()-7*86400:continue
            details={'work':key,'status':state.get('status'),'errors':len(state.get('errors',[]))}
            if key=='markets':details['symbols']=[r.get('symbol')for r in state.get('results',[])];details['outlook_ids']=list(dict.fromkeys(str(r['record_id'])for r in state.get('results',[])if r.get('record_id')is not None));details['outlooks']=len(details['outlook_ids'])
            else:details['summary']=state.get('summary',{})
            record(key+':'+stamp,'completion',key+' cycle: '+str(state.get('status')),details,when)
        except Exception:LOG.warning('Activity snapshot unavailable for %s',key)
    with SessionLocal()as db:
        db.execute(delete(ActivityEvent).where(ActivityEvent.created<now().timestamp()-7*86400))
        keep=select(ActivityEvent.id).order_by(ActivityEvent.created.desc()).limit(2000)
        db.execute(delete(ActivityEvent).where(ActivityEvent.id.not_in(keep)));db.commit()

def recap(hours=24):
    cutoff=now()-timedelta(hours=hours);warnings=[];runs=[];notes=set();counts={'completed_runs':0,'failed_runs':0,'running_runs':0}
    try:
        from app.research_models import ResearchRun
        with SessionLocal()as db:
            rows=db.scalars(select(ResearchRun).where(ResearchRun.started_at>=cutoff.replace(tzinfo=None)).order_by(ResearchRun.started_at.desc()).limit(1000)).all()
            if len(rows)==1000:warnings.append('Research recap limited to 1,000 runs.')
            for row in rows:
                if row.status+'_runs'in counts:counts[row.status+'_runs']+=1
                result=json.loads(row.result_json)if row.result_json else{}
                for item in result.get('event_notes')or[]:
                    if isinstance(item,dict)and type(item.get('intel_note_id'))is int and item['intel_note_id']>0 and not item.get('error'):notes.add(item['intel_note_id'])
                if row.status!='completed':runs.append({'id':row.id,'status':row.status})
    except Exception:warnings.append('Research run coverage unavailable.')
    with SessionLocal()as db:
        events=db.scalars(select(ActivityEvent).where(ActivityEvent.created>=cutoff.timestamp()).order_by(ActivityEvent.created.desc()).limit(300)).all()
        total=db.scalar(select(func.count()).select_from(ActivityEvent).where(ActivityEvent.created>=cutoff.timestamp()))or 0
        if total>300:warnings.append('Showing the latest 300 activity records; counts below are based on that returned set.')
        activity=[{'id':r.id,'created':r.created,'kind':r.kind,**decode(r)}for r in events]
    briefs=[]
    try:
        from app.services.operations_center import BriefJob,job_data
        with SessionLocal()as db:
            for row in db.scalars(select(BriefJob).order_by(BriefJob.due.desc()).limit(200)).all():
                item=job_data(row)
                if datetime.fromisoformat(item['created_at'])>=cutoff:briefs.append({'id':row.id,'status':row.status,'report_id':item.get('report_id'),'available':bool(item.get('report_id')and(Path(get_settings().data_directory)/'reports'/(item['report_id']+'.pdf')).is_file())})
        if len(briefs)==200:warnings.append('Briefing recap limited to 200 returned jobs.')
    except Exception:warnings.append('Briefing job coverage unavailable.')
    return {'window_start':cutoff.isoformat(),'window_end':now().isoformat(),'counts':{**counts,'confirmed_note_ids':len(notes),'observed_market_outlooks':len({i for e in activity if e.get('work')=='markets'for i in e.get('outlook_ids',[])}),'briefs_available':sum(b.get('available',False)for b in briefs)},'note_ids':sorted(notes),'runs_needing_review':runs[:100],'briefs':briefs,'activity':activity,'warnings':warnings,'coverage':'Research counts use saved runs started in this window. Confirmed note IDs are deduplicated references, not independent events. Market and risk activity is sampled while monitoring runs; completions between samples or during downtime may be absent. Existing histories are not a full transaction ledger.'}

def snapshot():
    services=[{'key':k,'name':v[0],'state':'pending','failures':0,**read('service:'+k)}for k,v in targets().items()]
    for service in services:
        if service.get('state')=='healthy'and now().timestamp()-service.get('checked_at',0)>180:service.update(state='stale',message='The last health check is stale; new work waits for a fresh check.',failures=3)
    workers=[{'key':k,'alive':None,**read('worker:'+k)}for k in WORKERS]
    alerts=[]
    for s in services:
        if s['state']!='healthy'and(s.get('failures',0)>=3 or s['state']in {'configuration_required','authentication_required'}):alerts.append({'id':'service:'+s['key'],'title':s['name']+' needs attention','message':s.get('message',''),'acknowledged':s.get('acknowledged',False)})
    for w in workers:
        if w.get('alive')is False:alerts.append({'id':'worker:'+w['key'],'title':w['key']+' worker needs attention','message':w.get('message',''),'acknowledged':w.get('acknowledged',False)})
    for key,module,loader in [('markets','app.services.market_automation','load'),('risk_map','app.services.risk_map','state')]:
        try:
            state=getattr(import_module(module),loader)()
            if state.get('status')in {'failed','partial','interrupted'}and state.get('errors'):alerts.append({'id':'job:'+key,'title':key+' workload needs review','message':str(len(state['errors']))+' error(s) in its last recorded cycle. Open the corresponding worker page for details.','acknowledged':False})
        except Exception:pass
    try:
        from app.services.operations_center import BriefJob
        with SessionLocal()as db:
            incomplete=db.scalar(select(func.count()).select_from(BriefJob).where(BriefJob.status.in_(['failed','partial'])))or 0
            if incomplete:alerts.append({'id':'job:briefs','title':'Briefing jobs need review','message':str(incomplete)+' failed or partial briefing job(s). Open Daily briefings to inspect or retry them.','acknowledged':False})
    except Exception:pass
    try:
        from app.services.continuity import status as backup_status
        vault=backup_status()
        if not vault['configured']or vault['overdue']or vault['status']['state']=='failed':
            alerts.append({'id':'job:backups','title':'Backups need attention','message':'Configure encrypted backups or inspect the latest failed/overdue backup. A running server does not mean its data is protected. Open Backups & recovery.','acknowledged':False})
    except Exception:
        alerts.append({'id':'job:backups','title':'Backup configuration unavailable','message':'The automatic backup credential or state could not be read. Open Backups & recovery.','acknowledged':False})
    review=[]
    for module,model,statefield in [('app.services.change_followup','FollowupJob','status'),('app.services.voice_hub','VoiceAction','status'),('app.services.event_tracking','EventJob','state'),('app.services.collection_planning','CollectionJob','state')]:
        try:
            cls=getattr(import_module(module),model)
            with SessionLocal()as db:
                for row in db.scalars(select(cls).where(getattr(cls,statefield)=='blocked_review').limit(30)).all():review.append({'kind':model,'id':row.id,'state':'blocked_review'})
        except Exception:pass
    with SessionLocal()as db:waiting=[{'id':r.id,**decode(r)}for r in db.scalars(select(RecoveryState).where(RecoveryState.id.like('waiting:%'))).all()if decode(r).get('waiting')]
    return {'preferences':preferences(),'services':services,'workers':workers,'alerts':alerts,'blocked_jobs':review,'waiting':waiting,'monitor':read('monitor'),'worker_alive':bool(worker.thread and worker.thread.is_alive())}

class Worker:
    def __init__(self):self.event=Event();self.thread=None;self.started=0
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.started=now().timestamp();self.event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-operations-recovery');self.thread.start()
    def stop(self):
        self.event.set()
        if self.thread:self.thread.join(timeout=15)
    def tick(self):
        prefs=preferences()
        if not prefs['enabled']:return
        for key,target in targets().items():
            if self.event.is_set():return
            check_service(key,target)
        for key,spec in WORKERS.items():
            if self.event.is_set():return
            inspect_worker(key,spec,recover=prefs['recover_workers']and now().timestamp()-self.started>90)
        scan_activity();write('monitor',{'last_checked':now().isoformat()})
    def loop(self):
        while not self.event.is_set():
            try:self.tick()
            except Exception:LOG.exception('Operations recovery monitor error')
            self.event.wait(10)
worker=Worker()
def same_origin(request:Request):
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(request.headers.get('origin')and request.headers['origin']!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use this Artemis origin for recovery changes.')
router=APIRouter(prefix='/operations-recovery',dependencies=[Depends(same_origin)])
class Preferences(BaseModel):enabled:bool=True;recover_workers:bool=True
@router.get('/status')
def status():return snapshot()
@router.get('/recap')
def daily_recap():return recap()
@router.put('/settings')
def settings(body:Preferences):
    write('preferences',body.model_dump());return preferences()
@router.post('/check')
def check():
    # Reset due times; network checks stay on the monitor thread, never the request thread.
    for key in targets():
        value=read('service:'+key);value['next_check']=0;write('service:'+key,value)
    return {'queued':True,'enabled':preferences()['enabled']}
@router.post('/alerts/{scope}/{key}/acknowledge')
def acknowledge(scope:str,key:str):
    if scope not in {'service','worker'}or key not in(targets()if scope=='service'else WORKERS):raise HTTPException(404,'Alert not found.')
    value=read(scope+':'+key);value['acknowledged']=True;write(scope+':'+key,value);return {'acknowledged':True}
