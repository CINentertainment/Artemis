"""Local voice control, constrained commands and confirmed investigation jobs."""
import json,re,os,sys,subprocess,logging
from pathlib import Path
from typing import Literal
from datetime import datetime,timezone,timedelta
from threading import Event,Thread,RLock
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from sqlalchemy import String,Text,select
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.services.encryption import protect_text,unprotect_text

LOCK=RLock();LOG=logging.getLogger(__name__)
ROOT=Path(__file__).resolve().parents[2];STATE=ROOT/'.runtime/voice-agent'
def now():return datetime.now(timezone.utc)
def read_json(path,default):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.'+str(uuid4())+'.tmp')
    try:temp.write_text(json.dumps(value),encoding='utf-8');temp.replace(path)
    finally:temp.unlink(missing_ok=True)
def settings():return {'wake_phrase':'hey artemis','device':None,'energy':0.012,'end_silence':.55,'input_gain':1.0,'acknowledgement':True,'barge_in':False,'fast_transcription':True,'speech_speed':1.0,'reply_limit':12000,**read_json(STATE/'settings.json',{})}
def agent_state():
    value=read_json(STATE/'status.json',{});value['online']=bool(value.get('heartbeat')and now().timestamp()-value['heartbeat']<15 and value.get('phase')!='stopped');return value

def control(action,payload=None):
    atomic(STATE/'controls'/(str(uuid4())+'.json'),dict(action=action,payload=payload or{},created=now().timestamp()));return {'action':action}
class VoiceAction(Base):
    __tablename__='voice_actions'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    status:Mapped[str]=mapped_column(String(24),index=True)
    created:Mapped[str]=mapped_column(String(40),default=lambda:now().isoformat())
    payload:Mapped[str]=mapped_column(Text)
def packed(value):return protect_text(json.dumps(value,ensure_ascii=False))
def unpack(row):return json.loads(unprotect_text(row.payload))
def normalize(text):return re.sub(r'[^\w\s-]','',text.casefold()).strip()
def intent(text):
    clean=normalize(text);phrase=settings()['wake_phrase'];clean=re.sub(r'^'+re.escape(phrase)+r'\s*','',clean)
    choices={'activity':['what did you do today','daily activity recap','operations recap'],'read_brief':['give me my daily brief','read my brief','read the latest brief','brief me','read latest brief'],'new_brief':['prepare a new brief','create a new brief','prepare my daily brief'],'changes':['what changed','what changed overnight','show changes'],'status':['status','system status','what are you doing'],'pause_markets':['pause market research','pause market monitoring'],'resume_markets':['resume market research','resume market monitoring'],'pause_daily':['pause daily briefing'],'resume_daily':['resume daily briefing'],'stop':['stop speaking','stop briefing','stop'],'pause':['pause briefing','pause speaking'],'resume':['resume briefing','resume speaking'],'mute':['mute microphone','stop listening'],'cancel':['cancel research','cancel']}
    for name,phrases in choices.items():
        if clean in phrases:return name,''
    match=re.match(r'^(?:investigate|research)\s+(.+)$',clean)
    if match:return 'investigate',match[1][:300]
    match=re.match(r'^(?:ask about|question|tell me about)\s+(.+)$',clean)
    if match:return 'ask',match[1][:300]
    return 'unknown',''

def latest_brief(report_id=None):
    from app.config import get_settings
    folder=Path(get_settings().data_directory)/'reports';rows=[]
    if report_id is not None:
        try:requested=str(UUID(str(report_id)))
        except (ValueError,TypeError,AttributeError):return None,[]
        paths=[folder/(requested+'.json')]
    else:paths=folder.glob('*.json')
    for path in paths:
        value=read_json(path,{})
        try:key=str(UUID(value.get('id','')))
        except (ValueError,TypeError,AttributeError):continue
        if report_id is not None and key!=requested:continue
        if(folder/(key+'.pdf')).is_file():rows.append(value)
    if not rows:return None,[]
    manifest=max(rows,key=lambda r:r.get('created_at',''))
    from pypdf import PdfReader
    reader=PdfReader(folder/(manifest['id']+'.pdf'));pages=[p.extract_text()or''for p in reader.pages]
    return manifest,pages

