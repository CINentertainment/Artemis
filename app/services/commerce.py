"""Persistent commercial launch worker. All Gumroad operations are read-only."""
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from threading import Event, RLock, Thread
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import String, Text, Float, select
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, SessionLocal
from app.services.encryption import protect_text, unprotect_text
from app.services import commerce_core as core

LOG = logging.getLogger(__name__)
LOCK = RLock()
CYCLE = RLock()

class CommerceRecord(Base):
    __tablename__ = 'commerce_launch_records'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    created: Mapped[float] = mapped_column(Float, index=True)
    payload: Mapped[str] = mapped_column(Text)

def stamp():
    return datetime.now(timezone.utc).isoformat()

def unpack(row):
    return json.loads(unprotect_text(row.payload))

def read(db, key, default=None):
    row = db.get(CommerceRecord, key)
    return unpack(row) if row else (default if default is not None else {})

def put(db, key, kind, value):
    row = db.get(CommerceRecord, key)
    body = protect_text(json.dumps(value, ensure_ascii=False))
    if row:
        row.payload = body
    else:
        db.add(CommerceRecord(id=key, kind=kind, created=time.time(), payload=body))

def records(db, kind, limit=5000):
    return [unpack(r) for r in db.scalars(select(CommerceRecord).where(
        CommerceRecord.kind == kind).order_by(CommerceRecord.created.desc()).limit(limit))]

def defaults():
    return dict(enabled=False, discovery=False, interval_minutes=60, next_at=0,
                token='', account=None, revision=0, cursor=None, query_cursor=0, discovery_next=0)

def settings(db):
    return {**defaults(), **read(db, 'settings')}

def status():
    with LOCK, SessionLocal() as db:
        cfg = settings(db)
        sales = records(db, 'sale', 50001)
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        return dict(storefront=core.STORE, enabled=cfg['enabled'], discovery=cfg['discovery'],
                    token_configured=bool(cfg['token']), account=cfg['account'],
                    interval_minutes=cfg['interval_minutes'], next_at=cfg['next_at'],
                    worker_alive=bool(worker.thread and worker.thread.is_alive()),
                    cycle=read(db, 'cycle'), totals=core.totals(sales),
                    last_24_hours=core.totals(sales, since),
                    account_coverage='Stored records from completed or bounded provider scans; no payout reconciliation',
                    products=records(db, 'product', 200), opportunities=records(db, 'opportunity', 100),
                    recent_sales=sales[:50], launch=core.launch_copy(),
                    approval=read(db,'approval'), history=records(db,'history',30),
                    supported='Scheduled read-only sales reconciliation, opportunity searches, product export and local executive summary',
                    not_connected='Product publication, outreach, social posting, customer service and acquisition attribution are not automated in this package. Gumroad hosts checkout and file delivery after you publish.')

def binding(db, revision):
    cfg = settings(db)
    if cfg['revision'] != revision or not cfg['enabled']:
        raise core.ProviderError('Commercial cycle stopped because its saved policy changed.')
    return cfg

def reconcile(cfg):
    revision = cfg['revision']
    with core.connect(cfg['token']) as client:
        account = core.verify_account(core.request(client, 'user'))
        if cfg['account'] != account:
            raise core.ProviderError('Provider identity changed; reconnect locally.')
        products = core.request(client, 'products').get('products')
        if not isinstance(products,list) or len(products)>200:
            raise core.ProviderError('Product response is invalid or exceeds 200 records.')
        products = [core.product_record(p) for p in products]
        with LOCK, SessionLocal() as db:
            binding(db, revision)
            for p in products:
                put(db, 'product:' + hashlib.sha256(p['id'].encode()).hexdigest(), 'product', p)
            db.commit()
        cursor = cfg['cursor']
        seen = set()
        count = 0
        for _ in range(10):
            with LOCK, SessionLocal() as db:
                binding(db, revision)
            if cursor in seen:
                raise core.ProviderError('Provider repeated its continuation key.')
            seen.add(cursor)
            data = core.request(client, 'sales', {'page_key':cursor} if cursor else {})
            page = data.get('sales')
            if not isinstance(page,list) or len(page)>100:
                raise core.ProviderError('Invalid sales page or page exceeds 100 records.')
            verified = [core.sale_record(s, stamp()) for s in page]
            after = core.next_key(data)
            with LOCK, SessionLocal() as db:
                current = binding(db, revision)
                existing_count = len(records(db,'sale',50001))
                if existing_count + sum(db.get(CommerceRecord,'sale:'+hashlib.sha256(s['id'].encode()).hexdigest()) is None for s in verified) > 50000:
                    raise core.ProviderError('Local 50,000-sale bound reached. Increase coverage through a reviewed update.')
                for s in verified:
                    put(db, 'sale:' + hashlib.sha256(s['id'].encode()).hexdigest(), 'sale', s)
                current['cursor'] = after
                put(db, 'settings','config',current)
                db.commit()
            count += len(page)
            cursor = after
            if not cursor:
                break
        return dict(records_refreshed=count, scan_complete=cursor is None,
                    continuation_pending=cursor is not None,
                    coverage='Ten pages maximum per cycle; continuation resumes next cycle. A finished scan starts over next cycle to refresh refunds and disputes.')

