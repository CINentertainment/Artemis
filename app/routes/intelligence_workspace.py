"""Local intelligence workspace; upstream Intel Notes are read-only."""
import json
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_database
from app.config import get_settings
from app.connectors.intel_notes import IntelNotesConnector
from app.services import intelligence_workspace as work
from app.services.encryption import DataProtectionError


def same_origin(request: Request):
    origin = request.headers.get('origin')
    if request.method != 'GET' and (request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin != str(request.base_url).rstrip('/'))):
        raise HTTPException(403, 'Workspace writes must come from this Artemis origin.')


router = APIRouter(prefix='/intelligence', tags=['intelligence'], dependencies=[Depends(same_origin)])


class BoardCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class Change(BaseModel):
    version: int = Field(ge=1)
    action: Literal['import', 'note_status', 'entity', 'link', 'link_status', 'scan', 'rename', 'mention_review', 'observation_review', 'scan_relationships', 'identity_link', 'identity_unlink', 'entity_type']
    note_id: int = Field(default=1, ge=1, le=2147483647)
    name: str = Field(default='', max_length=200)
    kind: str = Field(default='other', max_length=30)
    quote: str = Field(default='', max_length=5000)
    left: str = Field(default='', max_length=32)
    right: str = Field(default='', max_length=32)
    label: str = Field(default='', max_length=500)
    status: str = Field(default='', max_length=30)
    observation_id: str = Field(default='', max_length=32)
    entity_id: str = Field(default='', max_length=32)
    link_id: str = Field(default='', max_length=32)


@router.get('', include_in_schema=False)
def page():
    return FileResponse(Path(__file__).resolve().parents[1] / 'static' / 'intelligence.html')


@router.get('/boards')
def boards(database: Session = Depends(get_database)):
    rows = database.scalars(select(work.IntelligenceBoard)).all()
    try:
        return [dict(id=r.id, title=json.loads(work.unprotect_text(r.payload))['title'], version=r.version) for r in rows]
    except DataProtectionError:
        raise HTTPException(503, 'Windows could not decrypt the workspace.')


@router.post('/boards')
def create(body: BoardCreate, database: Session = Depends(get_database)):
    try:
        return work.view(work.create_board(database, body.title))
    except ValueError as error:
        raise HTTPException(422, str(error))
    except DataProtectionError:
        raise HTTPException(503, 'Windows data protection is unavailable. Nothing saved.')


def load(database, board_id):
    try:
        return work.read_board(database, board_id)
    except LookupError:
        raise HTTPException(404, 'Board not found.')
    except DataProtectionError:
        raise HTTPException(503, 'Windows could not decrypt this board.')


@router.get('/boards/{board_id}')
def board(board_id: str, database: Session = Depends(get_database)):
    return work.view(load(database, board_id))


@router.get('/boards/{board_id}/export')
def export(board_id: str, database: Session = Depends(get_database)):
    data = work.view(load(database, board_id))
    return Response(json.dumps(dict(format='artemis-intelligence-v1', **data), ensure_ascii=False, indent=2),
        media_type='application/json', headers={'Content-Disposition':f'attachment; filename="Artemis-Board-{data["id"][:8]}.json"'})


