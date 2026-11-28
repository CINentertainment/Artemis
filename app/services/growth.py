"""Persistent marketing worker using the existing encrypted commerce store.

Exact immutable editorial-bank copy can run under a standing policy. No DMs,
comments, arbitrary model copy, ad spend or external-page instructions execute.
"""
import json
import time
from threading import Event, RLock, Thread
from urllib.parse import quote
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from app.database import SessionLocal
from app.services import commerce as store, growth_core as core
from app.services.encryption import protect_text

LOCK=store.LOCK
TICK=RLock()
CHANNELS=('member','organization')
PREFIX='growth:'

def read(db,key,default=None):return store.read(db,PREFIX+key,default)
def put(db,key,kind,value):return store.put(db,PREFIX+key,'growth_'+kind,value)
def rows(db,kind,limit=500):return store.records(db,'growth_'+kind,limit)
def defaults():
    return dict(enabled=False,mode='draft',research=True,hour_utc=16,minute=0,revision=0,
                policy_hash=None,policy_at=None,next_research=0,query_cursor=0,channels={})
def cfg(db):return {**defaults(),**read(db,'settings')}
def channel_default(kind):
    return dict(channel=kind,connected=False,enabled=False,token='',app_id='',secret='',binding=None,
                index=0,next_at=0,issue=None,connected_at=None)
def chan(c,kind):return {**channel_default(kind),**c['channels'].get(kind,{})}
def event(db,title,channel=None):
    put(db,'event:'+uuid4().hex,'event',{'at':time.time(),'title':title,'channel':channel})
def save(db,c):put(db,'settings','config',c)
def public_channel(ch):
    return {k:v for k,v in ch.items() if k not in ('token','secret','app_id')}
def status():
    with LOCK,SessionLocal() as db:
        c=cfg(db)
        public={k:v for k,v in c.items() if k!='channels'}
        public['channels']={k:public_channel(chan(c,k)) for k in CHANNELS}
        posts=rows(db,'post',100)
        return dict(policy=public,bank_hash=core.BANK_HASH,worker_alive=bool(worker.thread and worker.thread.is_alive()),
                    posts=posts,candidates=rows(db,'candidate',100),events=rows(db,'event',30),
                    preview={k:[core.draft(i,k) for i in range(len(core.LESSONS))] for k in CHANNELS},
                    cycle=read(db,'cycle'),limits={'posts_per_week_per_channel':3,'campaign_posts_per_channel':len(core.LESSONS),
                    'acquisition_attribution':'unknown','download_delivery':'unverified',
                    'research':'Snippet triage only; external sources never feed publishing instructions.'})

def product_ready(db):
    # Refresh is performed by Commerce. No stale or missing storefront product data is assumed live.
    cycle=store.read(db,'cycle')
    if not cycle or cycle.get('state')!='completed':return False
    try:
        finished = __import__('datetime').datetime.fromisoformat(cycle['finished_at'])
        if finished.tzinfo is None or time.time()-finished.timestamp()>86400:return False
    except (ValueError,TypeError,KeyError):return False
    # Product identity comes from the exact already verified storefront adapter.
    return any(p.get('name')==core.PRODUCT_NAME and p.get('published') is True and
               (p.get('short_url') or '').split('?')[0].rstrip('/') in (core.PRODUCT_URL,'https://artemisbycin.gumroad.com/l/kofxaz')
               for p in store.records(db,'product',200))

def discover(c):
    from app.connectors.web_search import search_web, normalize_result_url
    queries=(
      'research analyst community source evaluation collection planning discussion',
      'OSINT analyst research workflow template evidence ledger',
      'intelligence briefing template research workflow guide',
      'research documentation analyst workflow challenges discussion')
    q=queries[c['query_cursor']%len(queries)]
    results=search_web(q,limit=6,categories='general')
    with LOCK,SessionLocal() as db:
        current=cfg(db)
        if not current['enabled'] or not current['research'] or current['revision']!=c['revision']:return
        for r in results[:6]:
            normalized=normalize_result_url(r.url)
            row=core.candidate({'url':normalized or '', 'title':r.title,'snippet':r.snippet,'collected_at':time.time()})
            if row:
                import hashlib
                put(db,'candidate:'+hashlib.sha256(row['url'].encode()).hexdigest(),'candidate',row)
        current['query_cursor']+=1;current['next_research']=time.time()+86400
        save(db,current);event(db,'Daily audience/resource search completed; candidates remain unverified.')
        retained=list(db.scalars(select(store.CommerceRecord).where(store.CommerceRecord.kind=='growth_candidate').order_by(store.CommerceRecord.created.desc())))
        for r in retained[100:]:db.delete(r)
        db.commit()

