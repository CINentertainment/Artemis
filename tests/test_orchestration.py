from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import Settings
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.runtime.contracts import (
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from app.services.conversations import (
    add_message,
    archive_conversation,
    create_conversation,
    list_messages,
    read_message_content,
)
from app.services.encryption import (
    is_protected_value,
)
from app.services.orchestration import (
    ConversationOrchestrationError,
    build_conversation_context,
    run_conversation_turn,
)


class RecordingRuntime:
    def __init__(
        self,
        response_content: str = (
            "Local Artemis response."
        ),
    ) -> None:
        self.response_content = (
            response_content
        )

        self.request: (
            ModelRequest | None
        ) = None

        self.provider_name: (
            str | None
        ) = None

    def generate(
        self,
        request: ModelRequest,
        *,
        provider_name: str | None = None,
    ) -> ModelResponse:
        self.request = request
        self.provider_name = provider_name

        return ModelResponse(
            provider="deterministic",
            model="artemis-test-model",
            content=self.response_content,
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=12,
                output_tokens=4,
                total_tokens=16,
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
            "Private runtime failure detail."
        )


def build_test_engine(
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


def test_conversation_turn_is_stored_encrypted(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = create_conversation(
            database,
            title="Local reasoning test",
        )

        runtime = RecordingRuntime(
            "Artemis completed the response."
        )

        result = run_conversation_turn(
            database,
            conversation=conversation,
            user_content=(
                "Provide a private assessment."
            ),
            runtime=runtime,
        )

        messages = list_messages(
            database,
            conversation_id=conversation.id,
        )

        assert len(messages) == 2

        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

        assert is_protected_value(
            messages[0].content
        )

        assert is_protected_value(
            messages[1].content
        )

        assert read_message_content(
            messages[0]
        ) == "Provide a private assessment."

        assert read_message_content(
            messages[1]
        ) == (
            "Artemis completed the response."
        )

        assert (
            result.assistant_message.model_name
            == "artemis-test-model"
        )

    engine.dispose()


def test_context_contains_ordered_history(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        add_message(
            database,
            conversation=conversation,
            role="user",
            content="First question",
        )

        add_message(
            database,
            conversation=conversation,
            role="assistant",
            content="First answer",
            model_name="previous-model",
        )

        context = build_conversation_context(
            database,
            conversation=conversation,
            system_prompt=(
                "You are the private Artemis "
                "test assistant."
            ),
        )

        assert [
            message.role
            for message in context
        ] == [
            "system",
            "user",
            "assistant",
        ]

        assert context[0].content == (
            "You are the private Artemis "
            "test assistant."
        )

        assert context[1].content == (
            "First question"
        )

        assert context[2].content == (
            "First answer"
        )

    engine.dispose()


def test_turn_sends_history_and_new_message(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        add_message(
            database,
            conversation=conversation,
            role="user",
            content="Earlier question",
        )

        add_message(
            database,
            conversation=conversation,
            role="assistant",
            content="Earlier answer",
        )

        runtime = RecordingRuntime()

        run_conversation_turn(
            database,
            conversation=conversation,
            user_content="Current question",
            runtime=runtime,
            provider_name="deterministic",
        )

        assert runtime.request is not None

        assert [
            message.role
            for message
            in runtime.request.messages
        ] == [
            "system",
            "user",
            "assistant",
            "user",
        ]

        assert (
            runtime.request.messages[-1].content
            == "Current question"
        )

        assert (
            runtime.provider_name
            == "deterministic"
        )

    engine.dispose()


def test_archived_conversation_rejects_turn(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        archive_conversation(
            database,
            conversation,
        )

        with pytest.raises(
            ValueError,
            match="inactive conversation",
        ):
            run_conversation_turn(
                database,
                conversation=conversation,
                user_content="Rejected message",
                runtime=RecordingRuntime(),
            )

    engine.dispose()


def test_runtime_failure_is_audited_safely(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        private_content = (
            "Sensitive private question."
        )

        with pytest.raises(
            ConversationOrchestrationError,
            match="could not generate",
        ):
            run_conversation_turn(
                database,
                conversation=conversation,
                user_content=private_content,
                runtime=FailingRuntime(),
            )

        audit_event = database.scalar(
            select(models.AuditEvent)
            .where(
                models.AuditEvent.action
                == "model_response_generated"
            )
            .order_by(
                models.AuditEvent.id.desc()
            )
        )

        assert audit_event is not None
        assert audit_event.outcome == "failure"

        assert private_content not in (
            audit_event.detail or ""
        )

        assert (
            "Private runtime failure detail"
            not in (audit_event.detail or "")
        )

        messages = list_messages(
            database,
            conversation_id=conversation.id,
        )

        assert len(messages) == 1
        assert messages[0].role == "user"

    engine.dispose()


def test_successful_turn_records_safe_audit(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        run_conversation_turn(
            database,
            conversation=conversation,
            user_content="Analyze this.",
            runtime=RecordingRuntime(),
        )

        audit_event = database.scalar(
            select(models.AuditEvent)
            .where(
                models.AuditEvent.action
                == "model_response_generated"
            )
            .order_by(
                models.AuditEvent.id.desc()
            )
        )

        assert audit_event is not None
        assert audit_event.outcome == "success"

        assert "provider=deterministic" in (
            audit_event.detail or ""
        )

        assert "Analyze this." not in (
            audit_event.detail or ""
        )

    engine.dispose()