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
    get_market_lens_connector,
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
            model="market-api-test",
            content=(
                "Artemis analyzed the available "
                "Market Lens context."
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


class StubMarketLensConnector:
    def __init__(self) -> None:
        self.list_calls = 0
        self.get_calls = 0
        self.price_calls = 0
        self.analytics_calls = 0
        self.signal_calls = 0

        self.include_inactive = None
        self.requested_asset_id = None
        self.start_date = None
        self.end_date = None

    def list_assets(
        self,
        *,
        include_inactive: bool = False,
        limit: int = 100,
    ):
        self.list_calls += 1

        self.include_inactive = (
            include_inactive
        )

        return [
            {
                "id": 41,
                "symbol": "CIN",
                "name": (
                    "API Market Lens test asset"
                ),
                "asset_type": "Equity",
                "exchange": "TEST",
                "currency": "USD",
                "notes": (
                    "Verified Market Lens API "
                    "context."
                ),
                "is_active": True,
            }
        ][:limit]

    def get_asset(
        self,
        asset_id: int,
    ):
        self.get_calls += 1
        self.requested_asset_id = asset_id

        return {
            "id": asset_id,
            "symbol": "DETAIL",
            "name": "Detailed API market asset",
            "asset_type": "Equity",
            "exchange": "TEST",
            "currency": "USD",
            "is_active": True,
        }

    def list_prices(
        self,
        asset_id: int,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 250,
    ):
        self.price_calls += 1
        self.requested_asset_id = asset_id
        self.start_date = start_date
        self.end_date = end_date

        return [
            {
                "id": 201,
                "asset_id": asset_id,
                "trading_date": "2026-11-10",
                "open_price": "100.00",
                "high_price": "105.00",
                "low_price": "99.00",
                "close_price": "104.00",
                "volume": 100000,
                "source": "API test provider",
            }
        ][:limit]

    def get_analytics(
        self,
        asset_id: int,
    ):
        self.analytics_calls += 1
        self.requested_asset_id = asset_id

        return {
            "asset_id": asset_id,
            "symbol": "DETAIL",
            "price_count": 10,
            "latest_close": "104.00",
            "period_high": "110.00",
            "period_low": "90.00",
            "average_close": "100.00",
            "trend": "Recorded API test trend",
        }

    def list_signals(
        self,
        asset_id: int,
        *,
        include_inactive: bool = True,
        limit: int = 100,
    ):
        self.signal_calls += 1
        self.requested_asset_id = asset_id

        return [
            {
                "id": 205,
                "asset_id": asset_id,
                "signal_type": "Watch",
                "target_price": "105.00",
                "trigger_direction": "Above",
                "rationale": (
                    "API test configured signal."
                ),
                "is_active": True,
                "is_triggered": False,
            }
        ][:limit]


test_runtime = RecordingRuntime()

test_market_lens_connector = (
    StubMarketLensConnector()
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
    return None


def override_get_market_lens_connector():
    return test_market_lens_connector


@pytest.fixture(autouse=True)
def isolated_market_reasoning_database():
    test_runtime.requests.clear()

    test_market_lens_connector.list_calls = 0
    test_market_lens_connector.get_calls = 0
    test_market_lens_connector.price_calls = 0
    test_market_lens_connector.analytics_calls = 0
    test_market_lens_connector.signal_calls = 0

    test_market_lens_connector.include_inactive = (
        None
    )

    test_market_lens_connector.requested_asset_id = (
        None
    )

    test_market_lens_connector.start_date = None
    test_market_lens_connector.end_date = None

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

    app.dependency_overrides[
        get_market_lens_connector
    ] = override_get_market_lens_connector

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

    app.dependency_overrides.pop(
        get_market_lens_connector,
        None,
    )


def create_conversation() -> dict:
    response = client.post(
        "/conversations",
        json={
            "title": (
                "Market Lens reasoning API test"
            ),
        },
    )

    assert response.status_code == 201

    return response.json()


def test_market_retrieval_is_off_by_default():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Answer without Market Lens."
            ),
        },
    )

    assert response.status_code == 201

    assert (
        test_market_lens_connector.list_calls
        == 0
    )

    assert (
        test_market_lens_connector.get_calls
        == 0
    )

    assert len(test_runtime.requests) == 1

    system_message = (
        test_runtime.requests[0]
        .messages[0]
        .content
    )

    assert (
        "API Market Lens test asset"
        not in system_message
    )