def notify(db,identity,title,summary,link='/static/growth.html'):
    try:
        from app.services.proactive_delivery import item
        item(db,'services','growth:'+identity,time.time(),title,summary,link,severity='attention')
    except ImportError:
        pass

def pause_channel(db,c,kind,message):
    ch=chan(c,kind);ch['enabled']=False;ch['issue']=message;c['channels'][kind]=ch;save(db,c);event(db,message,kind)
    notify(db,kind+':'+str(time.time()),'Growth autopilot needs attention',kind+': '+message)

def complete(db,c,job,state,message=None,pid=None):
    job.update(state=state,finished_at=time.time(),message=message)
    if pid:job.update(provider_id=pid,url=core.post_url(pid))
    put(db,'post:'+job['id'],'post',job)
    ch=chan(c,job['channel'])
    if state in ('sent','skipped','cancelled'):
        ch['index']=max(ch['index'],job['index']+1)
        ch['next_at']=core.next_slot(time.time(),c['hour_utc'],(c['minute']+(30 if job['channel']=='organization' else 0))%60)
    c['channels'][job['channel']]=ch;save(db,c)
    event(db,'Post '+state+((': '+message) if message else ''),job['channel'])

def prepare(kind):
    with LOCK,SessionLocal() as db:
        c=cfg(db);ch=chan(c,kind)
        if not c['enabled'] or not ch['enabled'] or not ch['connected']:return
        if ch['index']>=len(core.LESSONS):
            pause_channel(db,c,kind,'Editorial campaign complete. Add a new reviewed content bank before continuing.');db.commit();return
        existing=[p for p in rows(db,'post') if p['channel']==kind and p['state'] in ('draft','queued','sending','uncertain')]
        if existing:return
        if not ch['next_at']:ch['next_at']=core.next_slot(time.time(),c['hour_utc'],(c['minute']+(30 if kind=='organization' else 0))%60)
        d=core.draft(ch['index'],kind);key=kind+':'+core.CAMPAIGN+':'+d['key']
        if read(db,'post:'+key):
            ch['index']+=1;c['channels'][kind]=ch;save(db,c);db.commit();return
        job=dict(id=key,channel=kind,index=ch['index'],key=d['key'],text=d['text'],created_at=time.time(),due_at=ch['next_at'],
                 author=ch['binding']['author'],bank_hash=core.BANK_HASH,revision=c['revision'],
                 state='queued' if c['mode']=='auto' else 'draft',provider_id=None,url=None,metrics=None)
        put(db,'post:'+key,'post',job);c['channels'][kind]=ch;save(db,c)
        try:db.commit()
        except IntegrityError:db.rollback() # Another process inserted this exact campaign slot.

