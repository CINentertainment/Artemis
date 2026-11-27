"""Retryable case delivery and encrypted local event-map cache."""
import hashlib
import json
import logging
import os
from datetime import datetime,timedelta,timezone
from threading import Event,RLock,Thread
from uuid import UUID,NAMESPACE_URL,uuid5

from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from sqlalchemy import String,Text,select
from sqlalchemy.orm import Mapped,mapped_column

from app.database import Base,SessionLocal
from app.config import get_settings
from app.connectors.risk_tracker import RiskTrackerConnector
from app.connectors.intel_notes import IntelNotesConnector
from app.services.encryption import protect_text,unprotect_text
from app.services.event_geography import note_event,indexes,places,norm

LOCK=RLock();LOG=logging.getLogger(__name__)
class AtlasState(Base):
    __tablename__='atlas_state'
    id:Mapped[str]=mapped_column(String(20),primary_key=True)
    payload:Mapped[str]=mapped_column(Text,nullable=False)
class AtlasEvent(Base):
    __tablename__='atlas_events'
    id:Mapped[str]=mapped_column(String(20),primary_key=True)
    payload:Mapped[str]=mapped_column(Text,nullable=False)
class RiskDelivery(Base):
    __tablename__='risk_automatic_deliveries'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    payload:Mapped[str]=mapped_column(Text,nullable=False)

def now():return datetime.now(timezone.utc)
def state():
    with SessionLocal()as db:
        row=db.get(AtlasState,'default')
        return {**dict(enabled=False,map_requested=False,next_at=None,status='paused',errors=[],last_finished=None,summary={}),**(json.loads(unprotect_text(row.payload))if row else{})}
def update(changes):
    with LOCK,SessionLocal()as db:
        row=db.get(AtlasState,'default');s=state();s.update(changes)
        if row:row.payload=protect_text(json.dumps(s))
        else:db.add(AtlasState(id='default',payload=protect_text(json.dumps(s))))
        db.commit();return s

def credentials(write=True):
    saved=state().get('credentials')
    if saved:return saved
    user=os.getenv('ARTEMIS_RISK_WRITE_USERNAME','').strip();password=os.getenv('ARTEMIS_RISK_WRITE_PASSWORD','')
    if user and password:return dict(username=user,password=password,role=os.getenv('ARTEMIS_RISK_WRITE_ROLE','Analyst'))
    if not write:
        s=get_settings()
        if s.risk_tracker_password:return dict(username=s.risk_tracker_username,password=s.risk_tracker_password.get_secret_value(),role='Read Only')
    raise ValueError('Configure an Analyst or Administrator Risk Tracker account in this page. Credentials stay encrypted on this computer.')
def tracker(write=True,credential=None):
    c=credential or credentials(write)
    if write and c['role']not in {'Analyst','Administrator'}:raise ValueError('Case updates require an Analyst or Administrator account.')
    return RiskTrackerConnector(base_url=str(get_settings().risk_tracker_url),username=c['username'],password=c['password'],expected_role=c['role'],timeout_seconds=20)

def report(key,value=None):
    with SessionLocal()as db:
        row=db.get(RiskDelivery,key)
        if value is None:return json.loads(unprotect_text(row.payload))if row else None
        if row:row.payload=protect_text(json.dumps(value))
        else:db.add(RiskDelivery(id=key,payload=protect_text(json.dumps(value))))
        db.commit()

def upsert(note,case_ids,case_metadata,case_complete=True):
    event=note_event(note);key=str(note['id'])
    with LOCK,SessionLocal()as db:
        row=db.get(AtlasEvent,key);old=json.loads(unprotect_text(row.payload))if row else{}
        event['case_ids']=sorted(set(case_ids))if case_complete else sorted(set(case_ids+old.get('case_ids',[])))
        event['case_metadata']={str(i):case_metadata.get(str(i),old.get('case_metadata',{}).get(str(i),{}))for i in event['case_ids']}
        event['case_links_complete']=case_complete;event['synced_at']=now().isoformat();event['manual_history']=old.get('manual_history',[])
        if old.get('revision')==event['revision']and old.get('override'):event['override']=old['override'];event['geography']=old['override']['geography']
        elif old.get('override'):event['resolution_invalidated']='Saved note changed; prior manual placement is no longer applied.'
        if row:row.payload=protect_text(json.dumps(event,ensure_ascii=False))
        else:db.add(AtlasEvent(id=key,payload=protect_text(json.dumps(event,ensure_ascii=False))))
        db.commit()

