import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base, get_database
from app.main import app
from app.routes.conversations import (
    get_intel_notes_connector,
    get_model_runtime,
    get_risk_tracker_connector,
)
from app.runtime.contracts import (
    ModelRequest,
    ModelResponse,
    TokenUsage,
)


test_engine = create_engine(
    "sqlite://",
    connect_args={
        "check_same_thread": False,
    },
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)

Base.metadata.create_all(bind=test_engine)

client = TestClient(app)


class RecordingRuntime:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def generate(
        self,
        request: ModelRequest,
        *,
        provider_name: str | None = None,
    ) -> ModelResponse:
        self.requests.append(request)

        return ModelResponse(
            provider="deterministic",
            model="risk-api-test",
            content=(
                "Artemis analyzed the available "
                "protective intelligence context."
            ),
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=40,
                output_tokens=10,
                total_tokens=50,
            ),
        )


class StubIntelNotesConnector:
    def list_notes(
        self,
        *,
        keyword=None,
        **filters,
    ):
        return []


class StubRiskTrackerConnector:
    def __init__(self) -> None:
        self.list_calls = 0
        self.get_calls = 0
        self.related_calls = 0

        self.include_archived = None
        self.requested_case_id = None

    def list_cases(
        self,
        *,
        include_archived: bool = False,
    ):
        self.list_calls += 1
        self.include_archived = include_archived

        return [
            {
                "id": 41,
                "case_number": "PIR-041",
                "title": (
                    "API protective intelligence "
                    "case"
                ),
                "summary": (
                    "Verified Risk Tracker API "
                    "context."
                ),
                "status": "Open",
                "priority": "High",
                "risk_level": "Elevated",
            }
        ]

    def get_case(
        self,
        case_id: int,
    ):
        self.get_calls += 1
        self.requested_case_id = case_id

        return {
            "id": case_id,
            "case_number": f"PIR-{case_id:03d}",
            "title": "Detailed API risk case",
            "summary": (
                "Detailed protective case "
                "information."
            ),
            "status": "Open",
            "priority": "High",
            "risk_level": "Elevated",
        }

    def list_case_activities(
        self,
        case_id: int,
    ):
        self.related_calls += 1

        return [
            {
                "id": 201,
                "case_id": case_id,
                "activity_type": "Review",
                "description": (
                    "Analyst reviewed reporting."
                ),
            }
        ]

    def list_case_reviews(
        self,
        case_id: int,
    ):
        self.related_calls += 1

        return [
            {
                "id": 202,
                "case_id": case_id,
                "review_type": "Periodic",
                "findings": (
                    "Continued review required."
                ),
            }
        ]

    def list_case_indicators(
        self,
        case_id: int,
    ):
        self.related_calls += 1

        return [
            {
                "id": 203,
                "case_id": case_id,
                "indicator_type": "Behavioral",
                "description": (
                    "API test indicator."
                ),
            }
        ]

    def list_case_assessments(
        self,
        case_id: int,
    ):
        self.related_calls += 1

        return [
            {
                "id": 204,
                "case_id": case_id,
                "overall_risk": "Elevated",
                "rationale": (
                    "API test assessment."
                ),
            }
        ]

    def list_case_mitigations(
        self,
        case_id: int,
    ):
        self.related_calls += 1

        return [
            {
                "id": 205,
                "case_id": case_id,
                "action": "Continue monitoring",
                "status": "Open",
            }
        ]


test_runtime = RecordingRuntime()
test_risk_tracker_connector = (
    StubRiskTrackerConnector()
)
test_intel_notes_connector = (
    StubIntelNotesConnector()
)


def override_get_database():
    database = TestingSessionLocal()

    try:
        yield database
    finally:
        database.close()


def override_get_model_runtime():
    return test_runtime


def override_get_intel_notes_connector():
    return test_intel_notes_connector


def override_get_risk_tracker_connector():
    return test_risk_tracker_connector


