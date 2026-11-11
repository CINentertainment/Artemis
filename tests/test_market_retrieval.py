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
from app.services.market_retrieval import (
    MAXIMUM_ASSETS,
    MAXIMUM_PRICES,
    MAXIMUM_SIGNALS,
    build_market_lens_context,
    retrieve_market_lens_asset,
    retrieve_market_lens_assets,
)


class StubMarketLensConnector:
    def __init__(
        self,
        *,
        assets=None,
        asset=None,
        prices=None,
        analytics=None,
        signals=None,
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

        self.prices = prices or []
        self.analytics = analytics or {}
        self.signals = signals or []
        self.unavailable = unavailable

        self.include_inactive = None
        self.asset_limit = None
        self.requested_asset_ids: list[
            int
        ] = []

        self.price_start_date = None
        self.price_end_date = None
        self.price_limit = None
        self.signal_include_inactive = None
        self.signal_limit = None

    def fail_if_unavailable(
        self,
    ) -> None:
        if self.unavailable:
            raise ConnectorUnavailableError(
                "Private connection information."
            )

    def list_assets(
        self,
        *,
        include_inactive: bool = False,
        limit: int = 100,
    ):
        self.fail_if_unavailable()

        self.include_inactive = (
            include_inactive
        )
        self.asset_limit = limit

        return self.assets

    def get_asset(
        self,
        asset_id: int,
    ):
        self.fail_if_unavailable()

        self.requested_asset_ids.append(
            asset_id
        )

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

        self.requested_asset_ids.append(
            asset_id
        )
        self.price_start_date = start_date
        self.price_end_date = end_date
        self.price_limit = limit

        return self.prices

    def get_analytics(
        self,
        asset_id: int,
    ):
        self.fail_if_unavailable()

        self.requested_asset_ids.append(
            asset_id
        )

        return self.analytics

    def list_signals(
        self,
        asset_id: int,
        *,
        include_inactive: bool = True,
        limit: int = 100,
    ):
        self.fail_if_unavailable()

        self.requested_asset_ids.append(
            asset_id
        )
        self.signal_include_inactive = (
            include_inactive
        )
        self.signal_limit = limit

        return self.signals


def build_market_retrieval_test_engine(
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


def test_lists_market_lens_assets(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector(
        assets=[
            {
                "id": 4,
                "symbol": "CIN",
                "name": "CIN Test Equity",
                "asset_type": "Equity",
                "exchange": "TEST",
                "currency": "USD",
                "is_active": True,
            }
        ]
    )

    with Session(engine) as database:
        result = retrieve_market_lens_assets(
            database,
            connector=connector,
        )

        assert (
            connector.include_inactive
            is False
        )

        assert result.available is True
        assert result.asset_count == 1
        assert result.price_count == 0
        assert result.signal_count == 0
        assert result.analytics_count == 0
        assert result.context is not None

        assert "CIN" in result.context

        assert (
            "<market_lens_records>"
            in result.context
        )

        assert (
            "untrusted reference data"
            in result.context
        )

    engine.dispose()


def test_asset_list_limit_is_enforced(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector(
        assets=[
            {
                "id": index,
                "symbol": f"ASSET{index}",
                "name": f"Asset {index}",
            }
            for index in range(
                1,
                MAXIMUM_ASSETS + 2,
            )
        ]
    )

    with Session(engine) as database:
        result = retrieve_market_lens_assets(
            database,
            connector=connector,
            maximum_assets=1000,
        )

        assert (
            connector.asset_limit
            == MAXIMUM_ASSETS
        )

        assert (
            result.asset_count
            == MAXIMUM_ASSETS
        )

        assert result.context is not None
        assert "ASSET100" in result.context
        assert "ASSET101" not in result.context

    engine.dispose()


def test_empty_asset_list_returns_no_context(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector()

    with Session(engine) as database:
        result = retrieve_market_lens_assets(
            database,
            connector=connector,
        )

        assert result.available is True
        assert result.asset_count == 0
        assert result.context is None
        assert result.error is None

    engine.dispose()


def test_retrieves_asset_and_related_market_data(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector(
        asset={
            "id": 3,
            "symbol": "TEST",
            "name": "Test Corporation",
            "asset_type": "Equity",
            "exchange": "TEST",
            "currency": "USD",
            "is_active": True,
        },
        prices=[
            {
                "id": 10,
                "asset_id": 3,
                "trading_date": "2026-11-10",
                "open_price": "100.00",
                "high_price": "105.00",
                "low_price": "99.00",
                "close_price": "104.00",
                "volume": 100000,
                "source": "Test provider",
            }
        ],
        analytics={
            "asset_id": 3,
            "symbol": "TEST",
            "price_count": 10,
            "latest_close": "104.00",
            "period_high": "110.00",
            "period_low": "90.00",
            "average_close": "100.00",
            "trend": "Upward",
        },
        signals=[
            {
                "id": 20,
                "asset_id": 3,
                "signal_type": "Watch",
                "target_price": "105.00",
                "trigger_direction": "Above",
                "rationale": (
                    "Monitor configured threshold."
                ),
                "is_active": True,
                "is_triggered": False,
            }
        ],
    )

    with Session(engine) as database:
        result = retrieve_market_lens_asset(
            database,
            connector=connector,
            asset_id=3,
            start_date="2026-11-01",
            end_date="2026-11-10",
        )

        assert result.available is True
        assert result.asset_count == 1
        assert result.price_count == 1
        assert result.signal_count == 1
        assert result.analytics_count == 1

        assert (
            connector.requested_asset_ids
            == [3, 3, 3, 3]
        )

        assert (
            connector.price_start_date
            == "2026-11-01"
        )

        assert (
            connector.price_end_date
            == "2026-11-10"
        )

        assert (
            connector.signal_include_inactive
            is True
        )

        assert result.context is not None
        assert "Test Corporation" in result.context
        assert "104.00" in result.context
        assert "Upward" in result.context

        assert (
            "Monitor configured threshold."
            in result.context
        )

    engine.dispose()


def test_related_market_data_can_be_excluded(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector(
        asset={
            "id": 6,
            "symbol": "ONLY",
            "name": "Asset Only",
        },
        prices=[
            {
                "id": 20,
                "asset_id": 6,
                "close_price": "999.00",
            }
        ],
        analytics={
            "asset_id": 6,
            "trend": "Excluded",
        },
        signals=[
            {
                "id": 21,
                "asset_id": 6,
                "rationale": (
                    "Should not be retrieved."
                ),
            }
        ],
    )

    with Session(engine) as database:
        result = retrieve_market_lens_asset(
            database,
            connector=connector,
            asset_id=6,
            include_prices=False,
            include_analytics=False,
            include_signals=False,
        )

        assert result.available is True
        assert result.asset_count == 1
        assert result.price_count == 0
        assert result.signal_count == 0
        assert result.analytics_count == 0

        assert (
            connector.requested_asset_ids
            == [6]
        )

        assert result.context is not None
        assert "Asset Only" in result.context
        assert "999.00" not in result.context
        assert "Excluded" not in result.context

        assert (
            "Should not be retrieved."
            not in result.context
        )

    engine.dispose()


def test_related_record_limits_are_enforced(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector(
        prices=[
            {
                "id": index,
                "asset_id": 1,
                "close_price": str(index),
            }
            for index in range(1, 10)
        ],
        signals=[
            {
                "id": index,
                "asset_id": 1,
                "signal_type": "Watch",
            }
            for index in range(1, 10)
        ],
    )

    with Session(engine) as database:
        retrieve_market_lens_asset(
            database,
            connector=connector,
            asset_id=1,
            maximum_prices=10000,
            maximum_signals=10000,
        )

        assert (
            connector.price_limit
            == MAXIMUM_PRICES
        )

        assert (
            connector.signal_limit
            == MAXIMUM_SIGNALS
        )

    engine.dispose()


def test_unknown_fields_are_excluded():
    context = build_market_lens_context(
        [
            {
                "asset": {
                    "id": 1,
                    "symbol": "SAFE",
                    "name": "Allowed asset",
                    "api_key": (
                        "must-not-enter-context"
                    ),
                    "password": (
                        "also-must-not-enter-context"
                    ),
                },
                "prices": [
                    {
                        "id": 2,
                        "asset_id": 1,
                        "close_price": "101.00",
                        "private_token": (
                            "hidden-price-value"
                        ),
                    }
                ],
                "analytics": {
                    "asset_id": 1,
                    "trend": "Allowed trend",
                    "internal_secret": (
                        "hidden-analytics-value"
                    ),
                },
                "signals": [
                    {
                        "id": 3,
                        "asset_id": 1,
                        "rationale": (
                            "Allowed rationale"
                        ),
                        "credential": (
                            "hidden-signal-value"
                        ),
                    }
                ],
            }
        ]
    )

    assert context is not None
    assert "Allowed asset" in context
    assert "101.00" in context
    assert "Allowed trend" in context
    assert "Allowed rationale" in context

    assert (
        "must-not-enter-context"
        not in context
    )

    assert (
        "also-must-not-enter-context"
        not in context
    )

    assert "hidden-price-value" not in context
    assert "hidden-analytics-value" not in context
    assert "hidden-signal-value" not in context
    assert "api_key" not in context
    assert "password" not in context
    assert "private_token" not in context
    assert "internal_secret" not in context
    assert "credential" not in context


def test_retrieved_fields_are_limited():
    long_description = "A" * 5000

    context = build_market_lens_context(
        [
            {
                "asset": {
                    "id": 1,
                    "symbol": "TEST",
                    "description": (
                        long_description
                    ),
                }
            }
        ],
        maximum_field_length=200,
    )

    assert context is not None
    assert "A" * 200 in context
    assert "A" * 201 not in context


def test_invalid_asset_id_is_rejected(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector()

    with Session(engine) as database:
        with pytest.raises(
            ValueError,
            match="greater than zero",
        ):
            retrieve_market_lens_asset(
                database,
                connector=connector,
                asset_id=0,
            )

    engine.dispose()


def test_unavailable_asset_list_fails_safely(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector(
        unavailable=True
    )

    with Session(engine) as database:
        result = retrieve_market_lens_assets(
            database,
            connector=connector,
        )

        assert result.available is False
        assert result.asset_count == 0
        assert result.context is None

        assert result.error == (
            "Market Lens is currently "
            "unavailable."
        )

    engine.dispose()


def test_unavailable_asset_detail_fails_safely(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    connector = StubMarketLensConnector(
        unavailable=True
    )

    with Session(engine) as database:
        result = retrieve_market_lens_asset(
            database,
            connector=connector,
            asset_id=8,
        )

        assert result.available is False
        assert result.asset_count == 0
        assert result.context is None

        assert result.error == (
            "Market Lens is currently "
            "unavailable."
        )

    engine.dispose()


def test_market_retrieval_audit_excludes_content(
    tmp_path: Path,
):
    engine = (
        build_market_retrieval_test_engine(
            tmp_path
        )
    )

    private_notes = (
        "Sensitive private market notes"
    )

    private_rationale = (
        "Sensitive private signal rationale"
    )

    connector = StubMarketLensConnector(
        asset={
            "id": 9,
            "symbol": "PRIVATE",
            "name": "Private asset",
            "notes": private_notes,
        },
        signals=[
            {
                "id": 30,
                "asset_id": 9,
                "signal_type": "Watch",
                "rationale": private_rationale,
            }
        ],
    )

    with Session(engine) as database:
        retrieve_market_lens_asset(
            database,
            connector=connector,
            asset_id=9,
        )

        events = database.scalars(
            select(models.AuditEvent)
        ).all()

        assert len(events) == 2

        for event in events:
            assert private_notes not in (
                event.detail or ""
            )

            assert private_rationale not in (
                event.detail or ""
            )

    engine.dispose()
def test_context_reports_exact_retrieval_counts():
    prices = [
        {
            "id": index,
            "asset_id": 1,
            "trading_date": (
                f"2026-10-{index:02d}"
            ),
            "close_price": str(index),
        }
        for index in range(1, 24)
    ]

    context = build_market_lens_context(
        [
            {
                "asset": {
                    "id": 1,
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "exchange": "NASDAQ",
                    "currency": "USD",
                },
                "prices": prices,
                "analytics": {
                    "asset_id": 1,
                    "symbol": "AAPL",
                    "price_bar_count": 23,
                    "latest_close": 23.0,
                    "daily_change_percent": 1.5,
                    "total_return_percent": 10.0,
                    "moving_average_20": 12.5,
                    "annualized_volatility_percent": (
                        14.25
                    ),
                    "trend": "Recorded trend",
                },
                "signals": [],
            }
        ]
    )

    assert context is not None

    assert (
        '"retrieval_metadata":'
        in context
    )

    assert (
        '"asset_record_count":1'
        in context
    )

    assert (
        '"price_record_count":23'
        in context
    )

    assert (
        '"analytics_record_count":1'
        in context
    )

    assert (
        '"signal_record_count":0'
        in context
    )

    assert (
        '"price_record_count":30'
        not in context
    )

    assert (
        '"authoritative_summaries":'
        in context
    )

    assert (
        '"exchange":"NASDAQ"'
        in context
    )

    assert (
        '"earliest_record_date":'
        '"2026-10-01"'
        in context
    )

    assert (
        '"latest_record_date":'
        '"2026-10-23"'
        in context
    )

    assert (
        '"latest_close_price":"23"'
        in context
    )

    assert (
        '"lowest_close_price":"1"'
        in context
    )

    assert (
        '"highest_close_price":"23"'
        in context
    )

    assert (
        '"price_bar_count":23'
        in context
    )

    assert (
        '"daily_change_percent":1.5'
        in context
    )

    assert (
        '"total_return_percent":10.0'
        in context
    )

    assert (
        '"moving_average_20":12.5'
        in context
    )

    assert (
        '"annualized_volatility_percent":'
        "14.25"
        in context
    )

    assert (
        "exact number of records"
        in context
    )

    assert (
        "Do not reproduce every price row"
        in context
    )

    assert (
        "Do not describe the records as a number of "
        "trading days"
        in context
    )