def brief_text(report_id=None):
    manifest,pages=latest_brief(report_id)if report_id else latest_brief()
    if not manifest:return {'speech':'No saved briefing PDF is available. Say prepare a new brief to queue one.'}
    blocks=[];started=False
    for page in pages:
        header=page.lstrip()
        if header.startswith(('Summary of events','Risk Tracker case updates','Market research outlook','Analytical assessments','Mission briefing')):started=True
        if header.startswith(('Notes by collection time','Additional collected reporting','References and coverage')):started=False
        if started:blocks.append(re.sub(r'ARTEMIS\s*/\s*DAILY INTELLIGENCE BRIEF\s*\d+','',page).strip())
    text='\n'.join(blocks).strip()
    if not text:text='This report has no summary section available for speech. Open the PDF for its selected reporting.'
    if len(text)>12000:text=text[:12000]+' Reading limit reached. Open the PDF for the complete briefing.'
    return {'speech':'Saved briefing. Source statements have not been independently verified. '+text,'report_id':manifest['id'],'link':'/reports/'+manifest['id']+'/pdf'}

def answer(question):
    manifest,pages=latest_brief()
    if not manifest:return {'speech':'No saved brief is available to search.'}
    words={w for w in re.findall(r'[a-z]{4,}',question.casefold())if w not in {'what','when','where','about','does','have','that','this','with','from','tell','more'}}
    passages=[]
    for page in pages[2:]:
        for text in re.split(r'\n\s*\n|[•]',page):
            score=sum(word in text.casefold()for word in words)
            if score and len(text.strip())>35:passages.append((score,text.strip()[:1000]))
    matches=list(dict.fromkeys(text for _,text in sorted(passages,key=lambda p:p[0],reverse=True)))[:2]
    speech='Matching passages copied from the saved brief, not a new analysis. '+ '\n'.join(matches)if matches else'I did not find a matching passage in the latest saved brief. You can request an investigation.'
    return {'speech':speech,'report_id':manifest['id'],'link':'/reports/'+manifest['id']+'/pdf'}

def dispatch(text):
    kind,argument=intent(text)
    if kind=='activity':
        from app.services.operations_recovery import recap,snapshot
        report=recap();state=snapshot();c=report['counts']
        return {'speech':'In the past twenty-four hours, '+str(c['completed_runs'])+' saved research runs completed, referencing '+str(c['confirmed_note_ids'])+' distinct confirmed note IDs. '+str(c['observed_market_outlooks'])+' market outlook records were observed and '+str(c['briefs_available'])+' briefing jobs have an available result. '+str(len(state['alerts']))+' service or worker attention items are listed. These counts are not independent event totals; the recap page explains coverage gaps.','link':'/static/recovery.html'}
    if kind=='read_brief':return brief_text()
    if kind=='ask':return answer(argument)
    if kind in {'stop','pause','resume','mute'}:control(kind);return {'speech':{'stop':'Speech stopped.','pause':'Briefing paused.','resume':'Resuming briefing.','mute':'Microphone muted. Use the desktop controls to unmute.'}[kind],'control':kind}
    if kind=='new_brief':
        from app.services.operations_center import run
        value=run();return {'speech':'A briefing is already pending.'if value.get('already_pending')else'Your briefing is queued using your saved preferences.','link':'/static/operations.html'}
    if kind in {'pause_daily','resume_daily'}:
        from app.services.operations_center import config,settings as save,Preferences
        with SessionLocal()as db:value=config(db)
        value['enabled']=kind=='resume_daily';save(Preferences(**value));return {'speech':'Daily briefing schedule '+('enabled.'if value['enabled']else'paused. Active work may finish.')}
    if kind in {'pause_markets','resume_markets'}:
        from app.services.market_automation import load,update,now as market_now
        update(dict(enabled=kind=='resume_markets',next_at=market_now().isoformat()if kind=='resume_markets'else None));return {'speech':'Market monitoring '+('resumed.'if kind=='resume_markets'else'paused. An active request may finish.')}
    if kind=='changes':
        from app.services.change_followup import status
        rows=[n for n in status()['notices']if not n['reviewed']];return {'speech':str(len(rows))+' unreviewed notices in the returned change inbox. '+'. '.join(n['title']+' for '+n['topic']for n in rows[:5])+'. These are review cues, not independently confirmed event changes.','link':'/static/changes.html'}
    if kind=='status':
        from app.services.operations_center import status,workers
        brief=status();state=workers();return {'speech':'Daily brief '+('enabled'if brief['settings']['enabled']else'paused')+'. '+str(sum(r.get('enabled',False)for r in state.get('research',[])))+' research schedules enabled. Market worker reports '+str(state.get('markets',{}).get('status','unknown'))+'. Risk delivery reports '+str(state.get('risk_tracker',{}).get('status','unknown'))+'.'}
    return {'speech':'Supported commands include read my brief, prepare a new brief, what changed, system status, pause market monitoring, investigate followed by a topic, and ask about a topic in the latest brief.'}