def dispatch(job_id):
    with LOCK,SessionLocal() as db:
        c=cfg(db);job=read(db,'post:'+job_id)
        if not job or job['state']!='queued' or job['due_at']>time.time():return
        ch=chan(c,job['channel'])
        if not c['enabled'] or not ch['enabled'] or not ch['connected']:return
        if job['revision']!=c['revision'] or job['author']!=ch['binding']['author'] or job['bank_hash']!=core.BANK_HASH:
            complete(db,c,job,'cancelled','Policy or identity changed.');db.commit();return
        if job['due_at']<time.time()-3600:
            complete(db,c,job,'skipped','Missed posting window; no backlog publish.');db.commit();return
        if c['policy_hash']!=core.BANK_HASH:
            pause_channel(db,c,job['channel'],'The standing content policy needs updating.');db.commit();return
        if not product_ready(db):
            # Avoid rapid retry: next worker check after Commerce refresh; no provider send attempted.
            put(db,'cycle','cycle',{'at':time.time(),'state':'waiting','message':'Waiting for a completed Commerce sync with the published toolkit.'});db.commit();return
        if ch['binding']['expires_at']<=time.time()+300:
            pause_channel(db,c,job['channel'],'LinkedIn token expires shortly. Reconnect locally.');db.commit();return
        # A persisted compare-and-swap claim prevents duplicate POSTs across processes.
        row=db.get(store.CommerceRecord,PREFIX+'post:'+job_id);old=row.payload
        job.update(state='sending',attempted_at=time.time())
        body=protect_text(json.dumps(job,ensure_ascii=False))
        result=db.execute(update(store.CommerceRecord).where(store.CommerceRecord.id==row.id,store.CommerceRecord.payload==old).values(payload=body))
        if result.rowcount!=1:db.rollback();return
        db.commit()
        # Hold the policy lock at the external side-effect boundary. Pause may wait for an in-flight request.
        try:
            verified=core.verify(ch['token'],ch['app_id'],ch['secret'],job['channel'],
                                 ch['binding']['author'].split(':')[-1] if job['channel']=='organization' else '')
            if verified['author']!=job['author'] or verified['member']!=ch['binding']['member']:
                raise core.ProviderError('Publishing identity changed. Reconnect locally.','identity')
            # Re-read saved policy after verification I/O; a pause from another instance must win.
            db.commit();current=cfg(db);latest=chan(current,job['channel'])
            if not current['enabled'] or not latest['enabled'] or current['revision']!=job['revision']:
                complete(db,current,job,'cancelled','Policy changed before provider dispatch.');db.commit();return
            with core.client(ch['token']) as client:pid=core.publish(client,job['author'],job['text'])
        except core.UncertainSend as exc:
            c=cfg(db);complete(db,c,job,'uncertain',str(exc));pause_channel(db,c,job['channel'],str(exc));db.commit();return
        except core.ProviderError as exc:
            c=cfg(db);complete(db,c,job,'failed',str(exc));pause_channel(db,c,job['channel'],str(exc));db.commit();return
        c=cfg(db);complete(db,c,job,'sent',pid=pid);db.commit()

def refresh_metrics():
    with LOCK,SessionLocal() as db:
        c=cfg(db)
        if not c['enabled']:return
        for job in rows(db,'post',100):
            if job['state']!='sent' or not job.get('provider_id') or job.get('metrics_checked',0)>time.time()-86400:continue
            ch=chan(c,job['channel'])
            if not ch['connected']:continue
            try:
                with core.client(ch['token']) as client:job['metrics']=core.metrics(client,job['provider_id'])
                job['metrics_status']='available'
            except core.ProviderError:
                job['metrics_status']='unavailable: API permission or provider response does not allow this read'
            job['metrics_checked']=time.time();put(db,'post:'+job['id'],'post',job);db.commit();break

def run_cycle():
    if not TICK.acquire(blocking=False):return
    try:
        with LOCK,SessionLocal() as db:
            c=cfg(db)
            # A crashed in-flight POST is never retried, even after restart.
            for j in rows(db,'post'):
                if j['state']=='sending' and j.get('attempted_at',0)<time.time()-180:
                    complete(db,c,j,'uncertain','Interrupted publish. Check LinkedIn; no automatic resend.')
                    pause_channel(db,c,j['channel'],'Interrupted publish needs resolution.')
            db.commit()
            if not c['enabled']:return
            put(db,'cycle','cycle',{'at':time.time(),'state':'running','message':'Processing saved policy.'})
            for kind in CHANNELS:
                ch=chan(c,kind)
                if ch['connected'] and ch['enabled'] and ch['binding']['expires_at']<=time.time()+300:
                    pause_channel(db,c,kind,'LinkedIn token expires shortly. Reconnect locally.')
                elif ch['connected'] and ch['enabled'] and ch['binding']['expires_at']<=time.time()+86400:
                    notify(db,kind+':expiry:'+str(ch['binding']['expires_at']),'LinkedIn token expires within 24 hours',kind+': reconnect locally before token expiry.')
            db.commit()
            research_due=c['research'] and c['next_research']<=time.time()
            if research_due:
                # Reserve next window before I/O. Failure never hammers search on every worker tick.
                c['next_research']=time.time()+3600;save(db,c);db.commit()
        if research_due:
            try:discover(c)
            except Exception:
                with LOCK,SessionLocal() as db:event(db,'Audience search unavailable; next attempt in one hour.');db.commit()
        for kind in CHANNELS:prepare(kind)
        with LOCK,SessionLocal() as db:jobs=[j['id'] for j in rows(db,'post') if j['state']=='queued']
        for key in jobs:dispatch(key)
        refresh_metrics()
        with LOCK,SessionLocal() as db:
            prior=read(db,'cycle')
            if prior.get('state')!='waiting':
                put(db,'cycle','cycle',{'at':time.time(),'state':'completed','message':'Saved policy cycle finished.'})
            retained=list(db.scalars(select(store.CommerceRecord).where(store.CommerceRecord.kind=='growth_event').order_by(store.CommerceRecord.created.desc())))
            for r in retained[200:]:db.delete(r)
            db.commit()
    except Exception:
        # Tokens, request bodies and third-party content never enter logs/error responses.
        with LOCK,SessionLocal() as db:
            put(db,'cycle','cycle',{'at':time.time(),'state':'attention','message':'Cycle interrupted. Check channel status; in-flight posts are not automatically resent.'});db.commit()
    finally:TICK.release()

