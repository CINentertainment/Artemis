from collections.abc import Iterable

from app.config import Settings
from app.connectors.base import (
    Connector,
    ConnectorHealth,
)
from app.connectors.http import (
    LocalHttpConnector,
)
from app.connectors.intel_notes import (
    IntelNotesConnector,
)


class ConnectorRegistryError(Exception):
    pass


class ConnectorNotFoundError(
    ConnectorRegistryError
):
    pass


class DuplicateConnectorError(
    ConnectorRegistryError
):
    pass


class ConnectorRegistry:
    def __init__(
        self,
        connectors: (
            Iterable[Connector] | None
        ) = None,
    ) -> None:
        self._connectors: dict[
            str,
            Connector,
        ] = {}

        if connectors is not None:
            for connector in connectors:
                self.register(connector)

    def register(
        self,
        connector: Connector,
    ) -> None:
        connector_key = (
            connector.connector_key
            .strip()
            .lower()
        )

        if not connector_key:
            raise ValueError(
                "Connector key cannot be empty."
            )

        if connector_key in self._connectors:
            raise DuplicateConnectorError(
                (
                    "Connector is already "
                    f"registered: {connector_key}"
                )
            )

        self._connectors[
            connector_key
        ] = connector

    def get(
        self,
        connector_key: str,
    ) -> Connector:
        normalized_key = (
            connector_key.strip().lower()
        )

        connector = self._connectors.get(
            normalized_key
        )

        if connector is None:
            raise ConnectorNotFoundError(
                (
                    "Connector is not registered: "
                    f"{normalized_key}"
                )
            )

        return connector

    def connector_keys(
        self,
    ) -> list[str]:
        return list(self._connectors.keys())

    def connectors(
        self,
    ) -> list[Connector]:
        return list(
            self._connectors.values()
        )

    def capability_map(
        self,
    ) -> dict[str, tuple[str, ...]]:
        return {
            connector.connector_key: (
                connector.capabilities
            )
            for connector in self.connectors()
        }

    def check_health(
        self,
    ) -> list[ConnectorHealth]:
        return [
            connector.check_health()
            for connector in self.connectors()
        ]

    def close(self) -> None:
        for connector in self.connectors():
            close_method = getattr(
                connector,
                "close",
                None,
            )

            if callable(close_method):
                close_method()

    def __enter__(
        self,
    ) -> "ConnectorRegistry":
        return self

    def __exit__(
        self,
        exception_type,
        exception_value,
        traceback,
    ) -> None:
        self.close()


def build_connector_registry(
    settings: Settings,
) -> ConnectorRegistry:
    return ConnectorRegistry(
        connectors=[
            IntelNotesConnector(
    base_url=settings.intel_notes_url,
),
            LocalHttpConnector(
                connector_key="risk-tracker",
                connector_name=(
                    "Protective Intelligence "
                    "Risk Tracker"
                ),
                base_url=(
                    settings.risk_tracker_url
                ),
                capabilities=(
                    "risk_tracker.health",
                    "risk_tracker.list",
                    "risk_tracker.get",
                    "risk_tracker.search",
                    "risk_tracker.status",
                ),
            ),
            LocalHttpConnector(
                connector_key="market-lens",
                connector_name="Market Lens",
                base_url=(
                    settings.market_lens_url
                ),
                capabilities=(
                    "market_lens.health",
                    "market_lens.list",
                    "market_lens.get",
                    "market_lens.search",
                    "market_lens.analyze",
                    "market_lens.analytics",
                ),
            ),
        ]
    )