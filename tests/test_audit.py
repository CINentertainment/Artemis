from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.services.audit import (
    MAXIMUM_AUDIT_DETAIL_LENGTH,
    list_audit_events,
    record_audit_event,
)


def build_audit_test_engine(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    initialize_database(engine)

    return engine


def test_record_audit_event(
    tmp_path: Path,
):
    engine = build_audit_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        event = record_audit_event(
            database,
            event_type="system",
            action="application_started",
            outcome="success",
            resource_type="application",
            resource_id="artemis",
            detail="Artemis started locally.",
        )

        assert event.id is not None
        assert event.event_type == "system"
        assert (
            event.action
            == "application_started"
        )
        assert event.outcome == "success"

        stored_events = list_audit_events(
            database
        )

        assert len(stored_events) == 1
        assert stored_events[0].id == event.id

    engine.dispose()


def test_audit_detail_is_limited(
    tmp_path: Path,
):
    engine = build_audit_test_engine(
        tmp_path
    )

    oversized_detail = (
        "A"
        * (
            MAXIMUM_AUDIT_DETAIL_LENGTH
            + 500
        )
    )

    with Session(engine) as database:
        event = record_audit_event(
            database,
            event_type="security",
            action="detail_limit_test",
            detail=oversized_detail,
        )

        assert event.detail is not None

        assert len(event.detail) == (
            MAXIMUM_AUDIT_DETAIL_LENGTH
        )

    engine.dispose()


def test_audit_query_limit_is_enforced(
    tmp_path: Path,
):
    engine = build_audit_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        for event_number in range(5):
            record_audit_event(
                database,
                event_type="test",
                action=(
                    f"event_{event_number}"
                ),
            )

        events = list_audit_events(
            database,
            limit=2,
        )

        assert len(events) == 2
        assert events[0].action == "event_4"
        assert events[1].action == "event_3"

    engine.dispose()