import json
from datetime import datetime, timezone
from app.services.work_priority import research_lock
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_database
from app.research_models import ResearchRequisition, ResearchRun
from app.research_trial import run_research


router = APIRouter(prefix="/research", tags=["research"])



class ResearchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    risk_tracker_case_id: int | None = Field(default=None, gt=0)


ResearchQuestion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=500,
    ),
]


class RequisitionRequest(ResearchRequest):
    questions: list[ResearchQuestion] = Field(
        default_factory=list,
        max_length=10,
    )


class RunRequest(BaseModel):
    request_id: UUID = Field(default_factory=uuid4)
    followup_questions: list[ResearchQuestion] | None = Field(default=None, min_length=1, max_length=2)


def _requisition_data_without_reference(record: ResearchRequisition) -> dict:
    return {
        "id": record.id,
        "topic": record.topic,
        "questions": json.loads(record.questions_json),
        "risk_tracker_case_id": record.risk_tracker_case_id,
        "created_at": record.created_at,
    }


def _run_data_without_reference(
    record: ResearchRun,
    *,
    include_result: bool = False,
) -> dict:
    data = {
        "id": record.id,
        "requisition_id": record.requisition_id,
        "status": record.status,
        "request": json.loads(record.request_json),
        "error": record.error,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
    }

    if include_result:
        data["result"] = (
            json.loads(record.result_json)
            if record.result_json is not None
            else None
        )

    return data


def get_requisition(
    database: Session,
    requisition_id: str,
) -> ResearchRequisition:
    # Resolve readable research references.
    from app.services.references import resolve_identifier

    requisition_id = resolve_identifier("research", requisition_id)

    record = database.get(
        ResearchRequisition,
        str(requisition_id),
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Research requisition not found.",
        )

    return record


def get_run(
    database: Session,
    requisition_id: str,
    run_id: str,
) -> ResearchRun:
    # Resolve readable research references.
    from app.services.references import resolve_identifier

    requisition_id = resolve_identifier("research", requisition_id)
    run_id = resolve_identifier("run", run_id)

    record = database.get(ResearchRun, str(run_id))

    if (
        record is None
        or record.requisition_id != str(requisition_id)
    ):
        raise HTTPException(
            status_code=404,
            detail="Research run not found.",
        )

    return record


def completed_result(record: ResearchRun) -> dict:
    if record.status != "completed" or record.result_json is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {record.id} has no completed result to compare."
            ),
        )

    result = json.loads(record.result_json)

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=409,
            detail=f"Run {record.id} has an unsupported result format.",
        )

    return result


def index_records(result: dict, collection: str) -> dict:
    """Match sources by URL and event notes by saved Intel Note ID."""
    indexed = {}

    for item in result.get(collection) or []:
        if not isinstance(item, dict):
            continue

        if collection == "sources":
            url = item.get("url")
            record_id = item.get("record_id")

            if isinstance(url, str) and url.strip():
                key = f"url:{url.strip()}"
            elif record_id:
                key = f"record:{record_id}"
            else:
                continue
        else:
            note_id = item.get("intel_note_id")

            # Unsaved candidates have no stable note identity.
            if type(note_id) is not int or note_id < 1:
                continue

            key = f"note:{note_id}"

        indexed[key] = item

    return indexed


def compare_records(before: dict, after: dict) -> dict:
    before_keys = set(before)
    after_keys = set(after)

    return {
        "added": [
            after[key]
            for key in sorted(after_keys - before_keys)
        ],
        "absent_from_target": [
            before[key]
            for key in sorted(before_keys - after_keys)
        ],
        "changed": [
            {
                "identity": key,
                "before": before[key],
                "after": after[key],
            }
            for key in sorted(before_keys & after_keys)
            if before[key] != after[key]
        ],
        "unchanged_count": sum(
            before[key] == after[key]
            for key in before_keys & after_keys
        ),
    }


def collection_needs(result: dict) -> list[str] | None:
    report = result.get("gap_collection")

    if not isinstance(report, dict):
        return None

    needs = report.get("information_needed")

    if not isinstance(needs, list):
        return None

    return sorted({
        item.strip()
        for item in needs
        if isinstance(item, str) and item.strip()
    })


def compare_collection_needs(before: dict, after: dict) -> dict:
    previous = collection_needs(before)
    current = collection_needs(after)

    if previous is None or current is None:
        return {
            "available": False,
            "reason": (
                "Both runs must contain structured collection needs."
            ),
        }

    previous_set = set(previous)
    current_set = set(current)

    return {
        "available": True,
        "newly_reported": sorted(current_set - previous_set),
        "no_longer_reported": sorted(previous_set - current_set),
        "still_reported": sorted(previous_set & current_set),
    }


