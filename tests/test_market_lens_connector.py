from typing import Callable

import httpx
import pytest

from app.connectors.base import (
    ConnectorResponseError,
)
from app.connectors.market_lens import (
    MarketLensAuthenticationError,
    MarketLensConnector,
)


TEST_API_KEY = (
    "market-lens-connector-test-key-"
    "0123456789abcdef"
)


def build_test_connector(
    handler: Callable[
        [httpx.Request],
        httpx.Response,
    ],
) -> MarketLensConnector:
    transport = httpx.MockTransport(
        handler
    )

    client = httpx.Client(
        transport=transport
    )

    return MarketLensConnector(
        base_url="http://127.0.0.1:8003",
        api_key=TEST_API_KEY,
        timeout_seconds=5.0,
        client=client,
    )


def test_connector_has_only_read_capabilities():
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            json={},
        )
    )

    assert connector.capabilities == (
        "market_lens.health",
        "market_lens.list_assets",
        "market_lens.get_asset",
        "market_lens.list_prices",
        "market_lens.get_analytics",
        "market_lens.list_signals",
    )

    assert all(
        operation not in capability
        for capability
        in connector.capabilities
        for operation in (
            "create",
            "update",
            "delete",
            "sync",
            "write",
            "execute",
        )
    )


def test_empty_api_key_is_rejected():
    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        MarketLensConnector(
            base_url=(
                "http://127.0.0.1:8003"
            ),
            api_key="",
        )


def test_short_api_key_is_rejected():
    with pytest.raises(
        ValueError,
        match="at least 32",
    ):
        MarketLensConnector(
            base_url=(
                "http://127.0.0.1:8003"
            ),
            api_key="too-short",
        )


def test_health_uses_protected_endpoint_and_key():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "GET"

        assert (
            request.url.path
            == "/integration/artemis/health"
        )

        assert (
            request.headers[
                "X-Artemis-Key"
            ]
            == TEST_API_KEY
        )

        return httpx.Response(
            200,
            json={
                "status": "healthy",
                "application": "Market Lens",
                "integration": "artemis",
                "access": "read-only",
            },
        )

    connector = build_test_connector(
        handler
    )

    result = connector.check_health()

    assert result.available is True
    assert result.status_code == 200


def test_invalid_health_response_is_unavailable():
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            json={
                "status": "healthy",
                "access": "write-enabled",
            },
        )
    )

    result = connector.check_health()

    assert result.available is False


def test_authentication_failure_is_rejected():
    connector = build_test_connector(
        lambda request: httpx.Response(
            401,
            json={
                "detail": "Invalid key",
            },
        )
    )

    with pytest.raises(
        MarketLensAuthenticationError,
        match="authentication failed",
    ):
        connector.list_assets()


def test_list_assets_uses_only_get():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "GET"

        assert (
            request.url.path
            == "/integration/artemis/assets"
        )

        assert (
            request.headers[
                "X-Artemis-Key"
            ]
            == TEST_API_KEY
        )

        assert (
            request.url.params["limit"]
            == "25"
        )

        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "symbol": "AAPL",
                }
            ],
        )

    connector = build_test_connector(
        handler
    )

    result = connector.list_assets(
        limit=25
    )

    assert result == [
        {
            "id": 1,
            "symbol": "AAPL",
        }
    ]


def test_get_asset_uses_fixed_path():
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            json={
                "id": 7,
                "symbol": "MSFT",
            },
        )
    )

    result = connector.get_asset(7)

    assert result["id"] == 7
    assert result["symbol"] == "MSFT"


def test_list_prices_passes_filters():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "GET"

        assert (
            request.url.path
            == (
                "/integration/artemis/assets/"
                "3/prices"
            )
        )

        assert (
            request.url.params[
                "start_date"
            ]
            == "2026-01-01"
        )

        assert (
            request.url.params["end_date"]
            == "2026-11-11"
        )

        assert (
            request.url.params["limit"]
            == "50"
        )

        return httpx.Response(
            200,
            json=[
                {
                    "asset_id": 3,
                    "close_price": "100.00",
                }
            ],
        )

    connector = build_test_connector(
        handler
    )

    prices = connector.list_prices(
        3,
        start_date="2026-01-01",
        end_date="2026-11-11",
        limit=50,
    )

    assert len(prices) == 1


def test_get_analytics_reads_fixed_path():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "GET"

        assert (
            request.url.path
            == (
                "/integration/artemis/assets/"
                "2/analytics"
            )
        )

        return httpx.Response(
            200,
            json={
                "asset_id": 2,
                "trend": "Upward",
            },
        )

    connector = build_test_connector(
        handler
    )

    result = connector.get_analytics(2)

    assert result["trend"] == "Upward"


def test_list_signals_reads_without_write():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        assert request.method == "GET"

        assert (
            request.url.path
            == (
                "/integration/artemis/assets/"
                "4/signals"
            )
        )

        return httpx.Response(
            200,
            json=[
                {
                    "id": 9,
                    "asset_id": 4,
                    "is_triggered": False,
                }
            ],
        )

    connector = build_test_connector(
        handler
    )

    signals = connector.list_signals(4)

    assert len(signals) == 1
    assert signals[0]["is_triggered"] is False


@pytest.mark.parametrize(
    "asset_id",
    [
        0,
        -1,
    ],
)
def test_invalid_asset_ids_are_rejected(
    asset_id: int,
):
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            json={},
        )
    )

    with pytest.raises(
        ValueError,
        match="greater than zero",
    ):
        connector.get_asset(asset_id)


def test_invalid_asset_collection_is_rejected():
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            json={
                "id": 1,
            },
        )
    )

    with pytest.raises(
        ConnectorResponseError,
        match="asset collection",
    ):
        connector.list_assets()