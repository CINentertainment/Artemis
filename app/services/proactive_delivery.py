"""Local saved-record inbox and delivery receipts. No external messaging.

Available, queued/leased, listener-reported playback and human acknowledgement
are separate states. Expired or interrupted deliveries are not blindly replayed.
"""
import hashlib,json,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
from threading import RLock,Event,Thread
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field,model_validator
from sqlalchemy import String,Text,Float,select,func,or_
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.config import get_settings
from app.services.encryption import protect_text,unprotect_text
LOG=logging.getLogger(__name__);LOCK=RLock()
class DeliverySettings(Base):
    __tablename__='proactive_delivery_settings'
    id:Mapped[str]=mapped_column(String(20),primary_key=True)
    payload:Mapped[str]=mapped_column(Text)
class InboxItem(Base):
    __tablename__='proactive_inbox'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    category:Mapped[str]=mapped_column(String(20),index=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    source_at:Mapped[float]=mapped_column(Float,index=True)
    availability:Mapped[str]=mapped_column(String(20),index=True)
    acknowledged_at:Mapped[float|None]=mapped_column(Float,nullable=True,index=True)
    payload:Mapped[str]=mapped_column(Text)
class DeliveryAttempt(Base):
    __tablename__='proactive_delivery_attempts'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    item_id:Mapped[str]=mapped_column(String(64),index=True)
    channel:Mapped[str]=mapped_column(String(20),index=True)
    state:Mapped[str]=mapped_column(String(24),index=True)
    created:Mapped[float]=mapped_column(Float,index=True)
    lease_until:Mapped[float]=mapped_column(Float,index=True)
    payload:Mapped[str]=mapped_column(Text)
class DeliveryCursor(Base):
    __tablename__='proactive_delivery_cursors'
    id:Mapped[str]=mapped_column(String(100),primary_key=True)
    payload:Mapped[str]=mapped_column(Text)
def now():return datetime.now(timezone.utc)
def pack(value):return protect_text(json.dumps(value,ensure_ascii=False))
def unpack(row):return json.loads(unprotect_text(row.payload))
def digest(*values):return hashlib.sha256('\n'.join(map(str,values)).encode()).hexdigest()
def defaults():return {'enabled':True,'desktop':False,'speech':False,'quiet_enabled':True,'quiet_start':22,'quiet_end':7,'timezone':'America/Chicago','max_per_hour':3,'cooldown_seconds':120,'digest_enabled':True,'digest_hour':7,'snooze_until':0,'routes':{'research':'digest','events':'immediate','markets':'digest','services':'immediate','briefs':'immediate','digest':'immediate'},'immediate_plan_ids':[]}
def config(db):
    row=db.get(DeliverySettings,'default');value=defaults();value.update(unpack(row)if row else{});return value
def save_config(db,value):
    row=db.get(DeliverySettings,'default')
    if row:row.payload=pack(value)
    else:db.add(DeliverySettings(id='default',payload=pack(value)))
def cursor(db,key):
    row=db.get(DeliveryCursor,key);return unpack(row)if row else{}
def set_cursor(db,key,value):
    row=db.get(DeliveryCursor,key)
    if row:row.payload=pack(value)
    else:db.add(DeliveryCursor(id=key,payload=pack(value)))
def stamp(value):
    if isinstance(value,datetime):return value.replace(tzinfo=timezone.utc).timestamp()if value.tzinfo is None else value.timestamp()
    if isinstance(value,str):return stamp(datetime.fromisoformat(value.replace('Z','+00:00')))
    return float(value)
def item(db,category,identity,source_at,title,summary,link,**extra):
    key=digest(category,identity);row=db.get(InboxItem,key);value={'title':title[:200],'summary':summary[:2000],'link':link,'severity':'information','eligible_after':now().timestamp(),'plan_id':None,**extra}
    if row:
        old=unpack(row);value['eligible_after']=old.get('eligible_after',row.created);row.payload=pack({**old,**value});row.availability='available';return row
    row=InboxItem(id=key,category=category,created=now().timestamp(),source_at=source_at,availability='available',acknowledged_at=None,payload=pack(value));db.add(row);return row

def intake():
    """Bounded local reads only. No new collection or note/case writes."""
    errors=[];cutoff=now()-timedelta(hours=24);stamp_now=now().timestamp()
    with LOCK,SessionLocal()as db:
        settings=config(db)
        if not settings['enabled']:return
        try:
            from app.research_models import ResearchRun,ResearchRequisition
            rows=db.scalars(select(ResearchRun).where(ResearchRun.finished_at>=cutoff.replace(tzinfo=None),ResearchRun.status.in_(['completed','failed'])).order_by(ResearchRun.finished_at.desc()).limit(100)).all()
            if settings['routes']['research']!='off':
                for run in rows:
                    result=json.loads(run.result_json)if run.result_json else{};req=db.get(ResearchRequisition,run.requisition_id);request=json.loads(run.request_json);notes={n['intel_note_id']for n in result.get('event_notes')or[]if isinstance(n,dict)and type(n.get('intel_note_id'))is int and n['intel_note_id']>0 and not n.get('error')}
                    topic=req.topic if req else'Saved research';count=len(result.get('sources')or[])
                    from app.services.collection_planning import matching
                    priority=matching(db,'world',run.requisition_id);priority_id=priority.id if priority else None
                    item(db,'research',run.id,stamp(run.finished_at),topic+' · '+run.status,str(count)+' returned source record(s); '+str(len(notes))+' confirmed saved note reference(s). Completion alone does not establish current reporting or verified findings.','/static/automation.html',note_ids=sorted(notes),run_id=run.id,plan_id=(request.get('collection_priority')or{}).get('plan_id')or priority_id,severity='attention'if run.status=='failed'else'information')
        except Exception:errors.append('Research intake incomplete.')
        try:
            from app.services.event_tracking import EventNotice,EventEvidence,EventDossier,unpack as event_data
            rows=db.scalars(select(EventNotice).where(or_(EventNotice.created>=cutoff.timestamp(),EventNotice.reviewed_at>=cutoff.timestamp())).order_by(EventNotice.created.desc()).limit(100)).all()
            if settings['routes']['events']!='off':
                for source in rows:
                    data=event_data(source)
                    if data['kind']=='candidate_note':continue
                    dossier=db.get(EventDossier,source.dossier_id);evidence=db.get(EventEvidence,data['evidence_id'])
                    if not dossier:continue
                    from app.services.collection_planning import matching
                    priority=matching(db,'events',source.dossier_id)
                    notice=item(db,'events',source.id,source.reviewed_at or source.created,event_data(dossier)['title']+' · '+data['title'],'Event '+source.state+' review cue. '+data['reason'],'/static/events.html#'+source.dossier_id,source_id=source.id,plan_id=priority.id if priority else None,note_ids=[data['note_id']],quote=data['quote'],classification=data.get('classification','Not recorded'),severity='attention'if source.state=='reviewed'and data.get('escalates')else'information')
                    if source.state in {'withdrawn','rejected'}or not evidence or evidence.state!='reviewed':notice.availability='withdrawn'
            # Reconcile withdrawals even when the source notice predates this intake window.
            for saved in db.scalars(select(InboxItem).where(InboxItem.category=='events',InboxItem.availability=='available').order_by(InboxItem.created.desc()).limit(250)).all():
                source=db.get(EventNotice,unpack(saved).get('source_id'))
                if source and source.state in {'withdrawn','rejected'}:saved.availability='withdrawn'
        except Exception:errors.append('Event cue intake incomplete.')
        try:
            from app.services.market_automation import load
            market=load();when=stamp(market['last_finished'])if market.get('last_finished')else 0
            if settings['routes']['markets']!='off'and when>=cutoff.timestamp():
                for result in market.get('results',[])[:10]:
                    if not result.get('record_id'):continue
                    from app.services.collection_planning import matching
                    symbol=result['symbol'];priority=matching(db,'markets',symbol);direction={'bullish':'Potential rise','bearish':'Potential fall','neutral':'No clear directional bias','insufficient_evidence':'Insufficient evidence'}.get(result.get('direction'),'Assessment available')
                    item(db,'markets',str(result['record_id'])+':'+symbol,when,symbol+' · saved market outlook',direction+'. Low-confidence, uncalibrated source-based hypothesis; not a guaranteed return or a trading instruction. This completion was observed from the latest market cycle.','/static/markets.html',record_id=result['record_id'],symbol=symbol,plan_id=priority.id if priority else None)
        except Exception:errors.append('Market intake incomplete.')
        try:
            from app.services.operations_recovery import snapshot
            state=snapshot();active=set()
            for alert in state['alerts'][:50]:
                source_key='alert:'+alert['id'];active.add(source_key);old=cursor(db,source_key)
                if not old.get('active'):old={'active':True,'episode':str(uuid4())}
                if settings['routes']['services']!='off':item(db,'services',old['episode'],stamp_now,alert['title'],alert['message'],'/static/recovery.html',source_key=source_key,severity='attention',underlying_acknowledged=alert.get('acknowledged',False))
                set_cursor(db,source_key,old)
            for row in db.scalars(select(DeliveryCursor).where(DeliveryCursor.id.like('alert:%'))).all():
                old=unpack(row)
                if old.get('active')and row.id not in active:
                    old['active']=False;row.payload=pack(old);saved=db.get(InboxItem,digest('services',old['episode']))
                    if saved:saved.availability='resolved'
        except Exception:errors.append('Service alert intake incomplete; existing alerts were not resolved by this failed scan.')
        try:
            folder=Path(get_settings().data_directory)/'reports'
            paths=sorted(folder.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]
            if settings['routes']['briefs']!='off':
                for path in paths:
                    try:
                        data=json.loads(path.read_text(encoding='utf-8'));key=str(UUID(data['id']));created=stamp(data['created_at'])
                        if created<cutoff.timestamp()or not(folder/(key+'.pdf')).is_file():continue
                        item(db,'briefs',key,created,'Saved intelligence brief available','A generated PDF is available for '+str(data.get('start','unknown'))+' through '+str(data.get('end','unknown'))+'. Review its source and coverage qualifications before distribution.','/reports/'+key+'/pdf',report_id=key)
                    except (OSError,ValueError,KeyError,TypeError):continue
            for saved in db.scalars(select(InboxItem).where(InboxItem.category=='briefs')).all():
                report_key=str(UUID(unpack(saved)['report_id']));saved.availability='available'if(folder/(report_key+'.pdf')).is_file()and(folder/(report_key+'.json')).is_file()else'missing'
        except Exception:errors.append('PDF availability intake incomplete.')
        # Initial historical items stay in the inbox but cannot suddenly speak as new alerts.
        initialized=cursor(db,'intake').get('initialized',False)
        if not initialized:
            db.flush()
            for row in db.scalars(select(InboxItem)).all():value=unpack(row);value['automatic_eligible']=False;row.payload=pack(value)
        set_cursor(db,'intake',{'initialized':True,'last_scan':now().isoformat(),'errors':errors,'coverage':'Bounded last-day intake: latest 100 finished research runs, 100 recent event notices, 10 observed market results, 50 recovery alerts and 100 report manifests. Not a complete external archive.'});db.commit()

def quiet(settings,current=None):
    current=current or now()
    if current.timestamp()<settings.get('snooze_until',0):return True
    if not settings['quiet_enabled']:return False
    hour=current.astimezone(ZoneInfo(settings['timezone'])).hour;start=settings['quiet_start'];end=settings['quiet_end']
    return start<=hour<end if start<end else hour>=start or hour<end

def mode(row,settings):
    route=settings['routes'][row.category]
    if route=='off':return 'off'
    data=unpack(row)
    if data.get('plan_id')in settings['immediate_plan_ids']:return 'immediate'
    if row.category=='events'and data.get('severity')!='attention':return 'digest'
    return route

def make_digest():
    with LOCK,SessionLocal()as db:
        settings=config(db);local=now().astimezone(ZoneInfo(settings['timezone']))
        if not settings['enabled']or not settings['digest_enabled']or local.hour<settings['digest_hour']:return
        identity=local.date().isoformat();key=digest('digest',identity)
        if db.get(InboxItem,key):return
        rows=db.scalars(select(InboxItem).where(InboxItem.source_at>=now().timestamp()-86400,InboxItem.category!='digest',InboxItem.availability=='available',InboxItem.acknowledged_at.is_(None)).order_by(InboxItem.created.desc()).limit(100)).all()
        included=[r for r in rows if mode(r,settings)!='off']
        if not included:return
        titles=[unpack(r)['title']for r in included];summary=str(len(included))+' unacknowledged saved updates in this bounded daily digest. Generated '+local.strftime('%d %b %Y, %H:%M %Z')+'. Availability and review cues do not independently verify their claims.'
        item(db,'digest',identity,now().timestamp(),'Artemis daily update digest',summary,'/static/inbox.html',item_ids=[r.id for r in included],titles=titles,automatic_eligible=True);db.commit()

def expire():
    with LOCK,SessionLocal()as db:
        for row in db.scalars(select(DeliveryAttempt).where(DeliveryAttempt.state.in_(['leased','started']),DeliveryAttempt.lease_until<now().timestamp())).all():
            row.state='unconfirmed';value=unpack(row);value['error']='Listener receipt expired. Presentation or playback may have occurred; no automatic replay was queued.';row.payload=pack(value)
        db.commit()

def native_ready(client_id):
    from app.services.voice_hub import agent_state
    state=agent_state()
    return bool(state.get('online')and state.get('client_id')==str(client_id)and state.get('phase')=='wake'and state.get('stage_seconds',0)>=15 and not any(state.get(k)for k in ['muted','paused','playback_active','command_busy','awaiting_confirmation']))

class Lease(BaseModel):client_id:UUID;channel:Literal['desktop','speech']
def lease(body:Lease):
    if not native_ready(body.client_id):return None
    with LOCK,SessionLocal()as db:
        settings=config(db);stamp_now=now().timestamp()
        if not settings['enabled']or not settings[body.channel]or quiet(settings):return None
        attempts=db.scalars(select(DeliveryAttempt).where(DeliveryAttempt.created>=stamp_now-3600)).all()
        automatic=[a for a in attempts if a.channel in {'desktop','speech'}]
        if len(automatic)>=settings['max_per_hour']:return None
        if any(a.channel==body.channel and a.created>stamp_now-settings['cooldown_seconds']for a in automatic):return None
        if db.scalars(select(DeliveryAttempt).where(DeliveryAttempt.channel==body.channel,DeliveryAttempt.state.in_(['leased','started']))).first():return None
        rows=db.scalars(select(InboxItem).where(InboxItem.availability=='available',InboxItem.acknowledged_at.is_(None),InboxItem.source_at>=stamp_now-7200).order_by(InboxItem.created).limit(100)).all()
        rows.sort(key=lambda r:0 if unpack(r).get('severity')=='attention'else 1)
        for row in rows:
            data=unpack(row)
            if not data.get('automatic_eligible',True)or mode(row,settings)!='immediate':continue
            previous=db.scalars(select(DeliveryAttempt).where(DeliveryAttempt.item_id==row.id,DeliveryAttempt.channel==body.channel)).all()
            if previous and(not all(a.state=='deferred'for a in previous)or len(previous)>=2):continue
            token=str(uuid4());key=str(uuid4());value={'client_id':str(body.client_id),'token':token,'title':data['title'],'speech':'Artemis has a new '+{'briefs':'saved briefing','events':'event review update','markets':'market research update','services':'service attention item','research':'research update','digest':'daily update digest'}[row.category]+' available. Open your local inbox for details.','link':data['link'],'started_at':None,'completed_at':None,'maximum_until':stamp_now+600}
            db.add(DeliveryAttempt(id=key,item_id=row.id,channel=body.channel,state='leased',created=stamp_now,lease_until=stamp_now+90,payload=pack(value)));db.commit();return {'id':key,'channel':body.channel,**value}
        return None

class Receipt(BaseModel):
    client_id:UUID
    token:UUID
    state:Literal['heartbeat','started','completed','failed','cancelled','deferred']
def receipt(attempt_id:UUID,body:Receipt):
    with LOCK,SessionLocal()as db:
        row=db.get(DeliveryAttempt,str(attempt_id))
        if row is None:raise HTTPException(404,'Delivery attempt not found.')
        data=unpack(row)
        if data['client_id']!=str(body.client_id)or data['token']!=str(body.token):raise HTTPException(403,'Receipt does not match this listener delivery.')
        stamp_now=now().timestamp()
        if body.state=='heartbeat':
            if row.state in {'leased','started'}:row.lease_until=min(stamp_now+90,data['maximum_until'])
        elif row.state not in {'completed','failed','cancelled','deferred'}:
            if body.state=='deferred'and(row.state=='started'or data.get('started_at')):raise HTTPException(409,'Playback already began; it cannot be deferred as unplayed.')
            row.state=body.state
            if body.state=='started':data['started_at']=stamp_now
            if body.state=='completed':data['completed_at']=stamp_now
            row.payload=pack(data)
        db.commit();return {'state':row.state}

def same_origin(request:Request):
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(request.headers.get('origin')and request.headers['origin']!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use this Artemis origin for inbox and delivery changes.')
router=APIRouter(prefix='/delivery',dependencies=[Depends(same_origin)])
class Routes(BaseModel):
    research:Literal['immediate','digest','off']='digest'
    events:Literal['immediate','digest','off']='immediate'
    markets:Literal['immediate','digest','off']='digest'
    services:Literal['immediate','digest','off']='immediate'
    briefs:Literal['immediate','digest','off']='immediate'
    digest:Literal['immediate','digest','off']='immediate'
class Preferences(BaseModel):
    enabled:bool=True
    desktop:bool=False
    speech:bool=False
    quiet_enabled:bool=True
    quiet_start:int=Field(default=22,ge=0,le=23)
    quiet_end:int=Field(default=7,ge=0,le=23)
    timezone:Literal['America/Chicago','UTC']='America/Chicago'
    max_per_hour:int=Field(default=3,ge=1,le=12)
    cooldown_seconds:int=Field(default=120,ge=30,le=3600)
    digest_enabled:bool=True
    digest_hour:int=Field(default=7,ge=0,le=23)
    routes:Routes=Field(default_factory=Routes)
    immediate_plan_ids:list[UUID]=Field(default_factory=list,max_length=100)
    @model_validator(mode='after')
    def times(self):
        if self.quiet_enabled and self.quiet_start==self.quiet_end:raise ValueError('Choose different quiet-hour start/end hours, or disable quiet hours.')
        return self
@router.put('/settings')
def settings(body:Preferences):
    with LOCK,SessionLocal()as db:
        value=body.model_dump();value['immediate_plan_ids']=[str(x)for x in body.immediate_plan_ids];value['snooze_until']=config(db)['snooze_until'];save_config(db,value);db.commit();return value
@router.post('/native/lease')
def native_lease(body:Lease):return lease(body)
@router.post('/attempts/{attempt_id}/authorize')
def authorize(attempt_id:UUID,body:Receipt):
    with SessionLocal()as db:
        row=db.get(DeliveryAttempt,str(attempt_id))
        if row is None:return {'allowed':False}
        value=unpack(row)
        if value['client_id']!=str(body.client_id)or value['token']!=str(body.token):raise HTTPException(403,'Listener delivery does not match.')
        if row.channel=='briefing':return {'allowed':row.state in {'leased','started'}}
        source=db.get(InboxItem,row.item_id);settings=config(db)
        return {'allowed':bool(row.state in {'leased','started'}and settings['enabled']and settings[row.channel]and not quiet(settings)and source and source.availability=='available'and source.acknowledged_at is None and mode(source,settings)=='immediate')}
@router.post('/attempts/{attempt_id}/receipt')
def native_receipt(attempt_id:UUID,body:Receipt):return receipt(attempt_id,body)
class Snooze(BaseModel):minutes:int=Field(default=60,ge=0,le=1440)
@router.post('/snooze')
def snooze(body:Snooze):
    with LOCK,SessionLocal()as db:value=config(db);value['snooze_until']=now().timestamp()+body.minutes*60 if body.minutes else 0;save_config(db,value);db.commit();return {'until':value['snooze_until']}
@router.post('/items/{item_id}/acknowledge')
def acknowledge(item_id:str):
    with LOCK,SessionLocal()as db:
        row=db.get(InboxItem,item_id)
        if row is None:raise HTTPException(404,'Inbox item not found.')
        if row.acknowledged_at is None:row.acknowledged_at=now().timestamp()
        db.commit();return {'acknowledged':True}
@router.get('/status')
def status(include_acknowledged:bool=False):
    with SessionLocal()as db:
        query=select(InboxItem)
        if not include_acknowledged:query=query.where(InboxItem.acknowledged_at.is_(None))
        rows=db.scalars(query.order_by(InboxItem.created.desc()).limit(100)).all();attempts=db.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.created.desc()).limit(200)).all()
        unread=db.scalar(select(func.count()).select_from(InboxItem).where(InboxItem.availability=='available',InboxItem.acknowledged_at.is_(None)))or 0
        return {'settings':config(db),'unacknowledged':unread,'intake':cursor(db,'intake'),'items':[{'id':r.id,'category':r.category,'created':r.created,'source_at':r.source_at,'availability':r.availability,'acknowledged_at':r.acknowledged_at,**unpack(r),'deliveries':[{'id':a.id,'channel':a.channel,'state':a.state,'created':a.created,**{k:v for k,v in unpack(a).items()if k in {'started_at','completed_at','error'}}}for a in attempts if a.item_id==r.id]}for r in rows]}

