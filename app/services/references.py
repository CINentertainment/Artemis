from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    event,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert

from app.database import Base, SessionLocal
from app.research_models import ResearchRequisition, ResearchRun
from app.services.task_agents import AgentMission


# These tables belong to the application's existing database.
metadata = Base.metadata

reference_counters = Table(
    "artemis_reference_counters",
    metadata,
    Column("scope", String(100), primary_key=True),
    Column("value", Integer, nullable=False),
)

record_references = Table(
    "artemis_record_references",
    metadata,
    Column("kind", String(30), primary_key=True),
    Column("record_id", String(36), primary_key=True),
    Column("reference", String(100), nullable=False, unique=True),
)


def existing_reference(connection, kind, record_id):
    return connection.scalar(
        select(record_references.c.reference).where(
            record_references.c.kind == kind,
            record_references.c.record_id == str(record_id),
        )
    )


def next_number(connection, scope):
    # SQLite serializes these writes. The counter and reference are
    # committed or rolled back with the record's transaction.
    connection.execute(
        insert(reference_counters)
        .values(scope=scope, value=0)
        .on_conflict_do_nothing(index_elements=["scope"])
    )

    connection.execute(
        update(reference_counters)
        .where(reference_counters.c.scope == scope)
        .values(value=reference_counters.c.value + 1)
    )

    return connection.scalar(
        select(reference_counters.c.value).where(
            reference_counters.c.scope == scope
        )
    )


def store_reference(connection, kind, record_id, reference):
    connection.execute(
        record_references.insert().values(
            kind=kind,
            record_id=str(record_id),
            reference=reference,
        )
    )
    return reference


def ensure_reference(connection, kind, record_id, created_at=None):
    record_id = str(record_id)
    existing = existing_reference(connection, kind, record_id)

    if existing:
        return existing

    if kind == "run":
        requisition_id = connection.scalar(
            select(ResearchRun.requisition_id).where(
                ResearchRun.id == record_id
            )
        )

        if requisition_id is None:
            raise ValueError("Research run not found.")

        parent_created = connection.scalar(
            select(ResearchRequisition.created_at).where(
                ResearchRequisition.id == requisition_id
            )
        )

        parent_reference = ensure_reference(
            connection,
            "research",
            requisition_id,
            parent_created,
        )

        number = next_number(
            connection,
            f"run:{requisition_id}",
        )
        reference = f"{parent_reference}-R{number:02d}"

    else:
        prefix = {
            "research": "RES",
            "mission": "MSN",
        }[kind]

        year = (
            created_at.year
            if created_at is not None
            else datetime.now(timezone.utc).year
        )

        number = next_number(
            connection,
            f"{kind}:{year}",
        )
        reference = f"{prefix}-{year}-{number:04d}"

    return store_reference(
        connection,
        kind,
        record_id,
        reference,
    )


def register_research_reference(mapper, connection, target):
    ensure_reference(
        connection,
        "research",
        target.id,
        target.created_at,
    )


def register_run_reference(mapper, connection, target):
    ensure_reference(
        connection,
        "run",
        target.id,
        target.started_at,
    )


def register_mission_reference(mapper, connection, target):
    ensure_reference(
        connection,
        "mission",
        target.id,
        target.created_at,
    )


event.listen(
    ResearchRequisition,
    "after_insert",
    register_research_reference,
)
event.listen(
    ResearchRun,
    "after_insert",
    register_run_reference,
)
event.listen(
    AgentMission,
    "after_insert",
    register_mission_reference,
)


def backfill_references(engine):
    # Stable ordering assigns references to existing records once.
    # Later title changes or new records do not renumber them.
    with engine.begin() as connection:
        for record_id, created_at in connection.execute(
            select(
                ResearchRequisition.id,
                ResearchRequisition.created_at,
            ).order_by(
                ResearchRequisition.created_at,
                ResearchRequisition.id,
            )
        ).all():
            ensure_reference(
                connection, "research", record_id, created_at
            )

        for record_id, created_at in connection.execute(
            select(
                ResearchRun.id,
                ResearchRun.started_at,
            ).order_by(
                ResearchRun.started_at,
                ResearchRun.id,
            )
        ).all():
            ensure_reference(
                connection, "run", record_id, created_at
            )

        for record_id, created_at in connection.execute(
            select(
                AgentMission.id,
                AgentMission.created_at,
            ).order_by(
                AgentMission.created_at,
                AgentMission.id,
            )
        ).all():
            ensure_reference(
                connection, "mission", record_id, created_at
            )


def reference_for(kind, record_id):
    with SessionLocal() as database:
        reference = database.scalar(
            select(record_references.c.reference).where(
                record_references.c.kind == kind,
                record_references.c.record_id == str(record_id),
            )
        )

    if reference is None:
        raise RuntimeError(
            "Readable reference missing. Restart Artemis to initialize references."
        )

    return reference


def resolve_identifier(kind, value):
    value = str(value).strip()

    try:
        return str(UUID(value))
    except ValueError:
        pass

    with SessionLocal() as database:
        record_id = database.scalar(
            select(record_references.c.record_id).where(
                record_references.c.kind == kind,
                record_references.c.reference == value.upper(),
            )
        )

    if record_id is None:
        raise HTTPException(
            status_code=404,
            detail="Research or mission reference not found.",
        )

    return record_id


def label_record(kind, data):
    data = dict(data)

    record_id = data.get("id") or data.get("run_id")
    reference = reference_for(kind, record_id)
    data["reference"] = reference

    if kind == "research":
        data["display_title"] = data["topic"]

    elif kind == "run":
        data["requisition_reference"] = reference_for(
            "research",
            data["requisition_id"],
        )
        request = data.get("request") or {}
        data["display_title"] = (
            data.get("topic")
            or request.get("topic")
            or "Research run"
        )

    else:
        data["display_title"] = data["assignment"]

        # Step numbers follow the saved execution history.
        data["steps"] = [
            {
                **step,
                "reference": f"{reference}-S{number:02d}",
            }
            for number, step in enumerate(
                data.get("steps") or [],
                start=1,
            )
        ]

    return data