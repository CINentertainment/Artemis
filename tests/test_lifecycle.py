from pathlib import Path
from unittest.mock import Mock, call

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import (
    SessionLocal,
    create_database_engine,
    initialize_database,
)
from app.main import app, lifespan
from app.services.audit import list_audit_events
from app.services.lifecycle import (
    record_application_lifecycle_event,
)


def test_lifecycle_events_are_recorded(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    initialize_database(engine)

    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    record_application_lifecycle_event(
        test_session_factory,
        "application_started",
    )

    record_application_lifecycle_event(
        test_session_factory,
        "application_stopped",
    )

    with test_session_factory() as database:
        events = list_audit_events(
            database
        )

    assert len(events) == 2

    assert (
        events[0].action
        == "application_stopped"
    )

    assert (
        events[1].action
        == "application_started"
    )

    engine.dispose()


@pytest.mark.asyncio
async def test_application_lifespan_is_audited(
    monkeypatch,
):
    initialize_mock = Mock()
    lifecycle_mock = Mock()

    monkeypatch.setattr(
        "app.main.initialize_database",
        initialize_mock,
    )

    monkeypatch.setattr(
        "app.main.record_application_lifecycle_event",
        lifecycle_mock,
    )

    async with lifespan(app):
        initialize_mock.assert_called_once()

        lifecycle_mock.assert_called_once_with(
            SessionLocal,
            "application_started",
        )

    assert lifecycle_mock.call_args_list == [
        call(
            SessionLocal,
            "application_started",
        ),
        call(
            SessionLocal,
            "application_stopped",
        ),
    ]