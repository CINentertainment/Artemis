"""Operational capability facts and bounded, advisory-only gap review.

No external probes, model autonomy, installs, spending or policy changes.
"""
import hashlib
import importlib
import importlib.util
import json
import re
import time
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel
from sqlalchemy import String,Text,select,func
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.services.encryption import protect_text,unprotect_text

LOCK=RLock();INTERVAL=900;ROOT=Path(__file__).resolve().parents[1]
class AwarenessRecord(Base):
    __tablename__='capability_awareness_records'
    id:Mapped[str]=mapped_column(String(100),primary_key=True)
    payload:Mapped[str]=mapped_column(Text)

def pack(v):return protect_text(json.dumps(v,ensure_ascii=False))
def unpack(r):return json.loads(unprotect_text(r.payload))
def read(db,key,default):
    row=db.get(AwarenessRecord,key);return unpack(row) if row else default

def write(db,key,data):
    row=db.get(AwarenessRecord,key)
    if row:row.payload=pack(data)
    else:db.add(AwarenessRecord(id=key,payload=pack(data)))

def module(name):return importlib.import_module('app.services.'+name)
def installed(name):return (ROOT/'services'/(name+'.py')).is_file()
def alive(mod):
    obj=getattr(mod,'worker',getattr(mod,'research_scheduler',None));thread=getattr(obj,'thread',None) or getattr(obj,'_thread',None)
    return bool(thread and thread.is_alive())

def facts(key):
    """Select only known booleans, enum states and timestamps; never return raw configs."""
    if key=='documents':
        mod=module('documents');d=mod.status()
        from app.services.readiness_checks import ocr_diagnostics
        ocr=ocr_diagnostics()
        with SessionLocal() as db:
            count=db.scalar(select(func.count()).select_from(mod.DocumentRecord))
            answers=db.scalar(select(func.count()).select_from(mod.DocumentAnswer))
        return {'pdf_reader':d['pdf_installed'],'ocr_installed':bool(ocr['runnable'] and ocr['english_available']),'ocr_diagnostics':ocr,'pdf_ocr_renderer':d['pdf_ocr_installed'],'saved_documents':count,'saved_answers':answers,'configuration':'ready' if d['pdf_installed'] else 'dependencies_missing','last_success':None,'evidence':'Saved record counts; no claim of exhaustive extraction or correct model answers.'}
    if key=='memory':
        mod=module('document_memory')
        with SessionLocal() as db:p=mod.policy(db)
        return {'enabled':bool(p['enabled']),'excluded_count':len(p['excluded_ids']),'configuration':'ready','last_success':None,'evidence':'Stored policy only; each dashboard chat can turn search off.'}
    if key in {'markets','risk'}:
        mod=module('market_automation' if key=='markets' else 'risk_map');d=mod.load() if key=='markets' else mod.state()
        return {'enabled':bool(d.get('enabled')),'worker_alive':alive(mod),'last_state':d.get('status'),'last_success':d.get('last_finished') if d.get('status')=='completed' else None,'error_count':len(d.get('errors') or []),'configuration':'configured' if d.get('enabled') else 'paused','evidence':'Last recorded worker cycle, not verification of source claims.'}
    if key=='mail':
        mod=module('outreach')
        with SessionLocal() as db:c=mod.config(db)
        return {'connected':bool(c.get('credential')),'enabled':bool(c.get('enabled')),'worker_alive':alive(mod),'last_success':c.get('last_poll'),'issue_present':bool(c.get('issue')),'configuration':'configured' if c.get('credential') else 'authorization_missing','evidence':'Mailbox check timestamp; does not prove sending, delivery or sales.'}
    if key=='publishing':
        mod=module('growth')
        with SessionLocal() as db:c=mod.cfg(db)
        channels={k:{'connected':bool(mod.chan(c,k)['connected']),'enabled':bool(mod.chan(c,k)['enabled'])} for k in ('member','organization')}
        return {'enabled':bool(c['enabled']),'mode':c['mode'],'worker_alive':alive(mod),'channels':channels,'configuration':'configured' if any(v['connected'] for v in channels.values()) else 'authorization_missing','last_success':None,'evidence':'Saved channel/policy state; no permission approval or successful post inferred.'}
    if key=='commerce':
        mod=module('commerce')
        with SessionLocal() as db:c=mod.settings(db);cycle=mod.read(db,'cycle')
        return {'connected':bool(c.get('token')),'enabled':bool(c.get('enabled')),'worker_alive':alive(mod),'configuration':'configured' if c.get('token') else 'authorization_missing','last_state':cycle.get('state'),'last_success':cycle.get('finished_at') if cycle.get('state')=='completed' else None,'evidence':'Read-only storefront reconciliation; not payout or customer-acquisition verification.'}
    if key=='briefs':
        mod=module('operations_center')
        with SessionLocal() as db:
            c=mod.config(db)
            rows=db.scalars(select(mod.BriefJob).where(mod.BriefJob.status.in_(['completed','partial'])).order_by(mod.BriefJob.due.desc()).limit(20)).all()
            successful=next((json.loads(r.payload) for r in rows if r.status=='completed' and json.loads(r.payload).get('report_id') and (Path(mod.get_settings().data_directory)/'reports'/(json.loads(r.payload)['report_id']+'.pdf')).is_file()),None)
        return {'enabled':bool(c['enabled']),'next_at':c.get('next_at'),'worker_alive':alive(mod),'configuration':'configured' if c['enabled'] else 'paused','last_success':successful.get('finished_at') if successful else None,'evidence':'Completed saved briefing job with PDF present, if recorded; not a review of the PDF contents.'}
    if key=='backup':
        mod=module('continuity');d=mod.status()
        return {'configured':bool(d['configured']),'enabled':bool(d.get('settings',{}).get('enabled')),'overdue':bool(d['overdue']),'last_success':d.get('status',{}).get('last_success'),'restore_rehearsal_at':d.get('status',{}).get('restore_rehearsal',{}).get('verified_at'),'configuration':'configured' if d['configured'] else 'setup_missing','evidence':'Saved authenticated backup and staged restore rehearsal receipt, where present. This does not prove live activation or recovery on a different machine.'}
    if key=='research':
        from app.research_models import ResearchRun
        with SessionLocal() as db:
            row=db.scalars(select(ResearchRun).order_by(ResearchRun.finished_at.desc()).limit(1)).first()
        mod=module('research_scheduler')
        return {'worker_alive':alive(mod),'last_state':row.status if row else None,'last_success':row.finished_at.isoformat() if row and row.status=='completed' and row.finished_at else None,'configuration':'installed','evidence':'Saved research run status; no independent verification of notes or sources.'}
    if key=='voice':
        mod=module('voice_hub');s=mod.settings()
        return {'wake_phrase_configured':bool(s.get('wake_phrase')),'configuration':'installed','last_success':None,'evidence':'Voice components installed; microphone quality and live listener status not checked here.'}
    return {'configuration':'installed','last_success':None,'evidence':'Source component present. Live workflow not checked.'}

