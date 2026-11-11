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
            model="market-retrieval-test-model",
            content=(
                "Artemis analyzed the available "
                "Market Lens context."
            ),
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=30,
                output_tokens=10,
                total_tokens=40,
            ),
        )


class StubMarketLensConnector:
    def __init__(
        self,
        *,
        assets=None,
        asset=None,
        unavailable: bool = False,
    ) -> None:
        self.assets = assets or []

        self.asset = (
            asset
            or {
                "id": 1,
                "symbol": "TEST",
                "name": "Test Asset",
                "asset_type": "Equity",
                "is_active": True,
            }
        )

        self.unavailable = unavailable
        self.list_calls = 0
        self.get_calls = 0
        self.price_calls = 0
        self.analytics_calls = 0
        self.signal_calls = 0

        self.include_inactive = None
        self.requested_asset_id = None
        self.start_date = None
        self.end_date = None

    def fail_if_unavailable(
        self,
    ) -> None:
        if self.unavailable:
            raise ConnectorUnavailableError(
                "Market Lens unavailable."
            )

    def list_assets(
        self,
        *,
        include_inactive: bool = False,
        limit: int = 100,
    ):
        self.fail_if_unavailable()
        self.list_calls += 1

        self.include_inactive = (
            include_inactive
        )

        return self.assets[:limit]

    def get_asset(
        self,
        asset_id: int,
    ):
        self.fail_if_unavailable()
        self.get_calls += 1
        self.requested_asset_id = asset_id

        return self.asset

    def list_prices(
        self,
        asset_id: int,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 250,
    ):
        self.fail_if_unavailable()
        self.price_calls += 1
        self.requested_asset_id = asset_id
        self.start_date = start_date
        self.end_date = end_date

        return [
            {
                "id": 10,
                "asset_id": asset_id,
                "trading_date": "2026-11-10",
                "open_price": "100.00",
                "high_price": "105.00",
                "low_price": "99.00",
                "close_price": "104.00",
                "volume": 100000,
                "source": "Test source",
            }
        ][:limit]

    def get_analytics(
        self,
        asset_id: int,
    ):
        self.fail_if_unavailable()
        self.analytics_calls += 1
        self.requested_asset_id = asset_id

        return {
            "asset_id": asset_id,
            "symbol": "TEST",
            "price_count": 10,
            "latest_close": "104.00",
            "period_high": "110.00",
            "period_low": "90.00",
            "trend": "Recorded test trend",
        }

    def list_signals(
        self,
        asset_id: int,
        *,
        include_inactive: bool = True,
        limit: int = 100,
    ):
        self.fail_if_unavailable()
        self.signal_calls += 1
        self.requested_asset_id = asset_id

        return [
            {
                "id": 20,
                "asset_id": asset_id,
                "signal_type": "Watch",
                "target_price": "105.00",
                "trigger_direction": "Above",
                "rationale": (
                    "Recorded test threshold."
                ),
                "is_active": True,
                "is_triggered": False,
            }
        ][:limit]


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