def command(text,key,confirmation=None):
    with LOCK,SessionLocal()as db:
        existing=db.get(VoiceAction,key)
        if existing:return unpack(existing).get('reply',{'speech':'That command is already being handled.'})
        if normalize(text)in {'confirm research','confirm investigation'}:
            pending=db.get(VoiceAction,str(confirmation))if confirmation else None
            if pending is None or pending.status!='pending':return {'speech':'No pending investigation can be confirmed.'}
            payload=unpack(pending)
            if datetime.fromisoformat(payload['expires'])<now():pending.status='expired';db.commit();return {'speech':'Research confirmation expired. Please request the investigation again.'}
            waiting=db.scalars(select(VoiceAction).where(VoiceAction.status.in_(['queued','running']))).all()
            if len(waiting)>=5:return {'speech':'Five voice investigations are already active or waiting. Review those before queuing another.'}
            pending.status='queued';payload['reply']={'speech':'Investigation queued. Confirmed saved notes will be delivered to its new investigation board.','link':'/static/voice.html'};pending.payload=packed(payload);db.add(VoiceAction(id=key,status='done',payload=packed({'reply':payload['reply']})));db.commit();return payload['reply']
        kind,topic=intent(text)
        if kind=='investigate':
            reply={'speech':'Investigate '+topic+'? Say confirm research within thirty seconds, or cancel.','confirmation':key}
            db.add(VoiceAction(id=key,status='pending',payload=packed(dict(topic=topic,expires=(now()+timedelta(seconds=30)).isoformat(),reply=reply))));db.commit();return reply
        if kind=='cancel'and confirmation:
            row=db.get(VoiceAction,str(confirmation))
            if row and row.status=='pending':row.status='cancelled';db.commit()
            return {'speech':'Pending investigation cancelled.'}
        # Serialized command ledger prevents a repeated HTTP request performing the action twice.
        db.add(VoiceAction(id=key,status='handling',payload=packed({'reply':{'speech':'Command already received; check its status.'}})));db.commit()
        try:reply=dispatch(text)
        except Exception as error:reply={'speech':'The command could not complete: '+type(error).__name__+'. Check the Voice Control page and supporting services.'}
        row=db.get(VoiceAction,key);row.status='done';row.payload=packed({'reply':reply});db.commit();return reply

class Worker:
    def __init__(self):self.stop_event=Event();self.thread=None
    def start(self):
        with SessionLocal()as db:
            for row in db.scalars(select(VoiceAction).where(VoiceAction.status=='running')).all():row.status='blocked_review'
            db.commit()
        self.stop_event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-voice-investigation');self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join()
    def tick(self):
        from app.routes.research import research_lock,run_requisition,RunRequest
        from app.research_models import ResearchRun,ResearchRequisition
        from app.services import intelligence_workspace as work
        from app.services.operations_recovery import ready
        if research_lock.locked()or not ready(['ollama','search','notes'],'voice investigations'):return
        with LOCK,SessionLocal()as db:
            row=db.scalars(select(VoiceAction).where(VoiceAction.status=='queued').limit(1)).first()
            if row is None:return
            key=row.id;payload=unpack(row);req_id=str(uuid5(NAMESPACE_URL,'voice-req:'+key));board_id=uuid5(NAMESPACE_URL,'voice-board:'+key).hex
            if db.get(ResearchRequisition,req_id)is None:db.add(ResearchRequisition(id=req_id,topic=payload['topic'],questions_json='[]'))
            if db.get(work.IntelligenceBoard,board_id)is None:
                board=dict(id=board_id,title=payload['topic'][:150],notes={},entities={},links=[],history=[],research_bindings={req_id:dict(topic=payload['topic'],bound_at=work.now())})
                if payload.get('voice_context'):board['voice_context']=payload['voice_context']
                db.add(work.IntelligenceBoard(id=board_id,version=1,payload=work.protect_text(json.dumps(board))))
            payload.update(requisition_id=req_id,board_id=board_id,run_id=key);row.payload=packed(payload);row.status='running';db.commit()
            existing=db.get(ResearchRun,key)
            if existing:row.status='completed'if existing.status=='completed'else'blocked_review';db.commit();return
        try:
            with research_lock.background():value=run_requisition(req_id,RunRequest(request_id=UUID(key)))
            with SessionLocal()as db:row=db.get(VoiceAction,key);row.status='completed'if value.get('status')=='completed'else'blocked_review';db.commit()
        except HTTPException as error:
            with SessionLocal()as db:row=db.get(VoiceAction,key);row.status='queued'if error.status_code==409 and db.get(ResearchRun,key)is None else'blocked_review';db.commit()
        except Exception:
            LOG.exception('Voice investigation failed')
            with SessionLocal()as db:row=db.get(VoiceAction,key);row.status='blocked_review';db.commit()
    def loop(self):
        while not self.stop_event.is_set():
            try:self.tick()
            except Exception:LOG.exception('Voice research worker error')
            self.stop_event.wait(5)