CATALOG=[
 ('documents','Document reading and persistent storage','documents','Upload and extract supported documents in the workspace and dashboard chat.','No universal file-format support; scans need local OCR; extraction is bounded.','/static/documents.html'),
 ('memory','Cross-chat saved-document search','document_memory','Retrieve relevant saved document excerpts for dashboard chat.','Keyword retrieval, bounded coverage; per-chat switch and file exclusions apply.','/static/documents.html'),
 ('research','Public-source research','research_scheduler','Run configured scheduled research and save supported findings.','Completed run status does not verify underlying source claims.','/static/automation.html'),
 ('markets','Market research','market_automation','Discover, track and research supported market symbols.','Directional outlooks are hypotheses; no trade execution.','/static/markets.html'),
 ('risk','Risk updates and event map','risk_map','Update risk records and geographic observations under saved policy.','Geography and assessments can need review.','/static/atlas.html'),
 ('briefs','PDF briefing preparation','operations_center','Generate and schedule briefs from selected saved sources.','Source availability and qualifications remain material.','/static/operations.html'),
 ('mail','Business email outreach','outreach','Authorized introductions, one follow-up, response checks and human reply approval.','Requires connected mailbox and enabled policy; no unapproved conversational replies.','/static/outreach.html'),
 ('publishing','LinkedIn publishing','growth','Publish to configured personal or company channels under saved policy.','Company authorization may require platform approval; connection is not a successful post.','/static/growth.html'),
 ('commerce','Gumroad commercial monitoring','commerce','Read saved provider product/sales records and reconcile qualifying gross.','No automatic product publication, payouts, profit or acquisition attribution.','/static/commerce.html'),
 ('backup','Encrypted backup and restore tools','continuity','Configure encrypted backup and operator-run restore procedures.','A saved backup does not prove a successful restore.','/static/continuity.html'),
 ('voice','Voice interface','voice_hub','Local voice listener and supported voice workflows.','Listener, microphone, permissions and model dependencies can block use.','/static/voice.html'),
]