def bound_requisitions():
    from app.services.intelligence_workspace import IntelligenceBoard
    from app.research_models import ResearchRequisition
    with SessionLocal()as db:
        ids=set()
        for row in db.scalars(select(IntelligenceBoard)).all():ids.update(json.loads(unprotect_text(row.payload)).get('research_bindings',{}))
        return [dict(id=r.id,topic=r.topic,case_id=r.risk_tracker_case_id)for r in db.scalars(select(ResearchRequisition).where(ResearchRequisition.id.in_(ids))).all()if not r.topic.casefold().startswith('market:')]

def ensure_case(client,req,cases):
    if req['case_id']:
        case=next((c for c in cases if c['id']==req['case_id']),None)or client.get_case(req['case_id'])
    else:
        number='ARTEMIS-'+req['id'].replace('-','')[:24].upper()
        case=next((c for c in cases if c.get('case_number')==number),None)
        if case is None:
            try:case=client._request_authenticated_json('POST','/cases',payload=dict(case_number=number,title=req['topic'][:200],summary='Automatically collected source reporting for this standing research topic. Claims and candidate links require review.',status='Monitoring',priority='Routine',risk_level='Undetermined'))
            except Exception:
                # A lost response or concurrent creation is reconciled by the stable case number.
                case=next((c for c in client.list_cases(include_archived=True)if c.get('case_number')==number),None)
                if case is None:raise
            if not isinstance(case,dict)or type(case.get('id'))is not int or case['id']<1 or case.get('case_number')!=number:raise ValueError('Risk Tracker returned an unexpected case identity.')
            cases.append(case)
        from app.research_models import ResearchRequisition
        with SessionLocal()as db:
            row=db.get(ResearchRequisition,req['id'])
            if row.risk_tracker_case_id is None:row.risk_tracker_case_id=case['id'];db.commit()
            elif row.risk_tracker_case_id!=case['id']:raise ValueError('Case binding changed while automatic setup was running.')
    if case.get('is_archived')or case.get('status')in {'Closed','Mitigated'}:raise ValueError('Case '+str(case['id'])+' is archived/closed/mitigated; automatic updates were withheld.')
    return case

def source_blocks(body):
    import re
    blocks=[]
    for block in re.split(r'(?m)^\[\d+\]\s+',body):
        url=re.search(r'(?m)^URL:\s*(https://\S+)',block);excerpt=re.search(r'(?s)Excerpt:\s*(.+)',block);record=re.search(r'(?m)^Artemis source record:\s*(\S+)',block)
        if url and excerpt:blocks.append(dict(url=url[1],content=excerpt[1].strip(),record_id=record[1]if record else'unknown'))
    return blocks[:6]

def deliver(client,case,req,fetch_note):
    from app.research_models import ResearchRun
    with SessionLocal()as db:runs=[dict(id=r.id,result=json.loads(r.result_json))for r in db.scalars(select(ResearchRun).where(ResearchRun.requisition_id==req['id'],ResearchRun.status=='completed').order_by(ResearchRun.started_at.desc()).limit(20)).all()if r.result_json]
    links={n['intel_note_id']:n for n in client.list_case_event_notes(case['id'])};errors=[];count=0
    for run in runs:
        key=hashlib.sha256((run['id']+':'+str(case['id'])).encode()).hexdigest();previous=report(key)
        if previous and previous.get('complete'):continue
        rows=[]
        for item in run['result'].get('event_notes',[]):
            note_id=item.get('intel_note_id')
            if type(note_id)is not int or note_id<1 or item.get('error'):continue
            try:
                note=fetch_note(note_id);event=note_event(note)
                if note_id not in links:
                    if not event['source_urls']:raise ValueError('No source URLs in the saved event note.')
                    status=event['event_status']if event['event_status']in {'occurred','announced','planned','reported'}else'reported'
                    client.link_case_event_note(case_id=case['id'],intel_note_id=note_id,title=event['title'][:250],event_date=event['event_date'],event_status=status,source_urls=event['source_urls']);links[note_id]={};count+=1
                # Stable derived evidence IDs avoid conflicting with original source-record payloads.
                for source in source_blocks(event['body']):
                    identity=uuid5(NAMESPACE_URL,'artemis:risk-note-source:'+str(note_id)+':'+source['url']+':'+hashlib.sha256(source['content'].encode()).hexdigest())
                    client.attach_case_evidence(case_id=case['id'],artemis_record_id=identity,title=('Saved Intel Note '+str(note_id)+' source excerpt')[:250],source_url=source['url'],content='Saved-note excerpt, not independent corroboration. Original Artemis record: '+source['record_id']+'\n\n'+source['content'],intel_note_id=note_id)
                rows.append(dict(note_id=note_id,status='linked'))
            except Exception as e:rows.append(dict(note_id=note_id,status='failed',error=str(e)[:250]));errors.append('Note '+str(note_id)+': '+str(e)[:200])
        report(key,dict(run_id=run['id'],case_id=case['id'],complete=all(r['status']=='linked'for r in rows),notes=rows,updated_at=now().isoformat()))
    return count,errors

