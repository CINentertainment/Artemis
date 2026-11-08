from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import Settings
from app.connectors.base import (
    ConnectorUnavailableError,
)
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.services.retrieval import (
    MAXIMUM_RESULTS,
    build_intel_notes_context,
    clean_search_query,
    retrieve_intel_notes,
)


class StubIntelNotesConnector:
    def __init__(
        self,
        notes=None,
        *,
        unavailable: bool = False,
    ) -> None:
        self.notes = notes or []
        self.unavailable = unavailable
        self.keyword = None

    def list_notes(
        self,
        *,
        keyword=None,
        **filters,
    ):
        self.keyword = keyword

        if self.unavailable:
            raise ConnectorUnavailableError(
                "Private connection detail."
            )

        return self.notes


def build_retrieval_test_engine(
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


def test_retrieval_searches_intel_notes(
    tmp_path: Path,
):
    engine = build_retrieval_test_engine(
        tmp_path
    )

    connector = StubIntelNotesConnector(
        notes=[
            {
                "id": 1,
                "title": "Infrastructure report",
                "body": (
                    "Observed infrastructure "
                    "development."
                ),
                "classification": (
                    "UNCLASSIFIED"
                ),
            }
        ]
    )

    with Session(engine) as database:
        result = retrieve_intel_notes(
            database,
            connector=connector,
            query=" infrastructure ",
        )

        assert (
            connector.keyword
            == "infrastructure"
        )

        assert result.available is True
        assert result.record_count == 1
        assert result.context is not None

        assert (
            "Infrastructure report"
            in result.context
        )

        assert (
            "Observed infrastructure "
            "development."
            in result.context
        )

        assert (
            "<intel_notes_records>"
            in result.context
        )

        assert (
            "untrusted reference data"
            in result.context
        )

    engine.dispose()


def test_retrieval_limits_results(
    tmp_path: Path,
):
    engine = build_retrieval_test_engine(
        tmp_path
    )

    connector = StubIntelNotesConnector(
        notes=[
            {
                "id": index,
                "title": f"Note {index}",
            }
            for index in range(50)
        ]
    )

    with Session(engine) as database:
        result = retrieve_intel_notes(
            database,
            connector=connector,
            query="note",
            maximum_results=1000,
        )

        assert (
            result.record_count
            == MAXIMUM_RESULTS
        )

        assert result.context is not None
        assert "Note 19" in result.context
        assert "Note 20" not in result.context

    engine.dispose()


def test_empty_results_return_no_context(
    tmp_path: Path,
):
    engine = build_retrieval_test_engine(
        tmp_path
    )

    connector = StubIntelNotesConnector()

    with Session(engine) as database:
        result = retrieve_intel_notes(
            database,
            connector=connector,
            query="missing",
        )

        assert result.available is True
        assert result.record_count == 0
        assert result.context is None
        assert result.error is None

    engine.dispose()


def test_unavailable_connector_fails_gracefully(
    tmp_path: Path,
):
    engine = build_retrieval_test_engine(
        tmp_path
    )

    connector = StubIntelNotesConnector(
        unavailable=True
    )

    with Session(engine) as database:
        result = retrieve_intel_notes(
            database,
            connector=connector,
            query="private subject",
        )

        assert result.available is False
        assert result.record_count == 0
        assert result.context is None

        assert result.error == (
            "Intel Notes is currently "
            "unavailable."
        )

    engine.dispose()


def test_retrieved_fields_are_limited():
    private_body = "A" * 5000

    context = build_intel_notes_context(
        [
            {
                "id": 1,
                "title": "Test",
                "body": private_body,
            }
        ],
        maximum_field_length=200,
    )

    assert context is not None
    assert "A" * 200 in context
    assert "A" * 201 not in context


def test_unknown_fields_are_excluded():
    context = build_intel_notes_context(
        [
            {
                "id": 1,
                "title": "Allowed title",
                "internal_secret": (
                    "must-not-enter-context"
                ),
            }
        ]
    )

    assert context is not None

    assert (
        "Allowed title"
        in context
    )

    assert (
        "must-not-enter-context"
        not in context
    )

    assert (
        "internal_secret"
        not in context
    )


@pytest.mark.parametrize(
    "query",
    [
        "",
        " ",
        "\n",
    ],
)
def test_empty_query_is_rejected(
    query: str,
):
    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        clean_search_query(query)


def test_retrieval_audit_does_not_store_content(
    tmp_path: Path,
):
    engine = build_retrieval_test_engine(
        tmp_path
    )

    private_query = (
        "Sensitive investigation subject"
    )

    private_note_content = (
        "Sensitive retrieved intelligence"
    )

    connector = StubIntelNotesConnector(
        notes=[
            {
                "id": 1,
                "title": "Private Note",
                "body": private_note_content,
            }
        ]
    )

    with Session(engine) as database:
        retrieve_intel_notes(
            database,
            connector=connector,
            query=private_query,
        )

        events = database.scalars(
            select(models.AuditEvent)
        ).all()

        assert len(events) == 2

        for event in events:
            assert private_query not in (
                event.detail or ""
            )

            assert private_note_content not in (
                event.detail or ""
            )

    engine.dispose()