def run_warnings(result: dict) -> dict:
    """Keep partial failures visible when comparing completed runs."""
    return {
        "collection_errors": result.get("collection_errors") or [],
        "event_extraction_error": result.get("event_extraction_error"),
        "synthesis_error": result.get("synthesis_error"),
        "source_save_errors": [
            item
            for item in result.get("sources") or []
            if isinstance(item, dict)
            and item.get("risk_tracker_error")
        ],
        "event_note_errors": [
            item
            for item in result.get("event_notes") or []
            if isinstance(item, dict)
            and any(
                value
                for key, value in item.items()
                if "error" in key
            )
        ],
    }


@router.post("")
def research(request: ResearchRequest) -> dict:
    topic = request.topic.strip()

    if not topic:
        raise HTTPException(
            status_code=422,
            detail="Enter a research topic.",
        )

    if not research_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="The research worker is busy or reserved for a queued operator question.",
        )

    try:
        if request.risk_tracker_case_id is None:
            return run_research(topic)

        return run_research(
            topic,
            risk_tracker_case_id=request.risk_tracker_case_id,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from None
    finally:
        research_lock.release()


@router.post("/requisitions", status_code=201)
def create_requisition(
    request: RequisitionRequest,
    database: Session = Depends(get_database),
) -> dict:
    topic = request.topic.strip()

    if not topic:
        raise HTTPException(
            status_code=422,
            detail="Enter a research topic.",
        )

    record = ResearchRequisition(
        topic=topic,
        questions_json=json.dumps(
            request.questions,
            ensure_ascii=False,
        ),
        risk_tracker_case_id=request.risk_tracker_case_id,
    )
    database.add(record)
    database.commit()

    return requisition_data(record)


@router.get("/requisitions")
def list_requisitions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    database: Session = Depends(get_database),
) -> list[dict]:
    records = database.scalars(
        select(ResearchRequisition)
        .order_by(
            ResearchRequisition.created_at.desc(),
            ResearchRequisition.id,
        )
        .offset(offset)
        .limit(limit)
    ).all()

    return [requisition_data(record) for record in records]


@router.get("/requisitions/{requisition_id}")
def read_requisition(
    requisition_id: str,
    database: Session = Depends(get_database),
) -> dict:
    # Resolve readable research references.
    from app.services.references import resolve_identifier

    requisition_id = resolve_identifier("research", requisition_id)

    return requisition_data(
        get_requisition(database, requisition_id)
    )


@router.get("/requisitions/{requisition_id}/compare")
def compare_runs(
    requisition_id: str,
    baseline_run_id: str,
    target_run_id: str,
    database: Session = Depends(get_database),
) -> dict:
    # Resolve readable research references.
    from app.services.references import resolve_identifier

    requisition_id = resolve_identifier("research", requisition_id)
    baseline_run_id = resolve_identifier("run", baseline_run_id)
    target_run_id = resolve_identifier("run", target_run_id)

    get_requisition(database, requisition_id)

    if baseline_run_id == target_run_id:
        raise HTTPException(
            status_code=422,
            detail="Choose two different research runs.",
        )

    baseline = get_run(
        database,
        requisition_id,
        baseline_run_id,
    )
    target = get_run(
        database,
        requisition_id,
        target_run_id,
    )

    before = completed_result(baseline)
    after = completed_result(target)

    source_changes = compare_records(
        index_records(before, "sources"),
        index_records(after, "sources"),
    )
    note_changes = compare_records(
        index_records(before, "event_notes"),
        index_records(after, "event_notes"),
    )

    return {
        "requisition_id": str(requisition_id),
        "baseline": run_data(baseline),
        "target": run_data(target),
        "request_changed": (
            json.loads(baseline.request_json)
            != json.loads(target.request_json)
        ),
        "sources": source_changes,
        "event_notes": note_changes,
        "collection_needs": compare_collection_needs(
            before,
            after,
        ),
        "brief_changed": (
            before.get("brief") != after.get("brief")
        ),
        "warnings": {
            "baseline": run_warnings(before),
            "target": run_warnings(after),
        },
        "comparison_limits": [
            (
                "This compares saved outputs. It does not verify "
                "that a newly recorded event happened recently."
            ),
            (
                "Sources match by exact URL, falling back to "
                "Artemis record ID when no URL is available."
            ),
            (
                "Event notes match by Intel Note ID. Duplicate "
                "notes with different IDs remain separate."
            ),
            (
                "Changed records may reflect metadata or save "
                "status changes rather than new reporting."
            ),
            (
                "A source or note absent from the target run "
                "has not necessarily been deleted or disproved."
            ),
            (
                "Collection needs use exact text from the "
                "preliminary gap assessment. A need no longer "
                "reported is not necessarily resolved."
            ),
        ],
    }