def start_brief(request_id,report_id=None,play=False):
    from app.services.voice_hub import latest_brief,brief_text,agent_state
    from app.services.voice_sessions import create
    with LOCK:
        with SessionLocal()as db:
            previous=cursor(db,'briefing:'+str(request_id))
            if previous:return previous
        manifest,_=latest_brief(report_id)
        if not manifest:raise HTTPException(404,'The selected saved PDF is not available. Generate or select an existing brief first.')
        key=str(UUID(manifest['id']))
        try:created=stamp(manifest['created_at'])
        except (KeyError,ValueError,TypeError):raise HTTPException(422,'Saved report creation metadata is incomplete. Open the PDF directly instead.')from None
        reply=brief_text(key)
        if reply.get('report_id')!=key:raise HTTPException(409,'The selected brief changed or became unavailable. Select it again.')
        session=create(title='Briefing conversation · '+manifest.get('end','saved report')[:40],report_id=key)
        reply.update(session_id=session['id'],session_link='/static/voice-session.html#'+session['id'],open_path='/reports/'+key+'/pdf')
        with SessionLocal()as db:
            saved=item(db,'briefs',key,created,'Saved intelligence brief available','An operator selected this saved PDF for a briefing session. Reading is bounded to its key findings; source claims remain unverified.','/reports/'+key+'/pdf',report_id=key,automatic_eligible=False)
            state=agent_state();client=state.get('client_id')
            if play and state.get('online')and client:
                identity=str(uuid4());token=str(uuid4());stamp_now=now().timestamp();value={'client_id':client,'token':token,'maximum_until':stamp_now+3600,'started_at':None,'completed_at':None,'session_id':session['id']}
                db.add(DeliveryAttempt(id=identity,item_id=saved.id,channel='briefing',state='leased',created=stamp_now,lease_until=stamp_now+90,payload=pack(value)));reply['delivery']={'id':identity,'client_id':client,'token':token,'channel':'briefing'}
            set_cursor(db,'briefing:'+str(request_id),reply);db.commit();return reply

