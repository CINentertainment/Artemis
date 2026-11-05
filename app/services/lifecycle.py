from collections.abc import Callable

from sqlalchemy.orm import Session

from app.services.audit import record_audit_event


SessionFactory = Callable[[], Session]


def record_application_lifecycle_event(
    session_factory: SessionFactory,
    action: str,
) -> None:
    with session_factory() as database:
        record_audit_event(
            database,
            event_type="system",
            action=action,
            outcome="success",
            resource_type="application",
            resource_id="artemis",
        )