@router.get("/requisitions/{requisition_id}/runs")
def list_runs(
    requisition_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    database: Session = Depends(get_database),
) -> list[dict]:
    # Resolve readable research references.
    from app.services.references import resolve_identifier

    requisition_id = resolve_identifier("research", requisition_id)

    get_requisition(database, requisition_id)

    records = database.scalars(
        select(ResearchRun)
        .where(
            ResearchRun.requisition_id == str(requisition_id)
        )
        .order_by(
            ResearchRun.started_at.desc(),
            ResearchRun.id,
        )
        .offset(offset)
        .limit(limit)
    ).all()

    return [run_data(record) for record in records]


@router.get("/requisitions/{requisition_id}/runs/{run_id}")
def read_run(
    requisition_id: str,
    run_id: str,
    database: Session = Depends(get_database),
) -> dict:
    # Resolve readable research references.
    from app.services.references import resolve_identifier

    requisition_id = resolve_identifier("research", requisition_id)
    run_id = resolve_identifier("run", run_id)

    return run_data(
        get_run(database, requisition_id, run_id),
        include_result=True,
    )


@router.post("/requisitions/{requisition_id}/runs")
def run_requisition(
    requisition_id: str,
    request: RunRequest,
) -> dict:
    # Resolve readable research references.
    from app.services.references import resolve_identifier

    requisition_id = resolve_identifier("research", requisition_id)

    if not research_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="The research worker is busy or reserved for a queued operator question.",
        )

    try:
        # Close this session before the workflow writes through
        # other local application endpoints.
        with SessionLocal() as database:
            existing = database.get(
                ResearchRun,
                str(request.request_id),
            )

            if existing is not None:
                if existing.requisition_id != str(requisition_id):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This request ID belongs to another requisition."
                        ),
                    )

                return run_data(existing, include_result=True)

            requisition = get_requisition(
                database,
                requisition_id,
            )
            snapshot = {
                "topic": requisition.topic,
                "questions": request.followup_questions if request.followup_questions is not None else json.loads(
                    requisition.questions_json
                ),
                "followup": request.followup_questions is not None,
                "risk_tracker_case_id": (
                    requisition.risk_tracker_case_id
                ),
            }

            from app.services.collection_planning import admit_research,research_deferred
            try:collection=admit_research(database,requisition.id,str(request.request_id))
            except HTTPException as error:
                database.rollback();context=research_lock.collection_context()
                research_deferred(context.get('lane')or'world',context.get('target')or requisition.id,str(error.detail));raise
            if collection:
                snapshot['collection_priority']={k:collection[k]for k in ('plan_id','name','questions','domains','reason')}
                if collection['questions']and request.followup_questions is None:snapshot['questions']=collection['questions']
            record = ResearchRun(
                id=str(request.request_id),
                requisition_id=requisition.id,
                status="running",
                request_json=json.dumps(
                    snapshot,
                    ensure_ascii=False,
                ),
            )
            database.add(record)
            database.commit()

        try:
            result = run_research(
                snapshot["topic"],
                risk_tracker_case_id=(
                    snapshot["risk_tracker_case_id"]
                ),
                research_questions=snapshot["questions"],
            )
            result_json = json.dumps(
                result,
                ensure_ascii=False,
                default=str,
            )
        except Exception as error:
            with SessionLocal() as database:
                record = database.get(
                    ResearchRun,
                    str(request.request_id),
                )
                record.status = "failed"
                record.error = f"{type(error).__name__}: {error}"
                record.finished_at = (
                    datetime.now(timezone.utc).replace(tzinfo=None)
                )
                database.commit()

                return run_data(record, include_result=True)

        with SessionLocal() as database:
            record = database.get(
                ResearchRun,
                str(request.request_id),
            )
            record.status = "completed"
            record.result_json = result_json
            record.finished_at = (
                datetime.now(timezone.utc).replace(tzinfo=None)
            )
            database.commit()

            # Durable workspace notification runs after research has been saved.
            from app.services.research_workspace_bridge import enqueue_completed_run
            enqueue_completed_run(record.id)
            return run_data(record, include_result=True)
    finally:
        research_lock.release()

def requisition_data(record: ResearchRequisition) -> dict:
    from app.services.references import label_record

    return label_record(
        "research",
        _requisition_data_without_reference(record),
    )



def run_data(
    record: ResearchRun,
    *,
    include_result: bool = False,
) -> dict:
    from app.services.references import label_record

    return label_record(
        "run",
        _run_data_without_reference(
            record,
            include_result=include_result,
        ),
    )
