from pathlib import Path

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
from app.runtime.contracts import (
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from app.services.conversations import (
    create_conversation,
    list_messages,
    read_message_content,
)
from app.services.orchestration import (
    build_conversation_context,
    run_conversation_turn,
)


class RecordingRuntime:
    def __init__(self) -> None:
        self.request: (
            ModelRequest | None
        ) = None

    def generate(
        self,
        request: ModelRequest,
        *,
        provider_name: str | None = None,
    ) -> ModelResponse:
        self.request = request

        return ModelResponse(
            provider="deterministic",
            model="retrieval-test-model",
            content=(
                "Artemis analyzed the available "
                "local context."
            ),
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=20,
                output_tokens=8,
                total_tokens=28,
            ),
        )


class StubIntelNotesConnector:
    def __init__(
        self,
        *,
        notes=None,
        unavailable: bool = False,
    ) -> None:
        self.notes = notes or []
        self.unavailable = unavailable
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

        if self.unavailable:
            raise ConnectorUnavailableError(
                "Private connector failure."
            )

        return self.notes


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


def test_supplemental_context_enters_system_message(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        context = build_conversation_context(
            database,
            conversation=conversation,
            supplemental_context=(
                "Retrieved record number 42."
            ),
        )

        assert len(context) == 1

        assert (
            "Supplemental retrieved context"
            in context[0].content
        )

        assert (
            "Retrieved record number 42."
            in context[0].content
        )

    engine.dispose()


def test_selected_turn_retrieves_intel_notes(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubIntelNotesConnector(
        notes=[
            {
                "id": 7,
                "title": (
                    "Critical infrastructure "
                    "observation"
                ),
                "body": (
                    "Observed a relevant change."
                ),
                "classification": (
                    "UNCLASSIFIED"
                ),
            }
        ]
    )

    runtime = RecordingRuntime()

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        result = run_conversation_turn(
            database,
            conversation=conversation,
            user_content=(
                "Review critical infrastructure."
            ),
            runtime=runtime,
            use_intel_notes=True,
            intel_notes_connector=connector,
        )

        assert connector.calls == 1

        assert connector.keyword == (
            "Review critical infrastructure."
        )

        assert runtime.request is not None

        system_message = (
            runtime.request.messages[0]
        )

        assert (
            "Critical infrastructure "
            "observation"
            in system_message.content
        )

        assert (
            "Observed a relevant change."
            in system_message.content
        )

        assert (
            result.retrieval_result
            is not None
        )

        assert (
            result.retrieval_result
            .record_count
            == 1
        )

    engine.dispose()


def test_retrieval_is_disabled_by_default(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubIntelNotesConnector(
        notes=[
            {
                "id": 1,
                "title": (
                    "Must not be retrieved"
                ),
            }
        ]
    )

    runtime = RecordingRuntime()

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        result = run_conversation_turn(
            database,
            conversation=conversation,
            user_content="Normal conversation.",
            runtime=runtime,
            intel_notes_connector=connector,
        )

        assert connector.calls == 0

        assert (
            result.retrieval_result
            is None
        )

        assert runtime.request is not None

        assert (
            "Must not be retrieved"
            not in (
                runtime.request.messages[0]
                .content
            )
        )

    engine.dispose()


def test_offline_intel_notes_does_not_break_turn(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubIntelNotesConnector(
        unavailable=True
    )

    runtime = RecordingRuntime()

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        result = run_conversation_turn(
            database,
            conversation=conversation,
            user_content=(
                "Search unavailable records."
            ),
            runtime=runtime,
            use_intel_notes=True,
            intel_notes_connector=connector,
        )

        assert (
            result.assistant_message
            is not None
        )

        assert (
            result.retrieval_result
            is not None
        )

        assert (
            result.retrieval_result.available
            is False
        )

        assert runtime.request is not None

        assert (
            "<intel_notes_records>"
            not in (
                runtime.request.messages[0]
                .content
            )
        )

    engine.dispose()


def test_retrieved_content_is_not_saved_as_message(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    private_record = (
        "Sensitive retrieved record content."
    )

    connector = StubIntelNotesConnector(
        notes=[
            {
                "id": 9,
                "title": "Private record",
                "body": private_record,
            }
        ]
    )

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        run_conversation_turn(
            database,
            conversation=conversation,
            user_content=(
                "Review the local records."
            ),
            runtime=RecordingRuntime(),
            use_intel_notes=True,
            intel_notes_connector=connector,
        )

        messages = list_messages(
            database,
            conversation_id=conversation.id,
        )

        assert len(messages) == 2

        stored_plaintext = [
            read_message_content(message)
            for message in messages
        ]

        assert all(
            private_record not in content
            for content in stored_plaintext
        )

    engine.dispose()


def test_retrieved_content_is_not_in_audit_log(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    private_record = (
        "Do not store this record in audit."
    )

    connector = StubIntelNotesConnector(
        notes=[
            {
                "id": 11,
                "title": "Audit privacy test",
                "body": private_record,
            }
        ]
    )

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        run_conversation_turn(
            database,
            conversation=conversation,
            user_content=(
                "Retrieve audit privacy test."
            ),
            runtime=RecordingRuntime(),
            use_intel_notes=True,
            intel_notes_connector=connector,
        )

        events = database.scalars(
            select(models.AuditEvent)
        ).all()

        assert any(
            event.event_type == "retrieval"
            for event in events
        )

        assert all(
            private_record not in (
                event.detail or ""
            )
            for event in events
        )

        response_event = next(
            event
            for event in events
            if (
                event.action
                == "model_response_generated"
            )
        )

        assert (
            "intel_notes_records=1"
            in (response_event.detail or "")
        )

    engine.dispose()
def test_retrieval_prompt_blocks_undefined_rating_inference(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        context = build_conversation_context(
            database,
            conversation=conversation,
            supplemental_context=(
                "Risk level: Moderate. "
                "Mitigation status: Planned."
            ),
        )

        system_message = context[0].content

        assert (
            "Never interpret, translate, rank"
            in system_message
        )

        assert (
            "Do not infer urgency, severity"
            in system_message
        )

        assert (
            "actions taken, or actions not taken"
            in system_message
        )

        assert (
            "meaning is not defined in the "
            "available context"
            in system_message
        )

    engine.dispose()