def discover(cfg):
    # Search result snippets are leads for investigation, never demand proof or verified contacts.
    from app.connectors.web_search import search_web, normalize_result_url
    query = core.QUERIES[cfg['query_cursor'] % len(core.QUERIES)]
    results = search_web(query, limit=6, categories='general')
    found = 0
    with LOCK, SessionLocal() as db:
        current = binding(db, cfg['revision'])
        for result in results:
            url = normalize_result_url(result.url)
            if not url:
                continue
            key = 'opportunity:' + hashlib.sha256(url.encode()).hexdigest()
            old = read(db,key)
            put(db,key,'opportunity',dict(url=url,title=result.title[:500],
                snippet=result.snippet[:1200], query=query, collected_at=stamp(),
                state=old.get('state','unqualified'), reason=old.get('reason',''),
                evidence_limit='Search result snippet only; not independently read or verified. No contact or buying intent inferred.'))
            found += 1
        current['query_cursor'] += 1
        current['discovery_next'] = time.time()+86400
        put(db,'settings','config',current)
        # Bound retained search records without touching reviewed choices.
        rows=list(db.scalars(select(CommerceRecord).where(CommerceRecord.kind=='opportunity').order_by(CommerceRecord.created.desc())))
        for row in rows[500:]:
            if unpack(row).get('state') == 'unqualified':
                db.delete(row)
        db.commit()
    return dict(query=query, candidates_refreshed=found, status='unqualified search candidates')

def run_cycle():
    if not CYCLE.acquire(blocking=False):
        return
    try:
        with LOCK, SessionLocal() as db:
            cfg=settings(db)
            if not cfg['enabled'] or cfg['next_at']>time.time():
                return
            cycle=dict(started_at=stamp(),state='running',errors=[],sales=None,discovery=None)
            put(db,'cycle','state',cycle)
            db.commit()
        try:
            if cfg['token']:
                cycle['sales']=reconcile(cfg)
            else:
                cycle['errors'].append('Sales reconciliation requires a verified local Gumroad token.')
            if cfg['discovery'] and cfg['discovery_next']<=time.time():
                try:
                    cycle['discovery']=discover(cfg)
                except Exception:
                    cycle['errors'].append('Opportunity search failed; check SearXNG. No new candidate evidence claimed.')
        except core.ProviderError as exc:
            cycle['errors'].append(str(exc))
        except Exception:
            # Do not serialize exception repr; provider requests may carry credentials.
            cycle['errors'].append('Commercial cycle failed locally. No completed sale or outreach is inferred.')
        cycle['finished_at']=stamp()
        cycle['state']='partial' if cycle['errors'] else 'completed'
        with LOCK, SessionLocal() as db:
            current=settings(db)
            if current['revision'] == cfg['revision']:
                current['next_at']=time.time()+current['interval_minutes']*60
                put(db,'settings','config',current)
            else:
                cycle['state']='cancelled'
            put(db,'cycle','state',cycle)
            put(db,'history:'+uuid4().hex,'history',cycle)
            history=list(db.scalars(select(CommerceRecord).where(CommerceRecord.kind=='history').order_by(CommerceRecord.created.desc())))
            for row in history[100:]:
                db.delete(row)
            db.commit()
    finally:
        CYCLE.release()

class Worker:
    def __init__(self):
        self.thread=None
        self.halt=Event()
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.halt.clear()
        self.thread=Thread(target=self.loop,daemon=True,name='artemis-commerce')
        self.thread.start()
    def stop(self):
        self.halt.set()
        if self.thread:
            self.thread.join(timeout=2)
    def loop(self):
        while not self.halt.is_set():
            try:
                run_cycle()
            except Exception:
                LOG.warning('Commerce worker cycle could not complete; retrying on next tick.')
            self.halt.wait(30)

