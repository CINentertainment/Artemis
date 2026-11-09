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
from app.services.risk_retrieval import (
    MAXIMUM_CASES,
    build_risk_tracker_context,
    retrieve_risk_tracker_case,
    retrieve_risk_tracker_cases,
)


class StubRiskTrackerConnector:
    def __init__(
        self,
        *,
        cases=None,
        case_record=None,
        activities=None,
        reviews=None,
        indicators=None,
        assessments=None,
        mitigations=None,
        unavailable: bool = False,
    ) -> None:
        self.cases = cases or []
        self.case_record = (
            case_record
            or {
                "id": 1,
                "case_number": "PIRT-001",
                "title": "Test case",
                "summary": "Test summary",
            }
        )

        self.activities = activities or []
        self.reviews = reviews or []
        self.indicators = indicators or []
        self.assessments = assessments or []
        self.mitigations = mitigations or []
        self.unavailable = unavailable

        self.include_archived = None
        self.requested_case_ids: list[
            int
        ] = []

    def fail_if_unavailable(
        self,
    ) -> None:
        if self.unavailable:
            raise ConnectorUnavailableError(
                "Private connection information."
            )

    def list_cases(
        self,
        *,
        include_archived: bool = False,
    ):
        self.fail_if_unavailable()
        self.include_archived = include_archived

        return self.cases

    def get_case(
        self,
        case_id: int,
    ):
        self.fail_if_unavailable()

        self.requested_case_ids.append(
            case_id
        )

        return self.case_record

    def list_case_activities(
        self,
        case_id: int,
    ):
        self.fail_if_unavailable()

        self.requested_case_ids.append(
            case_id
        )

        return self.activities

    def list_case_reviews(
        self,
        case_id: int,
    ):
        self.fail_if_unavailable()

        self.requested_case_ids.append(
            case_id
        )

        return self.reviews

    def list_case_indicators(
        self,
        case_id: int,
    ):
        self.fail_if_unavailable()

        self.requested_case_ids.append(
            case_id
        )

        return self.indicators

    def list_case_assessments(
        self,
        case_id: int,
    ):
        self.fail_if_unavailable()

        self.requested_case_ids.append(
            case_id
        )

        return self.assessments

    def list_case_mitigations(
        self,
        case_id: int,
    ):
        self.fail_if_unavailable()

        self.requested_case_ids.append(
            case_id
        )

        return self.mitigations


