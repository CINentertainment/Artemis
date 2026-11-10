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
            model="risk-retrieval-test-model",
            content=(
                "Artemis analyzed the available "
                "Risk Tracker context."
            ),
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=30,
                output_tokens=10,
                total_tokens=40,
            ),
        )


class StubRiskTrackerConnector:
    def __init__(
        self,
        *,
        cases=None,
        case_record=None,
        unavailable: bool = False,
    ) -> None:
        self.cases = cases or []
        self.case_record = case_record
        self.unavailable = unavailable

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

        if self.unavailable:
            raise ConnectorUnavailableError(
                "Risk Tracker unavailable."
            )

        return self.cases

    def get_case(
        self,
        case_id: int,
    ):
        self.get_calls += 1
        self.requested_case_id = case_id

        if self.unavailable:
            raise ConnectorUnavailableError(
                "Risk Tracker unavailable."
            )

        if self.case_record is not None:
            return self.case_record

        return {
            "id": case_id,
            "case_number": f"CASE-{case_id}",
            "title": "Protective intelligence case",
            "summary": "Case summary.",
            "status": "Open",
            "risk_level": "Moderate",
        }

    def list_case_activities(
        self,
        case_id: int,
    ):
        self.related_calls += 1

        return [
            {
                "id": 101,
                "case_id": case_id,
                "activity_type": "Review",
                "description": (
                    "Analyst reviewed current "
                    "reporting."
                ),
                "actor": "Test Analyst",
            }
        ]

    def list_case_reviews(
        self,
        case_id: int,
    ):
        self.related_calls += 1

        return [
            {
                "id": 102,
                "case_id": case_id,
                "review_type": "Periodic",
                "findings": (
                    "Additional monitoring "
                    "recommended."
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
                "id": 103,
                "case_id": case_id,
                "indicator_type": "Behavioral",
                "description": (
                    "Repeated concerning contact."
                ),
                "credibility": "Unassessed",
            }
        ]

    def list_case_assessments(
        self,
        case_id: int,
    ):
        self.related_calls += 1

        return [
            {
                "id": 104,
                "case_id": case_id,
                "overall_risk": "Moderate",
                "rationale": (
                    "Assessment test rationale."
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
                "id": 105,
                "case_id": case_id,
                "action": "Continue monitoring",
                "status": "Open",
                "owner": "Test Team",
            }
        ]


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


def test_selected_turn_lists_risk_tracker_cases(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubRiskTrackerConnector(
        cases=[
            {
                "id": 7,
                "case_number": "PIR-007",
                "title": (
                    "Critical infrastructure case"
                ),
                "summary": (
                    "Protective intelligence "
                    "monitoring is active."
                ),
                "status": "Open",
                "risk_level": "Moderate",
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
                "Summarize the current protective "
                "intelligence cases."
            ),
            runtime=runtime,
            use_risk_tracker=True,
            risk_tracker_connector=connector,
        )

        assert connector.list_calls == 1
        assert connector.get_calls == 0

        assert (
            connector.include_archived
            is False
        )

        assert runtime.request is not None

        system_message = (
            runtime.request.messages[0]
        )

        assert (
            "Critical infrastructure case"
            in system_message.content
        )

        assert (
            "Protective intelligence "
            "monitoring is active."
            in system_message.content
        )

        assert (
            "<risk_tracker_records>"
            in system_message.content
        )

        assert (
            result.risk_retrieval_result
            is not None
        )

        assert (
            result.risk_retrieval_result
            .case_count
            == 1
        )

        assert (
            result.risk_retrieval_result
            .related_record_count
            == 0
        )

    engine.dispose()


def test_selected_turn_retrieves_detailed_case(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubRiskTrackerConnector(
        case_record={
            "id": 12,
            "case_number": "PIR-012",
            "title": "Detailed case test",
            "summary": (
                "Detailed private case summary."
            ),
            "status": "Open",
            "priority": "High",
            "risk_level": "Elevated",
        }
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
                "Analyze protective case 12."
            ),
            runtime=runtime,
            use_risk_tracker=True,
            risk_tracker_case_id=12,
            risk_tracker_include_related=True,
            risk_tracker_connector=connector,
        )

        assert connector.list_calls == 0
        assert connector.get_calls == 1

        assert (
            connector.requested_case_id
            == 12
        )

        assert connector.related_calls == 5

        assert runtime.request is not None

        system_message = (
            runtime.request.messages[0]
        )

        assert (
            "Detailed case test"
            in system_message.content
        )

        assert (
            "Repeated concerning contact."
            in system_message.content
        )

        assert (
            "Assessment test rationale."
            in system_message.content
        )

        assert (
            result.risk_retrieval_result
            is not None
        )

        assert (
            result.risk_retrieval_result
            .case_count
            == 1
        )

        assert (
            result.risk_retrieval_result
            .related_record_count
            == 5
        )

    engine.dispose()


def test_related_records_can_be_disabled(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubRiskTrackerConnector()
    runtime = RecordingRuntime()

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        result = run_conversation_turn(
            database,
            conversation=conversation,
            user_content=(
                "Retrieve only the main case."
            ),
            runtime=runtime,
            use_risk_tracker=True,
            risk_tracker_case_id=3,
            risk_tracker_include_related=False,
            risk_tracker_connector=connector,
        )

        assert connector.get_calls == 1
        assert connector.related_calls == 0

        assert (
            result.risk_retrieval_result
            is not None
        )

        assert (
            result.risk_retrieval_result
            .related_record_count
            == 0
        )

    engine.dispose()


def test_risk_retrieval_is_disabled_by_default(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubRiskTrackerConnector(
        cases=[
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
            risk_tracker_connector=connector,
        )

        assert connector.list_calls == 0
        assert connector.get_calls == 0

        assert (
            result.risk_retrieval_result
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


def test_offline_risk_tracker_does_not_break_turn(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubRiskTrackerConnector(
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
                "Review unavailable risk cases."
            ),
            runtime=runtime,
            use_risk_tracker=True,
            risk_tracker_connector=connector,
        )

        assert (
            result.assistant_message
            is not None
        )

        assert (
            result.risk_retrieval_result
            is not None
        )

        assert (
            result.risk_retrieval_result
            .available
            is False
        )

        assert runtime.request is not None

        assert (
            "<risk_tracker_records>"
            not in (
                runtime.request.messages[0]
                .content
            )
        )

    engine.dispose()


def test_risk_content_is_not_saved_as_message(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    private_record = (
        "Sensitive protective case content."
    )

    connector = StubRiskTrackerConnector(
        cases=[
            {
                "id": 21,
                "case_number": "PRIVATE-21",
                "title": "Private case",
                "summary": private_record,
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
                "Review protective cases."
            ),
            runtime=RecordingRuntime(),
            use_risk_tracker=True,
            risk_tracker_connector=connector,
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


def test_risk_content_is_not_in_audit_log(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    private_record = (
        "Do not store this risk record "
        "in the audit log."
    )

    connector = StubRiskTrackerConnector(
        cases=[
            {
                "id": 31,
                "case_number": "AUDIT-31",
                "title": "Audit privacy case",
                "summary": private_record,
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
                "Retrieve the audit privacy case."
            ),
            runtime=RecordingRuntime(),
            use_risk_tracker=True,
            risk_tracker_connector=connector,
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
            "risk_tracker_cases=1"
            in (response_event.detail or "")
        )

        assert (
            "risk_tracker_related_records=0"
            in (response_event.detail or "")
        )

    engine.dispose()