worker=Worker()
def same_origin(request:Request):
    origin=request.headers.get('origin')
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(origin and origin!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use this Artemis origin for voice control.')
router=APIRouter(prefix='/voice-hub',dependencies=[Depends(same_origin)])
class Command(BaseModel):
    text:str=Field(min_length=1,max_length=500)
    request_id:UUID=Field(default_factory=uuid4)
    confirmation:UUID|None=None
    speak:bool=False
    source:Literal['web','native']='web'
@router.post('/command')
def handle(body:Command):
    from app.services import voice_sessions as sessions
    text=body.text;clean=normalize(text)
    phrase=settings()['wake_phrase'];clean=re.sub(r'^'+re.escape(phrase)+r'\s*','',clean)
    key=str(body.request_id);kind,argument=intent(text)
    event_command=clean in {'keep monitoring this','pause monitoring this','what changed with this event','whats changed with this event','event status'}or clean.startswith('open event ')
    planning_command=clean in {'what needs my attention','prioritize this event'}or clean.startswith('focus on ')
    delivery_command=clean in {'read my alerts','brief me on what changed','snooze notifications','resume notifications','start briefing session'}
    mission_command=clean in {'list missions','mission status','brief this mission','pause this mission','resume this mission','prioritize this mission'}or clean.startswith('open mission ')
    from app.services.voice_extras import COMMANDS,voice as extra_voice
    if clean in COMMANDS:
        with LOCK,SessionLocal()as db:
            existing=db.get(VoiceAction,key)
            if existing:reply=unpack(existing)['reply']
            else:
                reply=extra_voice(clean);db.add(VoiceAction(id=key,status='done',payload=packed({'reply':reply})));db.commit()
    elif mission_command:
        from app.services.mission_control import voice
        with LOCK,SessionLocal()as db:
            existing=db.get(VoiceAction,key)
            if existing:reply=unpack(existing)['reply']
            else:
                reply=voice(clean);db.add(VoiceAction(id=key,status='done',payload=packed({'reply':reply})));db.commit()
    elif delivery_command:
        from app.services.proactive_delivery import voice
        with LOCK,SessionLocal()as db:
            existing=db.get(VoiceAction,key)
            if existing:reply=unpack(existing)['reply']
            else:
                reply=voice(clean,key,body.speak or body.source=='native');db.add(VoiceAction(id=key,status='done',payload=packed({'reply':reply})));db.commit()
    elif planning_command:
        from app.services.collection_planning import voice
        with LOCK,SessionLocal()as db:
            existing=db.get(VoiceAction,key)
            if existing:reply=unpack(existing)['reply']
            else:
                reply=voice(clean);db.add(VoiceAction(id=key,status='done',payload=packed({'reply':reply})));db.commit()
    elif event_command:
        from app.services.event_tracking import voice
        with LOCK,SessionLocal()as db:
            existing=db.get(VoiceAction,key)
            if existing:reply=unpack(existing)['reply']
            else:
                reply=voice(clean);db.add(VoiceAction(id=key,status='done',payload=packed({'reply':reply})));db.commit()
    elif clean in {'start voice session','new voice session','start conversation','new conversation'}:
        with LOCK,SessionLocal()as db:
            existing=db.get(VoiceAction,key)
            if existing:reply=unpack(existing)['reply']
            else:
                value=sessions.create();reply={'speech':'Voice conversation started with the latest saved briefing. Ask a question using the wake phrase or Listen now.','session_id':value['id'],'link':'/static/voice-session.html#'+value['id']}
                db.add(VoiceAction(id=key,status='done',payload=packed({'reply':reply})));db.commit()
    elif clean in {'end conversation','end voice session'}:
        value=sessions.current(False)
        if value:sessions.end(UUID(value['id']))
        reply={'speech':'Voice conversation ended.'}
    elif clean in {'look into that further','investigate that','research that'}:
        value=sessions.current(False)
        reply=sessions.followup(value['id'],key)if value else{'speech':'Start a conversation and identify an event before requesting further research.'}
    elif kind in {'ask','unknown'}and clean not in {'confirm research','confirm investigation'}:
        reply=sessions.submit(text,key,speak=body.speak or body.source=='native')
    else:reply=command(text,key,body.confirmation)
    if body.speak and not reply.get('control')and not reply.get('pending'):
        if delivery_command:
            from app.services.proactive_delivery import dispatch_reply
            return {**reply,'playback':dispatch_reply(key,reply)}
        if agent_state().get('online'):control('reply',reply);reply={**reply,'playback':'queued'}
        else:reply={**reply,'playback':'listener_offline'}
    return reply
class Control(BaseModel):action:str=Field(pattern=r'^(mute|unmute|pause|resume|stop|quit|listen_once|speaker_test|calibrate)$')
@router.post('/control')
def controls(body:Control):return control(body.action)
class Settings(BaseModel):
    wake_phrase:str=Field(default='hey artemis',pattern=r'^[a-z]+(?: [a-z]+){1,3}$',max_length=35)
    device:int|None=Field(default=None,ge=0)
    output_device:int|None=Field(default=None,ge=0)
    end_silence:float=Field(default=.55,ge=.35,le=1.5)
    input_gain:float=Field(default=1,ge=.5,le=4)
    acknowledgement:bool=True
    barge_in:bool=False
    fast_transcription:bool=True
    energy:float=Field(default=.012,ge=.002,le=.15)
@router.put('/settings')
def preferences(body:Settings):
    value=settings();value.update(body.model_dump());atomic(STATE/'settings.json',value);return value
@router.post('/start')
def start():
    with LOCK:
        if agent_state().get('online'):return {'started':False,'message':'Voice listener is already running.'}
        model=STATE/'models/vosk-model-small-en-us-0.15'
        if not model.is_dir():raise HTTPException(409,'Run scripts/Setup-Voice-Activation.ps1 first to install the listener and download its local model.')
        python=Path(sys.executable).with_name('pythonw.exe')if os.name=='nt'else Path(sys.executable)
        if not python.exists():python=Path(sys.executable)
        subprocess.Popen([str(python),'-m','app.voice_agent'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return {'started':True,'message':'Starting local voice controls. Check the desktop panel for microphone or model errors.'}
@router.get('/status')
def status():
    with SessionLocal()as db:
        rows=db.scalars(select(VoiceAction).where(VoiceAction.status.in_(['pending','queued','running','completed','blocked_review'])).order_by(VoiceAction.created.desc()).limit(40)).all()
        jobs=[{'id':r.id,'status':r.status,**{k:v for k,v in unpack(r).items()if k in {'topic','requisition_id','board_id','run_id'}}}for r in rows]
    return {'agent':agent_state(),'settings':settings(),'model_installed':(STATE/'models/vosk-model-small-en-us-0.15').is_dir(),'jobs':jobs}

@router.post('/warmup')
def warmup():
    from app.services.voice import prepare_voice_model,synthesize_speech,VoiceError
    import time
    started=time.monotonic()
    try:
        prepare_voice_model();synthesize_speech('Ready.')
        return {'ready':True,'seconds':round(time.monotonic()-started,2)}
    except VoiceError as error:raise HTTPException(503,str(error))from error