@pytest.fixture(autouse=True)
def isolated_risk_reasoning_database():
    test_runtime.requests.clear()

    test_risk_tracker_connector.list_calls = 0
    test_risk_tracker_connector.get_calls = 0
    test_risk_tracker_connector.related_calls = 0
    test_risk_tracker_connector.include_archived = None
    test_risk_tracker_connector.requested_case_id = None

    app.dependency_overrides[
        get_database
    ] = override_get_database

    app.dependency_overrides[
        get_model_runtime
    ] = override_get_model_runtime

    app.dependency_overrides[
        get_intel_notes_connector
    ] = override_get_intel_notes_connector

    app.dependency_overrides[
        get_risk_tracker_connector
    ] = override_get_risk_tracker_connector

    with Session(test_engine) as database:
        database.execute(
            delete(models.AuditEvent)
        )

        database.execute(
            delete(models.Message)
        )

        database.execute(
            delete(models.Conversation)
        )

        database.commit()

    yield

    app.dependency_overrides.pop(
        get_database,
        None,
    )

    app.dependency_overrides.pop(
        get_model_runtime,
        None,
    )

    app.dependency_overrides.pop(
        get_intel_notes_connector,
        None,
    )

    app.dependency_overrides.pop(
        get_risk_tracker_connector,
        None,
    )


def create_conversation() -> dict:
    response = client.post(
        "/conversations",
        json={
            "title": (
                "Risk Tracker reasoning API test"
            ),
        },
    )

    assert response.status_code == 201

    return response.json()


def test_risk_retrieval_is_off_by_default():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Answer without Risk Tracker."
            ),
        },
    )

    assert response.status_code == 201

    assert (
        test_risk_tracker_connector.list_calls
        == 0
    )

    assert (
        test_risk_tracker_connector.get_calls
        == 0
    )

    assert len(test_runtime.requests) == 1

    system_message = (
        test_runtime.requests[0]
        .messages[0]
        .content
    )

    assert (
        "API protective intelligence case"
        not in system_message
    )


def test_api_can_list_risk_tracker_cases():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Summarize the current protective "
                "intelligence cases."
            ),
            "use_risk_tracker": True,
            "risk_tracker_include_archived": True,
        },
    )

    assert response.status_code == 201

    assert (
        test_risk_tracker_connector.list_calls
        == 1
    )

    assert (
        test_risk_tracker_connector.include_archived
        is True
    )

    assert (
        test_risk_tracker_connector.get_calls
        == 0
    )

    assert len(test_runtime.requests) == 1

    system_message = (
        test_runtime.requests[0]
        .messages[0]
        .content
    )

    assert (
        "API protective intelligence case"
        in system_message
    )

    assert (
        "Verified Risk Tracker API context."
        in system_message
    )

    assert (
        "<risk_tracker_records>"
        in system_message
    )


def test_api_can_retrieve_detailed_risk_case():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Analyze protective case 12."
            ),
            "use_risk_tracker": True,
            "risk_tracker_case_id": 12,
            "risk_tracker_include_related": True,
        },
    )

    assert response.status_code == 201

    assert (
        test_risk_tracker_connector.list_calls
        == 0
    )

    assert (
        test_risk_tracker_connector.get_calls
        == 1
    )

    assert (
        test_risk_tracker_connector
        .requested_case_id
        == 12
    )

    assert (
        test_risk_tracker_connector.related_calls
        == 5
    )

    assert len(test_runtime.requests) == 1

    system_message = (
        test_runtime.requests[0]
        .messages[0]
        .content
    )

    assert (
        "Detailed API risk case"
        in system_message
    )

    assert (
        "API test indicator."
        in system_message
    )

    assert (
        "API test assessment."
        in system_message
    )


def test_api_can_disable_related_case_records():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Retrieve only protective case 8."
            ),
            "use_risk_tracker": True,
            "risk_tracker_case_id": 8,
            "risk_tracker_include_related": False,
        },
    )

    assert response.status_code == 201

    assert (
        test_risk_tracker_connector.get_calls
        == 1
    )

    assert (
        test_risk_tracker_connector.related_calls
        == 0
    )


def test_invalid_risk_case_id_is_rejected():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Retrieve an invalid case."
            ),
            "use_risk_tracker": True,
            "risk_tracker_case_id": 0,
        },
    )

    assert response.status_code == 422

    assert (
        test_risk_tracker_connector.list_calls
        == 0
    )

    assert (
        test_risk_tracker_connector.get_calls
        == 0
    )

    assert test_runtime.requests == []


def test_missing_risk_credentials_fail_safely():
    conversation = create_conversation()

    app.dependency_overrides[
        get_risk_tracker_connector
    ] = lambda: None

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Retrieve protected cases."
            ),
            "use_risk_tracker": True,
        },
    )

    assert response.status_code == 503

    assert response.json() == {
        "detail": (
            "Risk Tracker credentials are "
            "not configured."
        )
    }

    assert test_runtime.requests == []