class Worker:
    def __init__(self):self.stop_event=Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear();self.thread=Thread(target=self.loop,name='artemis-risk-map',daemon=True);self.thread.start()
    def stop(self):self.stop_event.set();self.thread and self.thread.join(timeout=2)
    def loop(self):
        while not self.stop_event.is_set():
            try:
                s=state()
                if (s['enabled']and(not s['next_at']or now()>=datetime.fromisoformat(s['next_at'])))or s['map_requested']or(s.get('map_deferred')and(not s['next_at']or now()>=datetime.fromisoformat(s['next_at']))):self.cycle(s['enabled'])
            except Exception as e:update({'status':'failed','errors':[str(e)[:350]],'next_at':(now()+timedelta(minutes=5)).isoformat(),'map_requested':False})
            self.stop_event.wait(10)
    def cycle(self,writes):
        from app.services.operations_recovery import ready
        if not ready(['notes','risk_tracker']if writes else['notes'],'risk and map sync'):
            update({'status':'waiting_service','map_requested':False,'next_at':(now()+timedelta(seconds=30)).isoformat(),'map_deferred':True});return
        update({'map_deferred':False})
        update({'status':'running','map_requested':False});errors=[];notes={};observed={};case_metadata={};linked=0;complete=True;bound=0
        with IntelNotesConnector(base_url=str(get_settings().intel_notes_url))as intel:
            for n in intel.list_notes()[:500]:
                if type(n.get('id'))is int:notes[n['id']]=n
            def fetch(note_id):
                if note_id not in notes or not notes[note_id].get('body'):notes[note_id]=intel.get_note(note_id)
                return notes[note_id]
            try:
                with tracker(writes)as client:
                    cases=client.list_cases(include_archived=True)
                    if writes:
                        for req in bound_requisitions()[:100]:
                            if self.stop_event.is_set()or not state()['enabled']:break
                            try:
                                case=ensure_case(client,req,cases);bound+=1
                                n,e=deliver(client,case,req,fetch);linked+=n;errors.extend(e)
                            except Exception as e:errors.append(req['topic'][:60]+': '+str(e)[:200])
                    if len(cases)>100:errors.append('Map case scan limited to 100 cases.');complete=False
                    for case in cases[:100]:
                        case_metadata[str(case['id'])]={k:case.get(k)for k in ('title','case_number','status','priority','risk_level','is_archived')}
                        try:
                            links=client.list_case_event_notes(case['id'])
                            if len(links)>200:complete=False;errors.append('Case note-link scan limited to 200 notes per case.')
                            for link in links[:200]:
                                nid=link['intel_note_id'];observed.setdefault(nid,[]).append(case['id'])
                                if nid not in notes and len(notes)<700:fetch(nid)
                        except Exception as e:complete=False;errors.append('Case '+str(case['id'])+': '+str(e)[:180])
            except Exception as e:complete=False;errors.append('Risk Tracker unavailable: '+str(e)[:250])
            for nid in list(notes):
                try:
                    note=fetch(nid);upsert(note,observed.get(nid,[]),case_metadata,complete)
                except Exception as e:errors.append('Map note '+str(nid)+': '+str(e)[:180])
        update({'status':'partial'if errors else'completed','last_finished':now().isoformat(),'next_at':(now()+timedelta(minutes=5)).isoformat(),'errors':errors[-30:],'summary':dict(notes_scanned=len(notes),cases_bound=bound,new_note_links=linked,case_links_complete=complete,limits='Latest 20 completed runs per board-bound requisition; up to 100 requisitions, 100 cases, 500 initial notes and 700 total notes per sync. Older cached notes remain available with their sync timestamp.')})
