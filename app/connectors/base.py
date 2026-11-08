from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ConnectorError(Exception):
    pass


class ConnectorSecurityError(
    ConnectorError
):
    pass


class ConnectorUnavailableError(
    ConnectorError
):
    pass


class ConnectorResponseError(
    ConnectorError
):
    pass


@dataclass(frozen=True)
class ConnectorHealth:
    connector_key: str
    available: bool
    response_time_ms: float
    status_code: int | None = None
    error: str | None = None


class Connector(ABC):
    @property
    @abstractmethod
    def connector_key(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def connector_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def capabilities(self) -> tuple[str, ...]:
        raise NotImplementedError

    @abstractmethod
    def check_health(
        self,
    ) -> ConnectorHealth:
        raise NotImplementedError

    @abstractmethod
    def read_json(
        self,
        path: str,
        *,
        params: (
            dict[str, str | int | float | bool]
            | None
        ) = None,
    ) -> dict[str, Any] | list[Any]:
        raise NotImplementedError