def snapshot():
    caps=[]
    for key,label,component,description,limits,link in CATALOG:
        present=installed(component)
        item={'id':key,'name':label,'installed':present,'description':description,'limits':limits,'link':link,'readiness':'unavailable' if not present else 'unverified'}
        if present:
            try:
                data=facts(key)
                with SessionLocal() as db:receipts=read(db,'verification:'+key,{})
                if receipts:
                    data['verification']=receipts
                    if receipts.get('completed_at'):data['last_success']=receipts['completed_at']
                item.update(data)
                if data.get('configuration') in {'authorization_missing','setup_missing','dependencies_missing'}:item['readiness']='blocked'
                elif data.get('enabled') is False or data.get('configuration')=='paused':item['readiness']='paused'
                elif data.get('worker_alive') is False:item['readiness']='worker_unavailable'
                elif data.get('issue_present') or data.get('last_state') in {'failed','error','partial'} or data.get('error_count',0):item['readiness']='needs_attention'
                elif data.get('last_success'):item['readiness']='recorded_activity'
                else:item['readiness']='installed_not_end_to_end_verified'
            except Exception as error:item.update(readiness='status_unknown',status_error=type(error).__name__,evidence='Status could not be read; no missing capability inferred.')
        caps.append(item)
    return {'checked_at':time.time(),'method':'Local source presence plus allowlisted saved status reads. No live external probes.','capabilities':caps,'authority':'Diagnose and recommend only. No new installs, access grants, spending, external sends, policy changes or task execution.','unknown':'No matching capability specification means unknown, not proven impossible.'}

RULES=[
 ('documents',r'\b(document|pdf|docx|spreadsheet|xlsx|powerpoint|pptx|upload|attachment|ocr|scan)s?\b'),
 ('memory',r'cross.chat|saved.document|document (library|memory)|remember.*document'),
 ('research',r'\b(research|investigate|world watch|collect sources)\b'),
 ('markets',r'\b(stock|market|ticker|crypto|etf)s?\b'),
 ('risk',r'risk tracker|event map'),('briefs',r'\b(briefing|daily brief|pdf report)\b'),
 ('mail',r'\b(email|mailbox|outreach|gmail|prospect)s?\b'),
 ('publishing',r'linkedin|company page|social post'),('commerce',r'gumroad|storefront|sales reconciliation'),
 ('backup',r'\b(backup|restore)s?\b'),('voice',r'\b(voice|microphone|wake phrase|listen)\b')]

def assess_task(text,view):
    result=[];lookup={c['id']:c for c in view['capabilities']}
    for key,pattern in RULES:
        if not re.search(pattern,text,re.I):continue
        c=lookup[key];kind=None;reason=None;action=None
        if not c['installed']:kind='missing_component';reason='Required application component is not installed.';action='Review whether this capability is needed; plan an implementation before claiming it is available.'
        elif c['readiness']=='status_unknown':kind='diagnostic_unknown';reason='The component exists, but its current status could not be read.';action='Inspect the existing tool page and logs; do not assume the feature is absent.'
        elif c.get('configuration') in {'authorization_missing','setup_missing','dependencies_missing'}:
            kind='setup_or_access';reason='The component is installed but its required setup, access or dependencies are incomplete.';action='Complete the existing setup or authorization flow; code changes are not established as necessary.'
        elif c.get('enabled') is False:kind='paused_policy';reason='The capability is installed but its saved policy is paused.';action='Review the saved policy and authorize enabling it if desired; no automatic enablement.'
        elif c.get('worker_alive') is False:kind='worker_unavailable';reason='The saved workflow exists but its worker thread is not alive.';action='Inspect Operations & recovery and the application logs.'
        elif c.get('issue_present') or c.get('last_state') in {'failed','error','partial'} or c.get('error_count',0):kind='recorded_failure';reason='The installed workflow reports a failure or incomplete cycle.';action='Inspect its existing status and logs before proposing new functionality.'
        elif not c.get('last_success'):kind='verification_gap';reason='Installed capability has no end-to-end success evidence in this bounded check.';action='Perform one known-input task and inspect its output; do not rebuild solely for missing verification.'
        if key=='documents' and re.search(r'\b(ocr|scan|scanned|image|photo)\b',text,re.I) and c.get('ocr_installed') is False:
            kind='dependency';reason='Local OCR engine is unavailable.';action='Install/configure Tesseract and English data using the existing Document Reading setup.'
        if key=='publishing' and re.search(r'company|page|organization',text,re.I) and c.get('channels',{}).get('organization',{}).get('connected') is False:
            kind='authorization';reason='The company channel has no verified saved connection. Platform approval may still be required.';action='Complete the LinkedIn company API approval/authorization flow. Do not bypass platform restrictions.'
        if kind:result.append({'capability':key,'kind':kind,'reason':reason,'impact':'This task depends on an unavailable, paused or unverified prerequisite.','action':action,'link':c['link']})
    return {'requirements':[key for key,p in RULES if re.search(p,text,re.I)],'gaps':result,'limits':'Rules cover known capability families. They do not prove every prerequisite or assess unrestricted arbitrary tasks.'}