worker=Worker()
router=APIRouter(prefix='/commerce',tags=['Commercial launch'])

def guard(request):
    origin=request.headers.get('origin')
    if origin and origin not in {'http://127.0.0.1:8200','http://localhost:8200'}:
        raise HTTPException(403,'Local same-origin request required.')
    if request.headers.get('sec-fetch-site') not in (None,'same-origin','none'):
        raise HTTPException(403,'Cross-site request refused.')

class SettingsInput(BaseModel):
    enabled: bool=False
    discovery: bool=False
    interval_minutes: int=Field(default=60,ge=30,le=1440)
    token: SecretStr=Field(default=SecretStr(''),max_length=1000)

@router.get('/status')
def api_status():
    return status()

@router.put('/settings')
def configure(body:SettingsInput,request:Request):
    guard(request)
    token=body.token.get_secret_value().strip()
    account=None
    if token:
        try:
            with core.connect(token) as client:
                account=core.verify_account(core.request(client,'user'))
                core.request(client,'products')
                core.request(client,'sales')
        except core.ProviderError as exc:
            raise HTTPException(400,str(exc)) from None
    with LOCK,SessionLocal() as db:
        cfg=settings(db)
        if token:
            cfg.update(token=token,account=account,cursor=None)
        cfg.update(enabled=body.enabled,discovery=body.discovery,interval_minutes=body.interval_minutes,
                   revision=cfg['revision']+1,next_at=0)
        put(db,'settings','config',cfg)
        db.commit()
    return {'saved':True,'token_configured':bool(cfg['token'])}

@router.post('/disconnect')
def disconnect(request:Request):
    guard(request)
    with LOCK,SessionLocal() as db:
        cfg=settings(db)
        cfg.update(token='',account=None,enabled=False,revision=cfg['revision']+1,cursor=None)
        put(db,'settings','config',cfg)
        db.commit()
    return {'disconnected':True,'note':'Saved sale records retained. Revoke the token on Gumroad to invalidate it there.'}

@router.post('/run')
def run(request:Request):
    guard(request)
    with LOCK,SessionLocal() as db:
        cfg=settings(db)
        if not cfg['enabled']:
            raise HTTPException(409,'Enable the saved commercial policy first.')
        cfg['next_at']=0
        put(db,'settings','config',cfg)
        db.commit()
    return {'queued':True,'message':'Next worker tick will process the saved policy.'}

@router.get('/toolkit')
def download():
    return Response(core.toolkit(),media_type='application/zip',headers={
        'Content-Disposition':'attachment; filename="CIN-Studios-Analyst-Workflow-Toolkit-v1.zip"'})

class ApprovalInput(BaseModel):
    artifact_sha256: str=Field(min_length=64,max_length=64)
    decision: str=Field(pattern='^(approved|withdrawn)$')
    reason: str=Field(min_length=10,max_length=1000)

@router.post('/approval')
def approve(body:ApprovalInput,request:Request):
    guard(request)
    if body.artifact_sha256!=core.artifact_hash():
        raise HTTPException(409,'Artifact changed. Review the current export before approving.')
    data={**body.model_dump(),'at':stamp(),'scope':'Local artifact review only. Does not publish a listing or authorize outreach.'}
    with LOCK,SessionLocal() as db:
        put(db,'approval','approval',data)
        put(db,'history:'+uuid4().hex,'history',data)
        db.commit()
    return data

@router.get('/brief')
def brief():
    s=status()
    total=s['last_24_hours']
    text=(f"Artemis commercial activity — {stamp()}\n\n"
          f"Storefront: {core.STORE}\n"
          f"Past 24 hours by provider sale creation time: {total['paid_orders']} qualifying paid orders; "
          f"USD {total['confirmed_gross_usd_cents']/100:.2f} gross in saved records.\n"
          f"{total['excluded_records']} records excluded from this gross total.\n"
          f"Last cycle: {s['cycle'].get('state','not run')}.\n"
          f"Unqualified search candidates displayed: {len(s['opportunities'])}.\n"
          "Outreach sent by this package: 0. Listing publication is manual.\n"
          "Autonomous acquisition attribution, customer download completion, payouts and profit are not established.\n\n"
          +total['qualification']+"\n")
    return Response(text,media_type='text/plain',headers={'Content-Disposition':'attachment; filename="Artemis-Commercial-Activity.txt"'})