@router.post('/boards/{board_id}')
def change(board_id: str, body: Change, database: Session = Depends(get_database)):
    data = load(database, board_id)
    if data['version'] != body.version:
        raise HTTPException(409, 'Board changed. Refresh before editing.')
    report = None
    try:
        if body.action == 'import':
            with IntelNotesConnector(base_url=str(get_settings().intel_notes_url)) as client:
                note = client.get_note(body.note_id)
            changed = work.import_note(data, note, body.note_id)
            report = 'New snapshot imported.' if changed else 'Note unchanged; no new snapshot.'
        elif body.action == 'note_status':
            work.snapshot(data, body.note_id)
            work.require(body.status in {'new','reviewed','follow_up','archived'}, 'Unknown note status.')
            data['notes'][str(body.note_id)]['status'] = body.status
        elif body.action == 'identity_link':
            from app.services.intelligence_identities import link_identity
            link_identity(data,body.left,body.right,body.label)
            report='Identity link saved as an analyst judgment; original mentions and review choices retained.'
        elif body.action == 'identity_unlink':
            from app.services.intelligence_identities import unlink_identity
            unlink_identity(data,body.entity_id)
            report='Identity assignment undone; original records retained.'
        elif body.action == 'entity_type':
            from app.services.intelligence_identities import change_type
            change_type(data,body.entity_id,body.kind)
            report='Entity type corrected. Original type and review choices retained in history.'
        elif body.action == 'entity':
            work.add_entity(data, body.note_id, body.name, body.kind, body.quote, entity_id=body.entity_id)
        elif body.action == 'link':
            work.add_link(data, body.left, body.right, body.label, body.note_id, body.quote)
        elif body.action == 'link_status':
            work.require(body.status in {'proposed','reviewed','rejected'}, 'Unknown relationship status.')
            found = next((link for link in data['links'] if link['id'] == body.link_id), None)
            work.require(found is not None, 'Relationship not found.')
            found['status'] = body.status
        elif body.action == 'observation_review':
            work.require(body.status in {'reviewed','rejected'},'Choose reviewed or rejected.')
            observation=next((o for o in data.get('observations',[]) if o['id']==body.observation_id),None)
            work.require(observation is not None,'Observation not found.')
            observation['review_status']=body.status
        elif body.action == 'mention_review':
            work.require(body.status in {'reviewed','rejected'}, 'Choose reviewed or rejected.')
            work.require(body.entity_id in data['entities'], 'Entity not found.')
            snap = work.snapshot(data,body.note_id)
            mentions = [m for m in data['entities'][body.entity_id]['mentions'] if m['revision_id']==snap['id'] and m['quote']==body.quote and m['origin']=='model_candidate']
            work.require(bool(mentions), 'Current candidate mention not found.')
            for mention in mentions:
                mention['review_status']=body.status
        elif body.action == 'rename':
            work.require(body.name.strip(), 'Enter a board title.')
            data['title'] = body.name.strip()
        elif body.action == 'scan_relationships':
            from app.runtime.service import build_model_runtime
            from app.services.research_workspace_bridge import propose_candidates,apply_candidates
            snap=work.snapshot(data,body.note_id)
            candidates=propose_candidates(snap['record']['body'],build_model_runtime(get_settings()))
            counts=apply_candidates(data,body.note_id,snap['id'],candidates)
            report=(f"{counts['entities']} named entity candidates; {counts['relationships']} direct-target relationship candidates; "
                f"{counts['withheld']} candidates withheld. Empty relationship results are valid; review the source evidence.")
        elif body.action == 'scan':
            from app.runtime.service import build_model_runtime
            snap = work.snapshot(data, body.note_id)
            model_failed=False
            try:
                candidates = work.propose_entities(snap['record']['body'], build_model_runtime(get_settings()))
            except Exception:
                candidates=[]
                model_failed=True
            from app.services.research_workspace_bridge import apply_candidates
            counts=apply_candidates(data,body.note_id,snap['id'],{'entities':candidates,'relationships':[]})
            # A manual entity scan does not mark relationship extraction as processed.
            report=(f"{counts['entities']} named entity candidates; {counts['dates']} literal date mentions; "
                f"{counts['infrastructure']} infrastructure descriptions; {counts['withheld']} new candidates withheld; "
                f"{counts['legacy_excluded']} legacy mentions excluded from identity matrices. Review decisions were preserved.")
            if model_failed:
                report += ' Local model selection failed; only deterministic cleanup and narrative mentions were saved. Retry Reprocess note evidence after checking Ollama.'
        detail = body.action
        if body.action in {'import', 'note_status', 'entity', 'scan', 'link'}:
            detail += f' · Intel Note {body.note_id}'
        if body.action in {'note_status','link_status'}:
            detail += f' · {body.status}'
        saved = work.save_board(database, data, detail)
        return dict(work.view(saved), message=report)
    except ValueError as error:
        database.rollback()
        raise HTTPException(422, str(error))
    except DataProtectionError:
        database.rollback()
        raise HTTPException(503, 'Windows data protection failed. Nothing saved.')
    except HTTPException:
        raise
    except Exception:
        database.rollback()
        raise HTTPException(502, 'The note service or local model request failed. No workspace changes saved; retry after checking the service.')


class ResearchAssignment(BaseModel):
    version: int = Field(ge=1)
    action: Literal['bind','unbind','import_run','retry']
    reference: str = Field(min_length=1,max_length=80)


@router.get('/boards/{board_id}/deliveries')
def deliveries(board_id: str, database: Session = Depends(get_database)):
    from app.services.research_workspace_bridge import ResearchBoardDelivery
    load(database,board_id)
    rows=database.scalars(select(ResearchBoardDelivery).where(ResearchBoardDelivery.board_id==board_id).order_by(ResearchBoardDelivery.created_at.desc()).limit(100)).all()
    from app.services.references import reference_for
    return [dict(id=r.id,run_id=r.run_id,reference=reference_for('run',r.run_id),status=r.status,report=json.loads(work.unprotect_text(r.report))) for r in rows]


@router.post('/boards/{board_id}/research')
def assign_research(board_id: str, body: ResearchAssignment, database: Session = Depends(get_database)):
    from app.services.references import resolve_identifier
    from app.research_models import ResearchRequisition, ResearchRun
    from app.services.research_workspace_bridge import ResearchBoardDelivery, queue_delivery
    data=load(database,board_id)
    if data['version'] != body.version:
        raise HTTPException(409,'Board changed. Refresh before editing.')
    try:
        if body.action in {'bind','unbind'}:
            req_id=resolve_identifier('research',body.reference)
            req=database.get(ResearchRequisition,req_id)
            work.require(req is not None,'Research requisition not found.')
            bindings=data.setdefault('research_bindings',{})
            if body.action=='bind':
                bindings.setdefault(req_id,dict(topic=req.topic,bound_at=work.now()))
            else:
                bindings.pop(req_id,None)
            saved=work.save_board(database,data,body.action+' research · '+req.topic)
            return work.view(saved)
        if body.action=='import_run':
            run_id=resolve_identifier('run',body.reference)
            queue_delivery(database,board_id,run_id)
        elif body.action=='retry':
            job=database.get(ResearchBoardDelivery,body.reference)
            work.require(job is not None and job.board_id==board_id,'Delivery not found.')
            work.require(job.status in {'partial','failed'},'Only failed or partial deliveries can be retried.')
            job.status='queued'
            database.commit()
        return work.view(work.read_board(database,board_id))
    except LookupError:
        raise HTTPException(404,'Research record not found.')
    except ValueError as error:
        database.rollback()
        raise HTTPException(422,str(error))
    except DataProtectionError:
        database.rollback()
        raise HTTPException(503,'Windows data protection failed. Nothing saved.')