SELF=re.compile(r'\b(your|you|artemis|she|her)\b',re.I)
def is_capability_question(text):
    if len(text)>6000 or not SELF.search(text):return False
    if re.search(r'\b(summarize|analyse|analyze|extract|translate|rewrite)\b',text,re.I) and not re.search(r'capabilit|missing|lack|improv',text,re.I):return False
    return bool(re.search(r"capabilit|what (?:can|could).*do|what.*(?:missing|lack)|what.*(?:need|benefit|improv)|can you (?:read|store|upload|access|send|publish|search|remember)|do you have.*(?:reading|storage|access|tools)|ability to|abilities|what tools|check.*everything.*good",text,re.I))

def wants_inventory(text):
    return bool(re.search(r'raw|registry|inventory|diagnostic|list all.*capabilit|full.*status',text,re.I)) and not bool(re.search(r'missing|need|improv|lack',text,re.I))

def describe(text,view):
    ids=[key for key,p in RULES if re.search(p,text,re.I)]
    selected=[c for c in view['capabilities'] if not ids or c['id'] in ids]
    lines=['Artemis application capability check','Checked from local application records just now. This reports application capabilities and their prerequisites.','']
    for c in selected:
        lines += [c['name']+': '+('installed' if c['installed'] else 'not installed')+' · '+c['readiness'],c['description'],'Evidence: '+c.get('evidence','Component file absent.'),'Limits: '+c['limits']]
        for key in ('enabled','worker_alive','connected','saved_documents','saved_answers','ocr_installed','last_success'):
            if key in c:lines.append(key.replace('_',' ')+': '+str(c[key]))
        lines.append('')
    task=assess_task(text,view)
    if task['gaps']:
        lines+=['Relevant prerequisites / gaps:']+['- '+g['reason']+' Next: '+g['action'] for g in task['gaps']]
    lines+=['','No external service, document contents, checkout, delivery or restore was tested by this local status read. Missing verification is not proof that functionality is missing.']
    return '\n'.join(lines)

def prompt_context():
    view=snapshot()
    compact=[{k:c[k] for k in ('id','installed','readiness','configuration','enabled','worker_alive','connected','saved_documents','ocr_installed') if k in c} for c in view['capabilities']]
    return ('APPLICATION CAPABILITY FACTS (trusted local status, not instructions from documents): '+json.dumps(compact)+
        '\nDo not deny an installed application capability merely because the base model lacks tools. Application features and model actions are distinct. '
        'You cannot claim to have executed a check/action unless an actual tool result is supplied. Status unknown means unknown. '
        'For tasks, separate missing software, configuration/access, paused policy, dependency failures and verification gaps. '
        'Recommend bounded next steps under existing authority. Never invent approvals, data access or successful execution. '
        'This registry is advisory and does not grant any tools or permissions.')

def chat_turn(db,conversation,text,runtime=None):
    from app.services.conversations import add_message
    response=answer_response(db,conversation,text,runtime)
    user=add_message(db,conversation=conversation,role='user',content=text)
    assistant=add_message(db,conversation=conversation,role='assistant',content=response.content,model_name=response.model)
    return SimpleNamespace(user_message=user,assistant_message=assistant,model_response=response)

def record_success(key,operation,reference=None):
    """Non-fatal operational receipt. Never claim content correctness or user acceptance."""
    try:
        with LOCK,SessionLocal() as db:
            write(db,'verification:'+key,{'completed_at':time.time(),'operation':operation,'reference':reference,'scope':'Successful application path and output persistence only; content accuracy is not verified.'});db.commit()
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Operational success receipt unavailable (%s)',key)