def test_selected_turn_lists_market_assets(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubMarketLensConnector(
        assets=[
            {
                "id": 7,
                "symbol": "CIN",
                "name": "CIN Test Equity",
                "asset_type": "Equity",
                "exchange": "TEST",
                "currency": "USD",
                "is_active": True,
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
                "Summarize the available "
                "Market Lens assets."
            ),
            runtime=runtime,
            use_market_lens=True,
            market_lens_connector=connector,
        )

        assert connector.list_calls == 1
        assert connector.get_calls == 0

        assert (
            connector.include_inactive
            is False
        )

        assert runtime.request is not None

        system_message = (
            runtime.request.messages[0]
        )

        assert (
            "CIN Test Equity"
            in system_message.content
        )

        assert (
            "<market_lens_records>"
            in system_message.content
        )

        assert (
            result.market_retrieval_result
            is not None
        )

        assert (
            result.market_retrieval_result
            .asset_count
            == 1
        )

    engine.dispose()


def test_selected_turn_retrieves_market_detail(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubMarketLensConnector(
        asset={
            "id": 12,
            "symbol": "TEST",
            "name": "Detailed Market Asset",
            "asset_type": "Equity",
            "exchange": "TEST",
            "currency": "USD",
            "is_active": True,
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
                "Analyze Market Lens asset 12."
            ),
            runtime=runtime,
            use_market_lens=True,
            market_lens_asset_id=12,
            market_lens_start_date=(
                "2026-11-01"
            ),
            market_lens_end_date=(
                "2026-11-10"
            ),
            market_lens_connector=connector,
        )

        assert connector.list_calls == 0
        assert connector.get_calls == 1
        assert connector.price_calls == 1
        assert connector.analytics_calls == 1
        assert connector.signal_calls == 1

        assert (
            connector.requested_asset_id
            == 12
        )

        assert (
            connector.start_date
            == "2026-11-01"
        )

        assert (
            connector.end_date
            == "2026-11-10"
        )

        assert runtime.request is not None

        system_message = (
            runtime.request.messages[0]
        )

        assert (
            "Detailed Market Asset"
            in system_message.content
        )

        assert (
            "Recorded test trend"
            in system_message.content
        )

        assert (
            "Recorded test threshold."
            in system_message.content
        )

        assert (
            result.market_retrieval_result
            is not None
        )

        assert (
            result.market_retrieval_result
            .price_count
            == 1
        )

        assert (
            result.market_retrieval_result
            .signal_count
            == 1
        )

        assert (
            result.market_retrieval_result
            .analytics_count
            == 1
        )

    engine.dispose()


def test_related_market_data_can_be_disabled(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubMarketLensConnector()
    runtime = RecordingRuntime()

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        result = run_conversation_turn(
            database,
            conversation=conversation,
            user_content=(
                "Retrieve only the market asset."
            ),
            runtime=runtime,
            use_market_lens=True,
            market_lens_asset_id=3,
            market_lens_include_prices=False,
            market_lens_include_analytics=False,
            market_lens_include_signals=False,
            market_lens_connector=connector,
        )

        assert connector.get_calls == 1
        assert connector.price_calls == 0
        assert connector.analytics_calls == 0
        assert connector.signal_calls == 0

        assert (
            result.market_retrieval_result
            is not None
        )

        assert (
            result.market_retrieval_result
            .price_count
            == 0
        )

        assert (
            result.market_retrieval_result
            .signal_count
            == 0
        )

        assert (
            result.market_retrieval_result
            .analytics_count
            == 0
        )

    engine.dispose()


def test_market_retrieval_is_disabled_by_default(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubMarketLensConnector(
        assets=[
            {
                "id": 1,
                "name": (
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
            market_lens_connector=connector,
        )

        assert connector.list_calls == 0
        assert connector.get_calls == 0

        assert (
            result.market_retrieval_result
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


def test_offline_market_lens_does_not_break_turn(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    connector = StubMarketLensConnector(
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
                "Review unavailable market data."
            ),
            runtime=runtime,
            use_market_lens=True,
            market_lens_connector=connector,
        )

        assert (
            result.assistant_message
            is not None
        )

        assert (
            result.market_retrieval_result
            is not None
        )

        assert (
            result.market_retrieval_result
            .available
            is False
        )

        assert runtime.request is not None

        assert (
            "<market_lens_records>"
            not in (
                runtime.request.messages[0]
                .content
            )
        )

    engine.dispose()


def test_market_content_is_not_saved_as_message(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    private_record = (
        "Sensitive private market content."
    )

    connector = StubMarketLensConnector(
        assets=[
            {
                "id": 21,
                "symbol": "PRIVATE",
                "name": "Private market asset",
                "notes": private_record,
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
                "Review available market assets."
            ),
            runtime=RecordingRuntime(),
            use_market_lens=True,
            market_lens_connector=connector,
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


def test_market_content_is_not_in_audit_log(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    private_record = (
        "Do not store this market record "
        "in the audit log."
    )

    connector = StubMarketLensConnector(
        assets=[
            {
                "id": 31,
                "symbol": "AUDIT",
                "name": "Audit privacy asset",
                "notes": private_record,
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
                "Retrieve the audit privacy asset."
            ),
            runtime=RecordingRuntime(),
            use_market_lens=True,
            market_lens_connector=connector,
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

        detail = response_event.detail or ""

        assert (
            "market_lens_assets=1"
            in detail
        )

        assert (
            "market_lens_prices=0"
            in detail
        )

        assert (
            "market_lens_signals=0"
            in detail
        )

        assert (
            "market_lens_analytics=0"
            in detail
        )

    engine.dispose()