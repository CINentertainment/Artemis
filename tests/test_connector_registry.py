from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.connectors.base import (
    Connector,
    ConnectorHealth,
)
from app.connectors.registry import (
    ConnectorNotFoundError,
    ConnectorRegistry,
    DuplicateConnectorError,
    build_connector_registry,
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
    settings = Settings(
        data_directory=tmp_path,
        intel_notes_url=(
            "http://127.0.0.1:8001"
        ),
        risk_tracker_url=(
            "http://127.0.0.1:8002"
        ),
        market_lens_url=(
            "http://127.0.0.1:8003"
        ),
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

    assert "risk_tracker.status" in (
        capability_map["risk-tracker"]
    )

    assert "market_lens.analyze" in (
        capability_map["market-lens"]
    )

    registry.close()


def test_default_registry_rejects_remote_urls(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
        intel_notes_url=(
            "https://remote.example.com"
        ),
    )

    with pytest.raises(
        Exception,
        match="loopback",
    ):
        build_connector_registry(settings)