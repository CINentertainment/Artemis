from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.connectors.base import (
    Connector,
    ConnectorHealth,
)
from app.connectors.market_lens import (
    MarketLensConnector,
)
from app.connectors.registry import (
    ConnectorConfigurationError,
    ConnectorNotFoundError,
    ConnectorRegistry,
    DuplicateConnectorError,
    build_connector_registry,
)
from app.connectors.risk_tracker import (
    RiskTrackerConnector,
)


class StubConnector(Connector):
    def __init__(
        self,
        *,
        key: str,
        available: bool = True,
    ) -> None:
        self._key = key
        self._available = available
        self.closed = False

    @property
    def connector_key(self) -> str:
        return self._key

    @property
    def connector_name(self) -> str:
        return f"Connector {self._key}"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (
            f"{self._key}.health",
            f"{self._key}.read",
        )

    def check_health(
        self,
    ) -> ConnectorHealth:
        return ConnectorHealth(
            connector_key=self._key,
            available=self._available,
            response_time_ms=1.0,
            status_code=(
                200
                if self._available
                else None
            ),
            error=(
                None
                if self._available
                else "Unavailable"
            ),
        )

    def read_json(
        self,
        path: str,
        *,
        params: (
            dict[
                str,
                str | int | float | bool,
            ]
            | None
        ) = None,
    ) -> dict[str, Any] | list[Any]:
        return {
            "path": path,
            "params": params,
        }

    def close(self) -> None:
        self.closed = True


def build_test_settings(
    tmp_path: Path,
    **overrides,
) -> Settings:
    setting_values = {
        "data_directory": tmp_path,
        "intel_notes_url": (
            "http://127.0.0.1:8001"
        ),
        "risk_tracker_url": (
            "http://127.0.0.1:8000"
        ),
        "risk_tracker_username": (
            "artemis_readonly"
        ),
        "risk_tracker_password": (
            "private-test-password"
        ),
        "risk_tracker_timeout_seconds": 15.0,
        "market_lens_url": (
            "http://127.0.0.1:8003"
        ),
        "market_lens_api_key": (
            "market-lens-test-api-key-"
            "with-at-least-32-characters"
        ),
        "market_lens_timeout_seconds": 12.0,
    }

    setting_values.update(overrides)

    return Settings(**setting_values)


def test_registry_registers_and_gets_connector():
    connector = StubConnector(
        key="intel-notes"
    )

    registry = ConnectorRegistry(
        [connector]
    )

    assert (
        registry.get("intel-notes")
        is connector
    )

    assert (
        registry.get("  INTEL-NOTES  ")
        is connector
    )

    assert registry.connector_keys() == [
        "intel-notes"
    ]


def test_duplicate_connector_is_rejected():
    registry = ConnectorRegistry(
        [
            StubConnector(
                key="market-lens"
            )
        ]
    )

    with pytest.raises(
        DuplicateConnectorError,
        match="already registered",
    ):
        registry.register(
            StubConnector(
                key="market-lens"
            )
        )


def test_unknown_connector_is_rejected():
    registry = ConnectorRegistry()

    with pytest.raises(
        ConnectorNotFoundError,
        match="not registered",
    ):
        registry.get("missing")


def test_capability_map_is_reported():
    registry = ConnectorRegistry(
        [
            StubConnector(
                key="intel-notes"
            ),
            StubConnector(
                key="risk-tracker"
            ),
        ]
    )

    assert registry.capability_map() == {
        "intel-notes": (
            "intel-notes.health",
            "intel-notes.read",
        ),
        "risk-tracker": (
            "risk-tracker.health",
            "risk-tracker.read",
        ),
    }


def test_registry_checks_all_connector_health():
    registry = ConnectorRegistry(
        [
            StubConnector(
                key="available",
                available=True,
            ),
            StubConnector(
                key="unavailable",
                available=False,
            ),
        ]
    )

    results = registry.check_health()

    assert len(results) == 2

    assert [
        result.available
        for result in results
    ] == [
        True,
        False,
    ]


def test_registry_closes_connectors():
    first = StubConnector(key="first")
    second = StubConnector(key="second")

    registry = ConnectorRegistry(
        [first, second]
    )

    registry.close()

    assert first.closed is True
    assert second.closed is True


def test_registry_context_manager_closes_connectors():
    connector = StubConnector(
        key="context"
    )

    with ConnectorRegistry(
        [connector]
    ) as registry:
        assert (
            registry.get("context")
            is connector
        )

    assert connector.closed is True


def test_build_default_connector_registry(
    tmp_path: Path,
):
    settings = build_test_settings(
        tmp_path
    )

    registry = build_connector_registry(
        settings
    )

    assert registry.connector_keys() == [
        "intel-notes",
        "risk-tracker",
        "market-lens",
    ]

    capability_map = (
        registry.capability_map()
    )

    assert "intel_notes.search" in (
        capability_map["intel-notes"]
    )

    assert (
        "risk_tracker.list_cases"
        in capability_map["risk-tracker"]
    )

    assert (
        "risk_tracker.get_case"
        in capability_map["risk-tracker"]
    )

    assert (
        "market_lens.list_assets"
        in capability_map["market-lens"]
    )

    assert (
        "market_lens.get_analytics"
        in capability_map["market-lens"]
    )

    risk_tracker = registry.get(
        "risk-tracker"
    )

    assert isinstance(
        risk_tracker,
        RiskTrackerConnector,
    )

    assert (
        risk_tracker.base_url
        == "http://127.0.0.1:8000"
    )

    assert (
        risk_tracker.username
        == "artemis_readonly"
    )

    assert (
        risk_tracker.timeout_seconds
        == 15.0
    )

    market_lens = registry.get(
        "market-lens"
    )

    assert isinstance(
        market_lens,
        MarketLensConnector,
    )

    assert (
        market_lens.base_url
        == "http://127.0.0.1:8003"
    )

    assert (
        market_lens.timeout_seconds
        == 12.0
    )

    registry.close()


def test_missing_risk_tracker_credentials_fail_closed(
    tmp_path: Path,
):
    settings = build_test_settings(
        tmp_path,
        risk_tracker_password=None,
    )

    with pytest.raises(
        ConnectorConfigurationError,
        match="Risk Tracker credentials",
    ):
        build_connector_registry(settings)


def test_empty_risk_tracker_password_fails_closed(
    tmp_path: Path,
):
    settings = build_test_settings(
        tmp_path,
        risk_tracker_password="",
    )

    with pytest.raises(
        ConnectorConfigurationError,
        match="Risk Tracker credentials",
    ):
        build_connector_registry(settings)


def test_missing_market_lens_api_key_fails_closed(
    tmp_path: Path,
):
    settings = build_test_settings(
        tmp_path,
        market_lens_api_key=None,
    )

    with pytest.raises(
        ConnectorConfigurationError,
        match="Market Lens API key",
    ):
        build_connector_registry(settings)


def test_empty_market_lens_api_key_fails_closed(
    tmp_path: Path,
):
    settings = build_test_settings(
        tmp_path,
        market_lens_api_key="",
    )

    with pytest.raises(
        ConnectorConfigurationError,
        match="Market Lens API key",
    ):
        build_connector_registry(settings)


def test_default_registry_rejects_remote_urls(
    tmp_path: Path,
):
    settings = build_test_settings(
        tmp_path,
        intel_notes_url=(
            "https://remote.example.com"
        ),
    )

    with pytest.raises(
        Exception,
        match="loopback",
    ):
        build_connector_registry(settings)