worker=Worker()

def same_origin(request:Request):
    origin=request.headers.get('origin')
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(origin and origin!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Atlas changes must come from this Artemis origin.')
router=APIRouter(prefix='/atlas',dependencies=[Depends(same_origin)])
@router.get('/status')
def status():
    s=state();s.pop('credentials',None)
    try:c=credentials();s['account']={'username':c['username'],'role':c['role'],'configured':True}
    except ValueError:s['account']={'configured':False}
    return s
class Settings(BaseModel):
    enabled:bool
    username:str=Field(default='',max_length=150)
    password:str=Field(default='',max_length=1000)
    role:str=Field(default='Analyst',pattern=r'^(Analyst|Administrator)$')
@router.put('/settings')
def settings(body:Settings):
    changes={'enabled':body.enabled,'next_at':now().isoformat()if body.enabled else None}
    try:
        if body.username or body.password:
            if not body.username or not body.password:raise ValueError('Enter both username and password.')
            c=dict(username=body.username.strip(),password=body.password,role=body.role)
            with tracker(credential=c)as client:client.get_current_user()
            changes['credentials']=c
        elif body.enabled:
            with tracker()as client:client.get_current_user()
        update(changes);return status()
    except Exception as e:raise HTTPException(422,str(e))from e
@router.post('/refresh')
def refresh():update({'map_requested':True});return {'queued':True}
@router.get('/events')
def events():
    with SessionLocal()as db:return [json.loads(unprotect_text(r.payload))for r in db.scalars(select(AtlasEvent).limit(1000)).all()]
@router.get('/places')
def search_places(q:str=''):
    query=norm(q)
    if len(query)<2:return []
    return [{k:p[k]for k in ('id','name','country','kind','lat','lon')}for p in places()if query in norm(p['name']+', '+p['country'])][:30]
class Resolve(BaseModel):
    revision:str=Field(min_length=64,max_length=64)
    place_id:str=Field(max_length=80)
    reason:str=Field(min_length=5,max_length=800)
    quote:str=Field(min_length=1,max_length=5000)
@router.put('/events/{note_id}/location')
def resolve_note(note_id:int,body:Resolve):
    from app.services.intelligence_entity_policy import narrative_spans
    with LOCK,SessionLocal()as db:
        row=db.get(AtlasEvent,str(note_id))
        if not row:raise HTTPException(404,'Cached note not found.')
        event=json.loads(unprotect_text(row.payload))
        if event['revision']!=body.revision:raise HTTPException(409,'Note changed. Refresh before placing it.')
        if not any(body.quote in q for q in narrative_spans(event['body'])):raise HTTPException(422,'Use an exact saved narrative quote.')
        p=indexes()[1].get(body.place_id)
        if not p:raise HTTPException(422,'Choose a reference place from the search results.')
        geo={k:p[k]for k in ('id','name','country','kind','lat','lon')};geo.update(state='resolved',basis='analyst-selected reference place',precision='Reference point, not an independently verified incident coordinate')
        value=dict(geography=geo,reason=body.reason,quote=body.quote,at=now().isoformat())
        event['override']=value;event['geography']=geo;event['manual_history']=(event.get('manual_history',[])+[value])[-30:];row.payload=protect_text(json.dumps(event));db.commit()
    return {'saved':True}
@router.delete('/events/{note_id}/location')
def clear_resolution(note_id:int):
    with LOCK,SessionLocal()as db:
        row=db.get(AtlasEvent,str(note_id))
        if not row:raise HTTPException(404,'Cached note not found.')
        event=json.loads(unprotect_text(row.payload));event.pop('override',None);event['geography']=note_event({'id':note_id,'body':event['body'],'title':event['title'],'location':event.get('note_location')})['geography'];event['manual_history']=(event.get('manual_history',[])+[{'action':'clear','at':now().isoformat()}])[-30:];row.payload=protect_text(json.dumps(event));db.commit()
    return {'cleared':True}