def voice(text,request_id,play=False):
    if text=='start briefing session':return start_brief(request_id,play=play)
    if text=='snooze notifications':snooze(Snooze(minutes=60));return {'speech':'Automatic local notifications snoozed for one hour. Research continues and updates remain in your inbox.','link':'/static/inbox.html'}
    if text=='resume notifications':snooze(Snooze(minutes=0));return {'speech':'Notification snooze cleared. Saved quiet hours and channel settings still apply.','link':'/static/inbox.html'}
    with SessionLocal()as db:
        query=select(InboxItem).where(InboxItem.availability=='available',InboxItem.acknowledged_at.is_(None))
        if text=='brief me on what changed':query=query.where(InboxItem.category.in_(['events','research','digest']))
        rows=db.scalars(query.order_by(InboxItem.created.desc()).limit(5)).all();values=[unpack(r)for r in rows]
    return {'speech':('Latest saved inbox updates. '+' '.join(v['title']+'. '+v['summary'][:220]for v in values)if values else'No unacknowledged saved updates were found in that inbox view.')+' Source reporting, review cues and market hypotheses are not independently verified findings. Reading does not acknowledge an update.','link':'/static/inbox.html'}

def dispatch_reply(request_id,reply):
    from app.services.voice_hub import agent_state,control
    with LOCK,SessionLocal()as db:
        marker='dispatch:'+str(request_id)
        if cursor(db,marker):return 'already_queued'
        if not agent_state().get('online'):return 'listener_offline'
        control('reply',reply);set_cursor(db,marker,{'queued':True});db.commit();return 'queued'

class Brief(BaseModel):report_id:UUID|None=None;request_id:UUID=Field(default_factory=uuid4);speak:bool=True
@router.post('/briefing')
def briefing(body:Brief):
    from app.services.voice_hub import agent_state,control
    reply=start_brief(str(body.request_id),str(body.report_id)if body.report_id else None,body.speak)
    if body.speak:reply={**reply,'playback':dispatch_reply(body.request_id,reply)}
    return reply

class Worker:
    def __init__(self):self.event=Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-proactive-delivery');self.thread.start()
    def stop(self):
        self.event.set()
        if self.thread:self.thread.join(timeout=10)
    def tick(self):expire();intake();make_digest()
    def loop(self):
        while not self.event.is_set():
            try:self.tick()
            except Exception:LOG.exception('Proactive inbox intake failed; delivery claims remain separate from acknowledgement')
            self.event.wait(30)
worker=Worker()