def test_api_can_list_market_lens_assets():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Summarize the available "
                "Market Lens assets."
            ),
            "use_market_lens": True,
            "market_lens_include_inactive": True,
        },
    )

    assert response.status_code == 201

    assert (
        test_market_lens_connector.list_calls
        == 1
    )

    assert (
        test_market_lens_connector
        .include_inactive
        is True
    )

    assert (
        test_market_lens_connector.get_calls
        == 0
    )

    assert len(test_runtime.requests) == 1

    system_message = (
        test_runtime.requests[0]
        .messages[0]
        .content
    )

    assert (
        "API Market Lens test asset"
        in system_message
    )

    assert (
        "Verified Market Lens API context."
        in system_message
    )

    assert (
        "<market_lens_records>"
        in system_message
    )


def test_api_can_retrieve_detailed_market_asset():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Analyze Market Lens asset 12."
            ),
            "use_market_lens": True,
            "market_lens_asset_id": 12,
            "market_lens_include_prices": True,
            "market_lens_include_analytics": True,
            "market_lens_include_signals": True,
            "market_lens_start_date": (
                "2026-11-01"
            ),
            "market_lens_end_date": (
                "2026-11-10"
            ),
        },
    )

    assert response.status_code == 201

    assert (
        test_market_lens_connector.list_calls
        == 0
    )

    assert (
        test_market_lens_connector.get_calls
        == 1
    )

    assert (
        test_market_lens_connector
        .requested_asset_id
        == 12
    )

    assert (
        test_market_lens_connector.price_calls
        == 1
    )

    assert (
        test_market_lens_connector
        .analytics_calls
        == 1
    )

    assert (
        test_market_lens_connector.signal_calls
        == 1
    )

    assert (
        test_market_lens_connector.start_date
        == "2026-11-01"
    )

    assert (
        test_market_lens_connector.end_date
        == "2026-11-10"
    )

    assert len(test_runtime.requests) == 1

    system_message = (
        test_runtime.requests[0]
        .messages[0]
        .content
    )

    assert (
        "Detailed API market asset"
        in system_message
    )

    assert (
        "Recorded API test trend"
        in system_message
    )

    assert (
        "API test configured signal."
        in system_message
    )


def test_api_can_disable_related_market_data():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Retrieve only market asset 8."
            ),
            "use_market_lens": True,
            "market_lens_asset_id": 8,
            "market_lens_include_prices": False,
            "market_lens_include_analytics": False,
            "market_lens_include_signals": False,
        },
    )

    assert response.status_code == 201

    assert (
        test_market_lens_connector.get_calls
        == 1
    )

    assert (
        test_market_lens_connector.price_calls
        == 0
    )

    assert (
        test_market_lens_connector
        .analytics_calls
        == 0
    )

    assert (
        test_market_lens_connector.signal_calls
        == 0
    )


def test_invalid_market_asset_id_is_rejected():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Retrieve an invalid market asset."
            ),
            "use_market_lens": True,
            "market_lens_asset_id": 0,
        },
    )

    assert response.status_code == 422

    assert (
        test_market_lens_connector.list_calls
        == 0
    )

    assert (
        test_market_lens_connector.get_calls
        == 0
    )

    assert test_runtime.requests == []


def test_invalid_market_date_length_is_rejected():
    conversation = create_conversation()

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Retrieve market data using "
                "an invalid date."
            ),
            "use_market_lens": True,
            "market_lens_asset_id": 1,
            "market_lens_start_date": (
                "2026-11-010"
            ),
        },
    )

    assert response.status_code == 422

    assert (
        test_market_lens_connector.get_calls
        == 0
    )

    assert test_runtime.requests == []


def test_missing_market_lens_key_fails_safely():
    conversation = create_conversation()

    app.dependency_overrides[
        get_market_lens_connector
    ] = lambda: None

    response = client.post(
        (
            f"/conversations/"
            f"{conversation['id']}/respond"
        ),
        json={
            "content": (
                "Retrieve protected market data."
            ),
            "use_market_lens": True,
        },
    )

    assert response.status_code == 503

    assert response.json() == {
        "detail": (
            "Market Lens API key is not "
            "configured."
        )
    }

    assert test_runtime.requests == []