class Worker:
    def __init__(self):self.thread=None;self.halt=Event()
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.halt.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-growth');self.thread.start()
    def stop(self):
        self.halt.set()
        if self.thread:self.thread.join(timeout=2)
    def loop(self):
        while not self.halt.is_set():
            try:run_cycle()
            except Exception:pass
            self.halt.wait(30)
worker=Worker()
class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try:return await original(request)
            except RequestValidationError:
                raise HTTPException(422,'Invalid growth form. Check field lengths, channel and schedule; submitted values are not echoed.') from None
        return handler

router=APIRouter(prefix='/growth',tags=['Growth autopilot'],route_class=PrivateValidationRoute)

@router.get('/status')
def api_status():return status()

class ConnectInput(BaseModel):
    channel:str=Field(pattern='^(member|organization)$')
    token:SecretStr=Field(min_length=10,max_length=4000)
    app_id:str=Field(min_length=3,max_length=150,pattern='^[A-Za-z0-9_-]+$')
    secret:SecretStr=Field(min_length=3,max_length=1000)
    organization_id:str=Field(default='',max_length=25)

@router.post('/connect')
def connect(body:ConnectInput,request:Request):
    store.guard(request)
    try:binding=core.verify(body.token.get_secret_value().strip(),body.app_id,body.secret.get_secret_value().strip(),body.channel,body.organization_id)
    except core.ProviderError as exc:raise HTTPException(400,str(exc)) from None
    with LOCK,SessionLocal() as db:
        c=cfg(db);ch=chan(c,body.channel)
        if ch['binding'] and ch['binding']['author']!=binding['author']:
            raise HTTPException(409,'Disconnect this channel before binding a different identity.')
        ch.update(connected=True,enabled=False,token=body.token.get_secret_value().strip(),app_id=body.app_id,
                  secret=body.secret.get_secret_value().strip(),binding=binding,issue=None,connected_at=time.time())
        c['channels'][body.channel]=ch;c['revision']+=1
        save(db,c);event(db,'LinkedIn identity verified. Choose policy to enable this channel.',body.channel);db.commit()
    return public_channel(ch)

class ChannelInput(BaseModel):channel:str=Field(pattern='^(member|organization)$')
@router.post('/disconnect')
def disconnect(body:ChannelInput,request:Request):
    store.guard(request)
    with LOCK,SessionLocal() as db:
        c=cfg(db);ch=chan(c,body.channel)
        ch.update(token='',secret='',app_id='',connected=False,enabled=False,issue='Disconnected locally.')
        c['channels'][body.channel]=ch;c['revision']+=1;save(db,c);event(db,'Channel disconnected; history retained.',body.channel);db.commit()
    return {'disconnected':True}

class PolicyInput(BaseModel):
    enabled:bool=False
    mode:str=Field(default='draft',pattern='^(draft|auto)$')
    research:bool=True
    member:bool=False
    organization:bool=False
    hour_utc:int=Field(default=16,ge=0,le=23)
    minute:int=Field(default=0,ge=0,le=29)
    bank_hash:str=Field(min_length=64,max_length=64)
    acknowledge_public_posting:bool=False

