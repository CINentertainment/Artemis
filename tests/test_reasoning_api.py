import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base, get_database
from app.main import app
from app.routes.conversations import (
    get_intel_notes_connector,
    get_model_runtime,
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
            model="artemis-api-test",
            content=(
                "Artemis generated a private "
                "local response."
            ),
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=20,
                output_tokens=7,
                total_tokens=27,
            ),
        )


class FailingRuntime:
    def generate(
        self,
        request: ModelRequest,
        *,
        provider_name: str | None = None,
    ) -> ModelResponse:
        raise RuntimeError(
            "Private provider failure."
        )


class StubIntelNotesConnector:
    def __init__(self) -> None:
        self.calls = 0
        self.keyword = None

    def list_notes(
        self,
        *,
        keyword=None,
        **filters,
    ):
        self.calls += 1
        self.keyword = keyword

        return [
            {
                "id": 17,
                "title": (
                    "Retrieved API intelligence"
                ),
                "body": (
                    "Verified local record content."
                ),
                "classification": (
                    "UNCLASSIFIED"
                ),
            }
        ]


test_runtime = RecordingRuntime()

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


@pytest.fixture(autouse=True)
def isolated_reasoning_database():
    test_runtime.requests.clear()

    test_intel_notes_connector.calls = 0
    test_intel_notes_connector.keyword = None

    app.dependency_overrides[
        get_database
    ] = override_get_database

    app.dependency_overrides[
        get_model_runtime
    ] = override_get_model_runtime

    app.dependency_overrides[
        get_intel_notes_connector
    ] = override_get_intel_notes_connector

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


def create_conversation() -> dict:
    response = client.post(
        "/conversations",
        json={
            "title": "Reasoning API test",
        },
    )

    assert response.status_code == 201

    return response.json()


def test_reasoning_endpoint_returns_response():
    conversation = create_conversation()

    private_message = (
        "Prepare a concise local assessment."
    )

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": private_message,
        },
    )

    assert response.status_code == 201

    result = response.json()

    assert (
        result["user_message"]["content"]
        == private_message
    )

    assert (
        result["assistant_message"]["content"]
        == (
            "Artemis generated a private "
            "local response."
        )
    )

    assert result["provider"] == (
        "deterministic"
    )

    assert result["model"] == (
        "artemis-api-test"
    )

    assert result["finish_reason"] == "stop"
    assert result["input_tokens"] == 20
    assert result["output_tokens"] == 7
    assert result["total_tokens"] == 27


def test_reasoning_messages_are_encrypted():
    conversation = create_conversation()

    private_message = (
        "Sensitive intelligence requirement."
    )

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": private_message,
        },
    )

    assert response.status_code == 201

    with Session(test_engine) as database:
        messages = list(
            database.scalars(
                select(models.Message)
                .order_by(
                    models.Message.id.asc()
                )
            ).all()
        )

        assert len(messages) == 2

        assert messages[0].content.startswith(
            "dpapi:v1:"
        )

        assert messages[1].content.startswith(
            "dpapi:v1:"
        )

        assert private_message not in (
            messages[0].content
        )

        assert (
            "Artemis generated"
            not in messages[1].content
        )


def test_reasoning_endpoint_uses_history():
    conversation = create_conversation()

    first_response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": "First question",
        },
    )

    second_response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": "Follow-up question",
        },
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert len(test_runtime.requests) == 2

    second_request = test_runtime.requests[1]

    assert [
        message.role
        for message in second_request.messages
    ] == [
        "system",
        "user",
        "assistant",
        "user",
    ]

    assert (
        second_request.messages[-1].content
        == "Follow-up question"
    )


def test_archived_conversation_rejects_reasoning():
    conversation = create_conversation()

    archive_response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/archive"
        )
    )

    assert archive_response.status_code == 200

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": "Rejected question",
        },
    )

    assert response.status_code == 409

    assert response.json() == {
        "detail": (
            "Messages cannot be added to an "
            "inactive conversation."
        )
    }


def test_runtime_failure_is_private():
    conversation = create_conversation()

    app.dependency_overrides[
        get_model_runtime
    ] = lambda: FailingRuntime()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Private failed request"
            ),
        },
    )

    assert response.status_code == 503

    assert response.json() == {
        "detail": (
            "Artemis could not generate a "
            "response."
        )
    }

    assert (
        "Private provider failure"
        not in response.text
    )


def test_intel_notes_retrieval_is_off_by_default():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Answer without retrieval."
            ),
        },
    )

    assert response.status_code == 201
    assert test_intel_notes_connector.calls == 0
    assert len(test_runtime.requests) == 1

    system_message = (
        test_runtime.requests[0].messages[0]
    )

    assert (
        "Retrieved API intelligence"
        not in system_message.content
    )


def test_api_can_enable_intel_notes_retrieval():
    conversation = create_conversation()

    user_question = (
        "Review the retrieved intelligence."
    )

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": user_question,
            "use_intel_notes": True,
        },
    )

    assert response.status_code == 201
    assert test_intel_notes_connector.calls == 1

    assert (
        test_intel_notes_connector.keyword
        == user_question
    )

    assert len(test_runtime.requests) == 1

    system_message = (
        test_runtime.requests[0].messages[0]
    )

    assert (
        "Retrieved API intelligence"
        in system_message.content
    )

    assert (
        "Verified local record content."
        in system_message.content
    )

    assert (
        "<intel_notes_records>"
        in system_message.content
    )