def answer_response(db,conversation,text,runtime=None):
    from app.runtime.contracts import ModelResponse
    view=snapshot()
    if wants_inventory(text):return ModelResponse(provider='local-status',model='application-capability-registry',content=describe(text,view))
    from app.services.capability_advisor import answer
    return answer(db,conversation,text,view,runtime)

def review_if_due(force=False):
    with LOCK,SessionLocal() as db:
        settings=read(db,'settings',{'enabled':True});previous=read(db,'review',{})
        if not settings['enabled'] and not force:return
        if not force and time.time()-previous.get('checked_at',0)<INTERVAL:return
        view=snapshot();goals=[];errors=[]
        try:
            missions=module('mission_control')
            for row in db.scalars(select(missions.Mission).where(missions.Mission.state=='active').order_by(missions.Mission.created.desc()).limit(20)):
                data=missions.unpack(row);text=str(data.get('objective') or data.get('title') or '')[:1500]
                if text:goals.append((row.id,text))
        except Exception as error:errors.append('Active missions: '+type(error).__name__)
        for cap in view['capabilities']:
            if cap.get('enabled') is True:goals.append(('enabled:'+cap['id'],cap['name']+' '+{'memory':'saved document library','briefs':'daily brief','mail':'outreach','publishing':'LinkedIn','commerce':'Gumroad','backup':'backup','voice':'voice','risk':'risk tracker','markets':'market','research':'research'}.get(cap['id'],cap['id'])))
        active=set();found=[]
        for goal_id,text in goals[:30]:
            for gap in assess_task(text,view)['gaps']:
                key='gap:'+hashlib.sha256((goal_id+gap['capability']+gap['kind']).encode()).hexdigest()
                active.add(key);old=read(db,key,{})
                value={**gap,'id':key,'goal_id':goal_id,'goal':text[:160],'state':'open','first_seen':old.get('first_seen',time.time()),'last_checked':time.time()}
                write(db,key,value);found.append(value)
                try:
                    delivery=module('proactive_delivery')
                    delivery.item(db,'services','capability:'+key,time.time(),'Artemis prerequisite: '+gap['capability'],gap['reason']+' '+gap['action'],gap['link'],severity='attention')
                except Exception as error:errors.append('Inbox update: '+type(error).__name__)
        for row in db.scalars(select(AwarenessRecord).where(AwarenessRecord.id.like('gap:%'))):
            if row.id not in active:
                data=unpack(row);data.update(state='not_observed_in_latest_review',last_checked=time.time());row.payload=pack(data)
        write(db,'review',{'checked_at':time.time(),'goals_checked':len(goals[:30]),'open_gaps':len(found),'errors':list(dict.fromkeys(errors)),'scope':'Up to 20 active missions and enabled workflows; advisory prerequisites, not arbitrary new goals.'});db.commit()

router=APIRouter(prefix='/capabilities',tags=['Application capabilities'])
@router.get('/status')
def status():
    with SessionLocal() as db:
        return snapshot()|{'review':read(db,'review',{}),'settings':read(db,'settings',{'enabled':True}),'gaps':[unpack(r) for r in db.scalars(select(AwarenessRecord).where(AwarenessRecord.id.like('gap:%')).limit(100))]}
class Assess(BaseModel):task:str
@router.post('/assess')
async def assess(body:Assess,request:Request):
    from app.services.documents import same_origin
    await same_origin(request)
    if not 1<=len(body.task)<=6000:raise HTTPException(400,'Use a task description of 1–6,000 characters.')
    return assess_task(body.task,snapshot())
class Policy(BaseModel):enabled:bool
@router.put('/settings')
async def settings(body:Policy,request:Request):
    from app.services.documents import same_origin
    await same_origin(request)
    with LOCK,SessionLocal() as db:write(db,'settings',body.model_dump());db.commit()
    return body.model_dump()
@router.post('/review')
async def review(request:Request):
    from app.services.documents import same_origin
    await same_origin(request)
    import asyncio
    await asyncio.to_thread(review_if_due,True)
    return status()

@router.post('/check-ocr')
async def check_ocr(request:Request):
    from app.services.documents import same_origin
    from app.services.readiness_checks import ocr_smoke
    import asyncio
    await same_origin(request)
    return await asyncio.to_thread(ocr_smoke)
