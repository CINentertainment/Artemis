"""Standing objectives over the existing bounded collection dispatcher.

Mission completion is not source verification. This coordinator never directly
runs web research, infers event membership, executes trades or assigns risk levels.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from threading import Event, RLock, Thread
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import String, Text, Float, select, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, SessionLocal
from app.research_models import ResearchRequisition, ResearchRun
from app.services.encryption import protect_text, unprotect_text
from app.services import collection_planning as planner

LOCK = RLock()
LOG = logging.getLogger(__name__)
class Mission(Base):
    __tablename__ = 'standing_missions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(20), index=True)
    created: Mapped[float] = mapped_column(Float, index=True)
    payload: Mapped[str] = mapped_column(Text)

class MissionTask(Base):
    __tablename__ = 'mission_followup_tasks'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(20), index=True)
    created: Mapped[float] = mapped_column(Float, index=True)
    payload: Mapped[str] = mapped_column(Text)

class MissionReceipt(Base):
    __tablename__ = 'mission_run_receipts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mission_id: Mapped[str] = mapped_column(String(36), index=True)
    created: Mapped[float] = mapped_column(Float, index=True)
    payload: Mapped[str] = mapped_column(Text)

def now():return datetime.now(timezone.utc)
def pack(value):return protect_text(json.dumps(value, ensure_ascii=False))
def unpack(row):return json.loads(unprotect_text(row.payload))
def digest(*values):return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def normal(text):return re.sub(r'\s+',' ',text).strip().casefold()
def get(db,key):
    row=db.get(Mission,str(key))
    if not row:raise HTTPException(404,'Mission not found.')
    return row

def permitted(db,plan_data):
    key=plan_data.get('mission_id')
    if not key:return True
    row=db.get(Mission,key)
    return bool(row and row.state=='active')

def history(data,action,reason):
    data.setdefault('history',[]).append(dict(at=now().isoformat(),action=action,reason=reason))
    data['history']=data['history'][-100:]

def default_questions(objective):
    topic=objective[:190]
    return [f'What recent dated reporting addresses {topic}?',
        f'Who and which locations or services are affected within {topic}?',
        f'What independent reporting corroborates or challenges claims about {topic}?',
        f'What material changes and unresolved evidence gaps affect {topic}?']

class Create(BaseModel):
    request_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=3,max_length=120)
    objective: str = Field(min_length=10,max_length=260)
    questions: list[str] = Field(default_factory=list,max_length=6)
    start: bool = True
    auto_followups: bool = True
    interval_minutes: int = Field(default=720,ge=60,le=10080)
    daily_limit: int = Field(default=2,ge=1,le=12)
    followups_per_day: int = Field(default=2,ge=0,le=4)
    weight: int = Field(default=3,ge=1,le=5)
    domains: list[str] = Field(default_factory=list,max_length=6)
    regions: list[str] = Field(default_factory=list,max_length=8)
    entities: list[str] = Field(default_factory=list,max_length=8)
    symbols: list[str] = Field(default_factory=list,max_length=8)
    case_id: int|None = Field(default=None,gt=0)
    dossier_id: UUID|None = None
    @model_validator(mode='after')
    def clean(self):
        self.name=self.name.strip();self.objective=self.objective.strip()
        if len(self.name)<3 or len(self.objective)<10:raise ValueError('Enter a mission name and objective.')
        self.questions=list(dict.fromkeys(q.strip()for q in self.questions))or default_questions(self.objective)
        if any(not 5<=len(q)<=300 for q in self.questions):raise ValueError('Questions must contain five to three hundred characters.')
        validated=planner.Preferences(name=self.name,domains=self.domains,regions=self.regions,entities=self.entities)
        self.domains=validated.domains;self.regions=validated.regions;self.entities=validated.entities
        self.symbols=list(dict.fromkeys(s.strip().upper()for s in self.symbols))
        return self

def check_links(body):
    case=None
    if body.case_id:
        from app.services.risk_map import tracker
        with tracker(False)as client:record=client.get_case(body.case_id)
        if record.get('id')!=body.case_id:raise HTTPException(422,'Risk Tracker case ID mismatch.')
        case={k:record.get(k)for k in ('id','title','case_number','status')}
    if body.symbols:
        from app.services.market_automation import load
        if not set(body.symbols)<=set(load()['symbols']):raise HTTPException(422,'Link only existing provider-confirmed tracked symbols from Autonomous markets.')
    return case

def create_mission(body):
    key=str(body.request_id);identity=digest(body.model_dump(mode='json'))
    with LOCK,SessionLocal()as db:
        existing=db.get(Mission,key)
        if existing:
            if unpack(existing)['request_fingerprint']!=identity:raise HTTPException(409,'Request ID already belongs to different mission settings.')
            return {'id':key,'existing':True}
    try:case=check_links(body)
    except HTTPException:raise
    except Exception as error:raise HTTPException(503,'Mission links could not be validated ('+type(error).__name__+'). No mission was created.')from None
    from app.services.intelligence_workspace import IntelligenceBoard
    with planner.LOCK,LOCK,SessionLocal()as db:
        existing=db.get(Mission,key)
        if existing:
            if unpack(existing)['request_fingerprint']!=identity:raise HTTPException(409,'Request ID was already used.')
            return {'id':key,'existing':True}
        if (db.scalar(select(func.count()).select_from(Mission))or 0)>=30:raise HTTPException(409,'Thirty retained missions reached; reuse an existing mission.')
        if (db.scalar(select(func.count()).select_from(planner.CollectionPlan))or 0)>=100:raise HTTPException(409,'Collection priority capacity reached.')
        if body.dossier_id:
            from app.services.event_tracking import EventDossier
            d=db.get(EventDossier,str(body.dossier_id))
            if not d or d.state!='active':raise HTTPException(422,'Choose an active event dossier.')
        req=str(uuid5(NAMESPACE_URL,'artemis-mission-req:'+key));plan_id=str(uuid5(NAMESPACE_URL,'artemis-mission-plan:'+key));board_id=uuid5(NAMESPACE_URL,'artemis-mission-board:'+key).hex
        stamp=now();state='active'if body.start else'paused'
        questions=[dict(id=str(uuid4()),text=q,state='open',history=[])for q in body.questions]
        data=dict(name=body.name,objective=body.objective,request_fingerprint=identity,plan_id=plan_id,requisition_id=req,board_id=board_id,
            case_link=case,dossier_id=str(body.dossier_id)if body.dossier_id else None,symbols=body.symbols,
            auto_followups=body.auto_followups,followups_per_day=body.followups_per_day,created_at=stamp.isoformat(),history=[],last_observed=None,last_error=None)
        history(data,'created','Mission owns a research requisition, collection priority and topic board.')
        db.add(ResearchRequisition(id=req,topic=body.objective,questions_json=json.dumps(body.questions),risk_tracker_case_id=body.case_id))
        plan=dict(name=body.name,enabled=body.start,weight=body.weight,interval_minutes=body.interval_minutes,daily_limit=body.daily_limit,
            domains=body.domains,regions=body.regions,entities=body.entities,scope=body.objective,standalone=True,board_id=board_id,board_title=body.name,
            created_at=stamp.isoformat(),next_at=stamp.timestamp(),question_cursor=0,questions=questions,mission_id=key)
        db.add(planner.CollectionPlan(id=plan_id,kind='world',target=req,payload=planner.pack(plan)))
        board=dict(id=board_id,title=body.name,notes={},entities={},links=[],history=[],research_bindings={req:dict(topic=body.objective,bound_at=stamp.isoformat())})
        db.add(IntelligenceBoard(id=board_id,version=1,payload=pack(board)))
        db.add(Mission(id=key,state=state,created=stamp.timestamp(),payload=pack(data)))
        for question in questions:
            db.add(MissionTask(id=digest(key,'seed',question['id']),mission_id=key,state='active',created=stamp.timestamp(),payload=pack(dict(question=question['text'],question_id=question['id'],origin='objective',history=[],source=None))))
        db.commit();return {'id':key,'existing':False}

class StateChange(BaseModel):
    state: Literal['active','paused','archived']
    reason: str = Field(default='Operator mission control.',min_length=5,max_length=500)

def change_state(key,body):
    with planner.LOCK,LOCK,SessionLocal()as db:
        row=get(db,key);data=unpack(row);plan=db.get(planner.CollectionPlan,data['plan_id'])
        if not plan:raise HTTPException(409,'Owned collection priority is missing.')
        row.state=body.state;p=planner.unpack(plan);p['enabled']=body.state=='active'
        if p['enabled']:p['next_at']=now().timestamp()+60
        else:
            for job in db.scalars(select(planner.CollectionJob).where(planner.CollectionJob.plan_id==plan.id,planner.CollectionJob.state=='queued')).all():job.state='cancelled'
        plan.payload=planner.pack(p);history(data,body.state,body.reason);row.payload=pack(data);db.commit()
        return {'state':row.state,'message':'Mission state saved. An already started request may finish.'}

class Policy(BaseModel):
    auto_followups: bool
    followups_per_day: int = Field(ge=0,le=4)
    weight: int = Field(ge=1,le=5)
    interval_minutes: int = Field(ge=60,le=10080)
    daily_limit: int = Field(ge=1,le=12)

def policy(key,body):
    with planner.LOCK,LOCK,SessionLocal()as db:
        row=get(db,key);data=unpack(row);plan=db.get(planner.CollectionPlan,data['plan_id']);p=planner.unpack(plan)
        data.update(auto_followups=body.auto_followups,followups_per_day=body.followups_per_day)
        p.update(weight=body.weight,interval_minutes=body.interval_minutes,daily_limit=body.daily_limit)
        history(data,'policy','Saved mission cadence, priority and follow-up limits.');row.payload=pack(data);plan.payload=planner.pack(p);db.commit();return {'saved':True}

def tasks(db,key):return db.scalars(select(MissionTask).where(MissionTask.mission_id==str(key)).order_by(MissionTask.created.desc()).limit(200)).all()

def activate_task(db,mission,task):
    data=unpack(mission);p=db.get(planner.CollectionPlan,data['plan_id']);value=planner.unpack(p);t=unpack(task)
    if len(value['questions'])>=12:raise HTTPException(409,'This collection priority retains twelve questions. Close a mission work item to retire its owned question before adding more.')
    question=dict(id=str(uuid4()),text=t['question'],state='open',history=[],mission_task=task.id)
    value['questions'].append(question);p.payload=planner.pack(value);t['question_id']=question['id'];task.state='active';task.payload=pack(t)

class TaskDecision(BaseModel):
    state: Literal['active','closed','rejected']
    reason: str = Field(min_length=5,max_length=500)

def decide_task(key,task_id,body):
    with planner.LOCK,LOCK,SessionLocal()as db:
        row=get(db,key);task=db.get(MissionTask,task_id)
        if not task or task.mission_id!=str(key):raise HTTPException(404,'Mission task not found.')
        if body.state=='active':
            if row.state!='active':raise HTTPException(409,'Resume the mission before accepting a proposed follow-up.')
            if task.state!='proposed':raise HTTPException(409,'Only a proposed task can be accepted. Manage existing questions in Collection priorities.')
            activate_task(db,row,task)
        else:
            data=unpack(row);plan=db.get(planner.CollectionPlan,data['plan_id']);value=planner.unpack(plan);t=unpack(task)
            question=next((q for q in value['questions']if q['id']==t.get('question_id')),None)
            if question:
                question['state']='hold';question.setdefault('history',[]).append(dict(at=now().isoformat(),reason=body.reason,state='hold'))
                t['retired_question']=question;task.payload=pack(t)
                value['questions']=[q for q in value['questions']if q['id']!=question['id']]
            plan.payload=planner.pack(value);task.state=body.state
        t=unpack(task);history(t,body.state,body.reason);task.payload=pack(t);db.commit();return {'state':task.state}

def confirmed(result):
    return list(dict.fromkeys(n['intel_note_id']for n in result.get('event_notes',[])if isinstance(n,dict)and type(n.get('intel_note_id'))is int and n['intel_note_id']>0 and not n.get('error')))[:20]

def supporting_passage(body):
    from app.services.intelligence_entity_policy import narrative_spans
    for span in narrative_spans(body):
        for sentence in re.split(r'(?<=[.!?])\s+',span):
            sentence=sentence.strip()
            if 15<=len(sentence)<=800:return sentence
    return None

def observe_mission(db,row):
    from app.services.intelligence_workspace import IntelligenceBoard
    from app.services.intelligence_entity_policy import narrative_spans
    data=unpack(row);plan=db.get(planner.CollectionPlan,data['plan_id'])
    if not plan:raise ValueError('Owned priority is missing.')
    runs=db.scalars(select(ResearchRun).where(ResearchRun.requisition_id==data['requisition_id'],ResearchRun.finished_at.is_not(None),ResearchRun.finished_at>=datetime.fromtimestamp(row.created,timezone.utc).replace(tzinfo=None)).order_by(ResearchRun.finished_at.desc()).limit(50)).all()
    for run in runs:
        receipt_id=digest(row.id,run.id)
        if db.get(MissionReceipt,receipt_id):continue
        result=json.loads(run.result_json)if run.result_json else{}
        value=dict(run_id=run.id,status=run.status,note_ids=confirmed(result),source_count=len(result.get('sources')or[]),observed_at=now().isoformat(),finished_at=run.finished_at.isoformat(),
            qualifications='Completion is not proof of current reporting, saved notes, independent evidence or a changed event.')
        db.add(MissionReceipt(id=receipt_id,mission_id=row.id,created=now().timestamp(),payload=pack(value)))
    board=db.get(IntelligenceBoard,data['board_id']);board_data=unpack(board)if board else{}
    allowed=set(n for run in runs if run.status=='completed'for n in confirmed(json.loads(run.result_json)if run.result_json else{}))
    existing=tasks(db,row.id)
    for task in existing:
        t=unpack(task);source=t.get('source')
        if not source or task.state not in {'active','proposed'}:continue
        entry=board_data.get('notes',{}).get(str(source['note_id']),{})
        revisions=entry.get('revisions',[])
        valid=bool(revisions and entry.get('status')!='archived' and revisions[-1]['id']==source['revision_id'])
        if not valid:
            value=planner.unpack(plan);question=next((q for q in value['questions']if q['id']==t.get('question_id')),None)
            if question:
                t['retired_question']=question;value['questions']=[q for q in value['questions']if q['id']!=question['id']];plan.payload=planner.pack(value)
            task.state='withdrawn';history(t,'withdrawn','The referenced board snapshot is no longer current.');task.payload=pack(t)
    if len(existing)<200:
        for nid,entry in list(board_data.get('notes',{}).items())[:200]:
            if not nid.isdecimal()or int(nid)not in allowed or entry.get('status')=='archived'or not entry.get('revisions'):continue
            snap=entry['revisions'][-1];record=snap['record'];quote=supporting_passage(record.get('body')or'')
            if not quote:continue
            task_id=digest(row.id,'reporting-followup',normal(quote))
            if db.get(MissionTask,task_id)or any(t.id==task_id for t in db.new if isinstance(t,MissionTask)):continue
            if len(existing)>=200:break
            question=('Within '+data['objective'][:90]+', what dated reporting corroborates or corrects: "'+quote[:140]+'"?')[:300]
            source=dict(note_id=int(nid),revision_id=snap['id'],quote=quote,classification=record.get('classification')or'Not recorded')
            task=MissionTask(id=task_id,mission_id=row.id,state='proposed',created=now().timestamp(),payload=pack(dict(question=question,question_id=None,origin='saved_note',source=source,history=[])))
            db.add(task);existing.append(task)
    # Task state is a work status, never an assertion that the question is answered.
    start=now().astimezone(ZoneInfo('America/Chicago')).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    today=sum(1 for t in existing if unpack(t).get('auto_accepted_at',0)>=start)
    if row.state=='active'and data['auto_followups']:
        for task in sorted(existing,key=lambda t:t.created):
            if task.state!='proposed'or today>=data['followups_per_day']:continue
            p=planner.unpack(plan)
            if len(p['questions'])>=12:break
            activate_task(db,row,task);t=unpack(task);t['auto_accepted_at']=now().timestamp();history(t,'active','Automatically accepted within the saved mission scope and follow-up allowance.');task.payload=pack(t);today+=1
    data.update(last_observed=now().isoformat(),last_error=None);row.payload=pack(data)

def synchronize():
    with planner.LOCK,LOCK,SessionLocal()as db:
        rows=db.scalars(select(Mission).where(Mission.state!='archived').limit(30)).all()
        for row in rows:
            try:
                with db.begin_nested():observe_mission(db,row);db.flush()
            except Exception as error:
                data=unpack(row);data.update(last_error='Mission observation incomplete ('+type(error).__name__+').',last_observed=now().isoformat());row.payload=pack(data)
        db.commit()

def summarize(db,row):
    data=unpack(row);p=db.get(planner.CollectionPlan,data['plan_id']);value=planner.unpack(p)if p else{};settings=planner.config(db)
    work=tasks(db,row.id);receipts=db.scalars(select(MissionReceipt).where(MissionReceipt.mission_id==row.id).order_by(MissionReceipt.created.desc()).limit(20)).all()
    waiting='Mission '+row.state if row.state!='active'else'Collection planner is paused'if not settings['enabled']else'Collection priority is paused'if not value.get('enabled')else'Eligible for scheduled collection within saved daily allowances'
    question_states={q['id']:q['state']for q in value.get('questions',[])}
    return dict(id=row.id,state=row.state,**data,plan=value,waiting=waiting,
        tasks=[dict(id=t.id,state=t.state,question_state=question_states.get(unpack(t).get('question_id')),created=t.created,**unpack(t))for t in work],runs=[unpack(r)for r in receipts],
        counts=dict(proposed=sum(t.state=='proposed'for t in work),active=sum(t.state=='active'and question_states.get(unpack(t).get('question_id'))=='open'for t in work),closed=sum(t.state=='closed'for t in work)),
        limitations=['Up to fifty recent finished mission runs are reconciled per pass; long offline gaps may leave older runs unlisted.',
            'Up to two hundred mission tasks and two hundred board notes are retained. Twelve collection questions can be active or retained; no automatic evidence-gap closure is inferred.',
            'Tracked market symbols are context links; their independent market worker settings and budgets remain in force.'])

def briefing(db,row):
    value=summarize(db,row);runs=value['runs'];notes=sorted({nid for r in runs for nid in r['note_ids']})
    return dict(title=value['name'],state=row.state,objective=value['objective'],generated_at=now().isoformat(),
        summary=('Mission '+value['name']+' is '+row.state+'. '+str(len(runs))+' latest reconciled run receipt(s) reference '+str(len(notes))+' confirmed saved note ID(s). '
            +str(value['counts']['proposed'])+' proposed and '+str(value['counts']['active'])+' active work item(s). '+value['waiting']+'.'),
        note_ids=notes,highlights=[dict(note_id=t['source']['note_id'],quote=t['source']['quote'],classification=t['source']['classification']) for t in value['tasks']if t.get('source')and t['state']!='withdrawn'][:4],unknowns=['Run completion and counts do not establish new events or independent corroboration.','Open questions remain unresolved until separately reviewed.'],
        next_questions=[t['question']for t in value['tasks']if t['state']=='proposed'or(t['state']=='active'and t.get('question_state')=='open')][:6],
        symbols=value['symbols'],case_link=value['case_link'],dossier_id=value['dossier_id'],plan_id=value['plan_id'],board_id=value['board_id'])

def same_origin(request:Request):
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(request.headers.get('origin')and request.headers['origin']!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use this Artemis origin for mission controls.')
router=APIRouter(prefix='/missions',dependencies=[Depends(same_origin)])
@router.post('')
def create(body:Create):return create_mission(body)
@router.get('')
def listing():
    with LOCK,SessionLocal()as db:return {'missions':[summarize(db,r)for r in db.scalars(select(Mission).order_by(Mission.created.desc()).limit(30)).all()],'planner':planner.config(db)}
@router.get('/{mission_id}')
def details(mission_id:UUID):
    with LOCK,SessionLocal()as db:return summarize(db,get(db,mission_id))
@router.put('/{mission_id}/state')
def state(mission_id:UUID,body:StateChange):return change_state(mission_id,body)
@router.put('/{mission_id}/policy')
def save_policy(mission_id:UUID,body:Policy):return policy(mission_id,body)
@router.put('/{mission_id}/tasks/{task_id}')
def task_review(mission_id:UUID,task_id:str,body:TaskDecision):return decide_task(mission_id,task_id,body)
@router.get('/{mission_id}/briefing')
def brief(mission_id:UUID):
    with LOCK,SessionLocal()as db:return briefing(db,get(db,mission_id))

class ProposeNote(BaseModel):
    note_id:int=Field(gt=0)
@router.post('/{mission_id}/propose-event-note')
def propose_event_note(mission_id:UUID,body:ProposeNote):
    from app.services import event_tracking as events
    from app.services.intelligence_workspace import IntelligenceBoard,snapshot
    with events.LOCK,LOCK,SessionLocal()as db:
        row=get(db,mission_id);data=unpack(row)
        if not data['dossier_id']:raise HTTPException(409,'This mission has no event dossier context link.')
        board=db.get(IntelligenceBoard,data['board_id'])
        if not board:raise HTTPException(409,'Mission board is unavailable.')
        board_data=unpack(board)
        if str(body.note_id)not in board_data['notes']:raise HTTPException(422,'Choose a note imported into the mission board.')
        snap=snapshot(board_data,body.note_id);note=snap['record'];quote=next((s for s in events.narrative(note['body'])if 10<=len(s)<=1000),None)
        if not quote:raise HTTPException(422,'No bounded source narrative sentence available.')
        dossier=events.get(db,data['dossier_id'])
        if dossier.state!='active':raise HTTPException(409,'The linked dossier is archived.')
        evidence=events.ingest(db,dossier,note,quote);db.commit();return {'id':evidence.id,'state':evidence.state,'message':'Note proposed for grouping review; not accepted as the same event.'}

class Focus(BaseModel):
    mission_id:UUID
@router.post('/focus/select')
def focus(body:Focus):
    from app.services.voice_hub import STATE,atomic
    with LOCK,SessionLocal()as db:row=get(db,body.mission_id);title=unpack(row)['name']
    atomic(STATE/'active-mission.json',{'id':str(body.mission_id)})
    return {'selected':title}

def voice(text):
    from app.services.voice_hub import normalize,STATE,read_json
    clean=normalize(text)
    if clean=='list missions':
        with LOCK,SessionLocal()as db:
            rows=db.scalars(select(Mission).where(Mission.state!='archived').order_by(Mission.created.desc()).limit(10)).all()
            return {'speech':'. '.join(unpack(r)['name']+' is '+r.state for r in rows)or'No standing missions are configured.','link':'/static/missions.html'}
    if clean.startswith('open mission '):
        name=clean[len('open mission '):]
        with LOCK,SessionLocal()as db:
            rows=[r for r in db.scalars(select(Mission).limit(30)).all()if normalize(unpack(r)['name'])==name]
            if len(rows)!=1:return {'speech':'Use a unique exact mission name or select it in Mission Control.','link':'/static/missions.html'}
            key=rows[0].id
        focus(Focus(mission_id=UUID(key)));return {'speech':'Mission selected. Say mission status, brief this mission, pause this mission, resume this mission, or prioritize this mission.','link':'/static/missions.html#'+key}
    key=read_json(STATE/'active-mission.json',{}).get('id')
    if not key:return {'speech':'Select a mission in Mission Control, or say open mission followed by its exact name.','link':'/static/missions.html'}
    if clean in {'pause this mission','resume this mission'}:
        result=change_state(key,StateChange(state='paused'if clean.startswith('pause')else'active'));return {'speech':result['message'],'link':'/static/missions.html#'+key}
    if clean=='prioritize this mission':
        with planner.LOCK,LOCK,SessionLocal()as db:
            row=get(db,key);data=unpack(row);plan=db.get(planner.CollectionPlan,data['plan_id']);value=planner.unpack(plan);value['weight']=5;plan.payload=planner.pack(value);history(data,'prioritized','Operator voice command set the mission weight to five.');row.payload=pack(data);db.commit()
        return {'speech':'Mission priority set to five. Pauses and daily allowances still apply.','link':'/static/missions.html#'+key}
    with LOCK,SessionLocal()as db:value=briefing(db,get(db,key))
    speech=value['summary']
    if clean=='brief this mission':speech+=' Saved reporting, not independently verified: '+'. '.join('Intel Note '+str(h['note_id'])+': '+h['quote'][:300]for h in value['highlights'][:2])+'. Next questions: '+'. '.join(value['next_questions'][:3])+'. Counts are work receipts, not verified event totals.'
    return {'speech':speech,'link':'/static/missions.html#'+key}

def brief_missions(start,end):
    # This is current operational context as of export, not a historical state reconstruction.
    with LOCK,SessionLocal()as db:
        rows=db.scalars(select(Mission).where(Mission.state!='archived').limit(30)).all();return [briefing(db,r)for r in rows][:10]

class Worker:
    def __init__(self):self.event=Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-mission-control');self.thread.start()
    def stop(self):
        self.event.set()
        if self.thread:self.thread.join(timeout=2)
    def tick(self):synchronize()
    def loop(self):
        while not self.event.is_set():
            try:self.tick()
            except Exception:LOG.exception('Mission coordination failed')
            self.event.wait(30)
worker=Worker()