@router.put('/policy')
def policy(body:PolicyInput,request:Request):
    store.guard(request)
    if body.bank_hash!=core.BANK_HASH:raise HTTPException(409,'Content bank changed. Refresh and review the current campaign.')
    if body.enabled and (body.member or body.organization) and not body.acknowledge_public_posting:
        raise HTTPException(400,'Confirm the exact accounts and standing content policy before enabling publishing.')
    with LOCK,SessionLocal() as db:
        c=cfg(db)
        for kind in CHANNELS:
            ch=chan(c,kind);chosen=getattr(body,kind)
            if chosen and not ch['connected']:raise HTTPException(400,'Connect '+kind+' before enabling it.')
            if chosen and ch['binding']['expires_at']<=time.time()+300:raise HTTPException(400,'Reconnect '+kind+'; token expired or expires shortly.')
            if chosen and any(j['channel']==kind and j['state'] in ('sending','uncertain') for j in rows(db,'post')):
                raise HTTPException(409,'Resolve the uncertain '+kind+' post before enabling it.')
            ch.update(enabled=chosen,issue=None,next_at=core.next_slot(time.time(),body.hour_utc,body.minute+(30 if kind=='organization' else 0)))
            c['channels'][kind]=ch
        # Cancel old unattempted work, so changed schedules or policy never send stale drafts.
        for j in rows(db,'post'):
            if j['state'] in ('draft','queued'):
                complete(db,c,j,'cancelled','Policy updated before dispatch.')
        c.update(enabled=body.enabled,mode=body.mode,research=body.research,hour_utc=body.hour_utc,minute=body.minute,
                 policy_hash=core.BANK_HASH,policy_at=time.time(),revision=c['revision']+1)
        save(db,c);event(db,'Standing campaign policy saved: '+body.mode);db.commit()
    return {'saved':True}

@router.post('/pause')
def pause(request:Request):
    store.guard(request)
    with LOCK,SessionLocal() as db:
        c=cfg(db);c['enabled']=False;save(db,c);event(db,'All growth activity paused.');db.commit()
    return {'paused':True,'note':'An already dispatched provider request may finish.'}

class PostInput(BaseModel):
    id:str=Field(min_length=1,max_length=200)
    action:str=Field(pattern='^(approve|skip|resolve_absent|resolve_found)$')
    provider_id:str=Field(default='',max_length=100)
    reason:str=Field(default='',max_length=1000)

@router.post('/post')
def post_action(body:PostInput,request:Request):
    store.guard(request)
    with LOCK,SessionLocal() as db:
        c=cfg(db);j=read(db,'post:'+body.id)
        if not j:raise HTTPException(404,'Post not found.')
        if body.action=='approve':
            if j['state']!='draft' or j['revision']!=c['revision']:raise HTTPException(409,'Only current draft posts may be approved.')
            j.update(state='queued',due_at=max(time.time()+60,j['due_at']));put(db,'post:'+j['id'],'post',j)
        elif body.action=='skip':
            if j['state'] not in ('draft','queued','failed'):raise HTTPException(409,'This post cannot be skipped.')
            complete(db,c,j,'skipped','Skipped by operator.')
        else:
            if j['state']!='uncertain' or len(body.reason.strip())<10:raise HTTPException(409,'An uncertain post and a review reason are required.')
            if body.action=='resolve_found':
                if not core.valid_post_id(body.provider_id):raise HTTPException(400,'Use the LinkedIn post URN.')
                ch=chan(c,j['channel'])
                try:
                    with core.client(ch['token']) as client:data=core.get_json(client,'/rest/posts/'+quote(body.provider_id,safe=''))
                    if data.get('author')!=j['author'] or data.get('commentary')!=j['text']:raise core.ProviderError('Post author or content does not match.')
                except core.ProviderError as exc:raise HTTPException(400,str(exc)) from None
                complete(db,c,j,'sent',body.reason,body.provider_id)
            else:complete(db,c,j,'skipped','Operator checked feed; skipped permanently. '+body.reason)
        event(db,'Operator action: '+body.action,j['channel']);db.commit()
    return {'saved':True}

@router.get('/summary')
def summary():
    s=status();posts=s['posts'];sent=[p for p in posts if p['state']=='sent']
    text=('Artemis growth activity\n\nConfirmed published posts: '+str(len(sent))+'\n'
          'Research candidates retained: '+str(len(s['candidates']))+' (unverified snippets)\n'
          'Sales attribution, impressions and clicks: unknown\nNo DMs, comments, ad spend or paid campaigns.\n\n')
    text+='\n'.join(p['channel']+' | '+p['key']+' | '+p['state']+' | '+str(p.get('url') or '') for p in posts)
    return Response(text,media_type='text/plain',headers={'Content-Disposition':'attachment; filename="Artemis-Growth-Activity.txt"'})