def build_risk_retrieval_test_engine(
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


def test_lists_risk_tracker_cases(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubRiskTrackerConnector(
        cases=[
            {
                "id": 4,
                "case_number": "PIRT-004",
                "title": "Protective case",
                "summary": (
                    "Observed protective concern."
                ),
                "status": "Open",
                "priority": "Elevated",
                "risk_level": "Moderate",
            }
        ]
    )

    with Session(engine) as database:
        result = retrieve_risk_tracker_cases(
            database,
            connector=connector,
        )

        assert (
            connector.include_archived
            is False
        )

        assert result.available is True
        assert result.case_count == 1
        assert (
            result.related_record_count
            == 0
        )

        assert result.context is not None

        assert "PIRT-004" in result.context
        assert (
            "Observed protective concern."
            in result.context
        )

        assert (
            "<risk_tracker_records>"
            in result.context
        )

        assert (
            "untrusted reference data"
            in result.context
        )

    engine.dispose()


def test_case_list_limit_is_enforced(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubRiskTrackerConnector(
        cases=[
            {
                "id": index,
                "case_number": (
                    f"PIRT-{index:03d}"
                ),
                "title": f"Case {index}",
            }
            for index in range(1, 51)
        ]
    )

    with Session(engine) as database:
        result = retrieve_risk_tracker_cases(
            database,
            connector=connector,
            maximum_cases=1000,
        )

        assert (
            result.case_count
            == MAXIMUM_CASES
        )

        assert result.context is not None
        assert "PIRT-020" in result.context
        assert "PIRT-021" not in result.context

    engine.dispose()


def test_empty_case_list_returns_no_context(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubRiskTrackerConnector()

    with Session(engine) as database:
        result = retrieve_risk_tracker_cases(
            database,
            connector=connector,
        )

        assert result.available is True
        assert result.case_count == 0
        assert result.context is None
        assert result.error is None

    engine.dispose()


def test_retrieves_case_and_related_records(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubRiskTrackerConnector(
        case_record={
            "id": 3,
            "case_number": "PIRT-003",
            "title": "Detailed case",
            "summary": "Detailed summary",
            "risk_level": "High",
        },
        activities=[
            {
                "id": 10,
                "case_id": 3,
                "activity_type": "Review",
                "description": (
                    "Case reviewed by analyst."
                ),
                "actor": "Analyst",
            }
        ],
        reviews=[
            {
                "id": 11,
                "case_id": 3,
                "findings": (
                    "Continued monitoring required."
                ),
            }
        ],
        indicators=[
            {
                "id": 12,
                "case_id": 3,
                "indicator_type": (
                    "Behavioral"
                ),
                "description": (
                    "Observed concerning behavior."
                ),
                "credibility": "Confirmed",
            }
        ],
        assessments=[
            {
                "id": 13,
                "case_id": 3,
                "threat_level": "High",
                "vulnerability_level": (
                    "Moderate"
                ),
                "impact_level": "High",
                "overall_risk": "High",
            }
        ],
        mitigations=[
            {
                "id": 14,
                "case_id": 3,
                "description": (
                    "Increase protective monitoring."
                ),
                "status": "Open",
            }
        ],
    )

    with Session(engine) as database:
        result = retrieve_risk_tracker_case(
            database,
            connector=connector,
            case_id=3,
        )

        assert result.available is True
        assert result.case_count == 1

        assert (
            result.related_record_count
            == 5
        )

        assert result.context is not None

        assert "PIRT-003" in result.context

        assert (
            "Observed concerning behavior."
            in result.context
        )

        assert (
            "Increase protective monitoring."
            in result.context
        )

        assert (
            connector.requested_case_ids
            == [3, 3, 3, 3, 3, 3]
        )

    engine.dispose()


def test_related_records_can_be_excluded(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubRiskTrackerConnector(
        case_record={
            "id": 6,
            "case_number": "PIRT-006",
            "title": "Case only",
        },
        indicators=[
            {
                "id": 20,
                "case_id": 6,
                "description": (
                    "Should not be retrieved."
                ),
            }
        ],
    )

    with Session(engine) as database:
        result = retrieve_risk_tracker_case(
            database,
            connector=connector,
            case_id=6,
            include_related=False,
        )

        assert result.available is True

        assert (
            result.related_record_count
            == 0
        )

        assert (
            connector.requested_case_ids
            == [6]
        )

        assert result.context is not None

        assert (
            "Should not be retrieved."
            not in result.context
        )

    engine.dispose()


def test_unknown_fields_are_excluded():
    context = build_risk_tracker_context(
        [
            {
                "case": {
                    "id": 1,
                    "case_number": "PIRT-001",
                    "title": "Allowed title",
                    "password_hash": (
                        "must-not-enter-context"
                    ),
                    "private_token": (
                        "also-must-not-enter-context"
                    ),
                },
                "indicators": [
                    {
                        "id": 2,
                        "case_id": 1,
                        "description": (
                            "Allowed indicator"
                        ),
                        "internal_secret": (
                            "hidden-value"
                        ),
                    }
                ],
            }
        ]
    )

    assert context is not None
    assert "Allowed title" in context
    assert "Allowed indicator" in context

    assert (
        "must-not-enter-context"
        not in context
    )

    assert (
        "also-must-not-enter-context"
        not in context
    )

    assert "hidden-value" not in context
    assert "password_hash" not in context
    assert "private_token" not in context
    assert "internal_secret" not in context


def test_retrieved_fields_are_limited():
    private_summary = "A" * 5000

    context = build_risk_tracker_context(
        [
            {
                "case": {
                    "id": 1,
                    "case_number": "PIRT-001",
                    "summary": private_summary,
                }
            }
        ],
        maximum_field_length=200,
    )

    assert context is not None
    assert "A" * 200 in context
    assert "A" * 201 not in context


def test_invalid_case_id_is_rejected(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubRiskTrackerConnector()

    with Session(engine) as database:
        with pytest.raises(
            ValueError,
            match="greater than zero",
        ):
            retrieve_risk_tracker_case(
                database,
                connector=connector,
                case_id=0,
            )

    engine.dispose()


def test_unavailable_case_list_fails_safely(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubRiskTrackerConnector(
        unavailable=True
    )

    with Session(engine) as database:
        result = retrieve_risk_tracker_cases(
            database,
            connector=connector,
        )

        assert result.available is False
        assert result.case_count == 0
        assert result.context is None

        assert result.error == (
            "Risk Tracker is currently "
            "unavailable."
        )

    engine.dispose()


def test_unavailable_case_detail_fails_safely(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubRiskTrackerConnector(
        unavailable=True
    )

    with Session(engine) as database:
        result = retrieve_risk_tracker_case(
            database,
            connector=connector,
            case_id=8,
        )

        assert result.available is False
        assert result.case_count == 0
        assert result.context is None

        assert result.error == (
            "Risk Tracker is currently "
            "unavailable."
        )

    engine.dispose()


def test_risk_retrieval_audit_excludes_content(
    tmp_path: Path,
):
    engine = (
        build_risk_retrieval_test_engine(
            tmp_path
        )
    )

    private_summary = (
        "Sensitive protective case summary"
    )

    private_indicator = (
        "Sensitive behavioral indicator"
    )

    connector = StubRiskTrackerConnector(
        case_record={
            "id": 9,
            "case_number": "PIRT-009",
            "title": "Private case",
            "summary": private_summary,
        },
        indicators=[
            {
                "id": 30,
                "case_id": 9,
                "indicator_type": "Behavioral",
                "description": private_indicator,
            }
        ],
    )

    with Session(engine) as database:
        retrieve_risk_tracker_case(
            database,
            connector=connector,
            case_id=9,
        )

        events = database.scalars(
            select(models.AuditEvent)
        ).all()

        assert len(events) == 2

        for event in events:
            assert private_summary not in (
                event.detail or ""
            )

            assert private_indicator not in (
                event.detail or ""
            )

    engine.dispose()