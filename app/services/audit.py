from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models


MAXIMUM_AUDIT_DETAIL_LENGTH = 1000
MAXIMUM_AUDIT_QUERY_LIMIT = 500


def sanitize_audit_detail(
    detail: str | None,
) -> str | None:
    if detail is None:
        return None

    cleaned_detail = detail.strip()

    if not cleaned_detail:
        return None

    return cleaned_detail[
        :MAXIMUM_AUDIT_DETAIL_LENGTH
    ]


def record_audit_event(
    database: Session,
    *,
    event_type: str,
    action: str,
    outcome: str = "recorded",
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: str | None = None,
) -> models.AuditEvent:
    audit_event = models.AuditEvent(
        event_type=event_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=sanitize_audit_detail(detail),
    )

    try:
        database.add(audit_event)
        database.commit()
        database.refresh(audit_event)
    except Exception:
        database.rollback()
        raise

    return audit_event


def list_audit_events(
    database: Session,
    *,
    limit: int = 100,
) -> list[models.AuditEvent]:
    safe_limit = max(
        1,
        min(
            limit,
            MAXIMUM_AUDIT_QUERY_LIMIT,
        ),
    )

    statement = (
        select(models.AuditEvent)
        .order_by(
            models.AuditEvent.created_at.desc(),
            models.AuditEvent.id.desc(),
        )
        .limit(safe_limit)
    )

    return list(
        database.scalars(statement).all()
    )