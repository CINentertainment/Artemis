from ipaddress import ip_address
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.connectors.base import (
    Connector,
    ConnectorHealth,
    ConnectorResponseError,
    ConnectorSecurityError,
    ConnectorUnavailableError,
)


def is_loopback_host(
    hostname: str | None,
) -> bool:
    if hostname is None:
        return False

    normalized_hostname = (
        hostname.strip().lower()
    )

    if normalized_hostname == "localhost":
        return True

    try:
        return ip_address(
            normalized_hostname
        ).is_loopback
    except ValueError:
        return False


def validate_local_base_url(
    base_url: str,
) -> str:
    cleaned_url = base_url.strip()

    if not cleaned_url:
        raise ConnectorSecurityError(
            "Connector base URL cannot be empty."
        )

    parsed_url = urlsplit(cleaned_url)

    if parsed_url.scheme not in {
        "http",
        "https",
    }:
        raise ConnectorSecurityError(
            "Connector base URL must use HTTP "
            "or HTTPS."
        )

    if not is_loopback_host(
        parsed_url.hostname
    ):
        raise ConnectorSecurityError(
            "Connectors may only access local "
            "loopback services."
        )

    if (
        parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise ConnectorSecurityError(
            "Connector URLs cannot contain "
            "credentials."
        )

    if (
        parsed_url.query
        or parsed_url.fragment
    ):
        raise ConnectorSecurityError(
            "Connector base URLs cannot contain "
            "queries or fragments."
        )

    return cleaned_url.rstrip("/")


def validate_relative_path(
    path: str,
) -> str:
    cleaned_path = path.strip()

    if not cleaned_path:
        raise ConnectorSecurityError(
            "Connector path cannot be empty."
        )

    parsed_path = urlsplit(cleaned_path)

    if (
        parsed_path.scheme
        or parsed_path.netloc
        or cleaned_path.startswith("//")
    ):
        raise ConnectorSecurityError(
            "Connector requests must use a "
            "relative local path."
        )

    if not cleaned_path.startswith("/"):
        raise ConnectorSecurityError(
            "Connector paths must begin with '/'."
        )

    path_segments = [
        segment
        for segment in parsed_path.path.split("/")
        if segment
    ]

    if ".." in path_segments:
        raise ConnectorSecurityError(
            "Connector paths cannot traverse "
            "directories."
        )

    return cleaned_path


class LocalHttpConnector(Connector):
    def __init__(
        self,
        *,
        connector_key: str,
        connector_name: str,
        base_url: str,
        capabilities: tuple[str, ...],
        timeout_seconds: float = 10.0,
        health_path: str = "/health",
        client: httpx.Client | None = None,
    ) -> None:
        cleaned_key = connector_key.strip()
        cleaned_name = connector_name.strip()

        if not cleaned_key:
            raise ValueError(
                "Connector key cannot be empty."
            )

        if not cleaned_name:
            raise ValueError(
                "Connector name cannot be empty."
            )

        if timeout_seconds <= 0:
            raise ValueError(
                "Connector timeout must be "
                "greater than zero."
            )

        self._connector_key = cleaned_key
        self._connector_name = cleaned_name

        self.base_url = (
            validate_local_base_url(base_url)
        )

        self._capabilities = tuple(
            capability.strip()
            for capability in capabilities
            if capability.strip()
        )

        self.timeout_seconds = (
            timeout_seconds
        )

        self.health_path = (
            validate_relative_path(
                health_path
            )
        )

        self._client = client
        self._owns_client = client is None

    @property
    def connector_key(self) -> str:
        return self._connector_key

    @property
    def connector_name(self) -> str:
        return self._connector_name

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self._capabilities

    def build_url(
        self,
        path: str,
    ) -> str:
        safe_path = validate_relative_path(
            path
        )

        return f"{self.base_url}{safe_path}"

    def get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )

        return self._client

    def check_health(
        self,
    ) -> ConnectorHealth:
        started_at = perf_counter()

        try:
            response = self.get_client().get(
                self.build_url(
                    self.health_path
                ),
                timeout=self.timeout_seconds,
            )

            response.raise_for_status()
        except (
            httpx.RequestError,
            httpx.HTTPStatusError,
        ):
            response_time_ms = (
                perf_counter() - started_at
            ) * 1000

            return ConnectorHealth(
                connector_key=(
                    self.connector_key
                ),
                available=False,
                response_time_ms=(
                    response_time_ms
                ),
                error=(
                    "Local connector service is "
                    "unavailable."
                ),
            )

        response_time_ms = (
            perf_counter() - started_at
        ) * 1000

        return ConnectorHealth(
            connector_key=self.connector_key,
            available=True,
            response_time_ms=response_time_ms,
            status_code=response.status_code,
        )

    def read_json(
        self,
        path: str,
        *,
        params: (
            dict[str, str | int | float | bool]
            | None
        ) = None,
    ) -> dict[str, Any] | list[Any]:
        request_url = self.build_url(path)

        try:
            response = self.get_client().get(
                request_url,
                params=params,
                timeout=self.timeout_seconds,
            )

            response.raise_for_status()
        except httpx.RequestError as error:
            raise ConnectorUnavailableError(
                "Local connector service is "
                "unavailable."
            ) from error
        except httpx.HTTPStatusError as error:
            raise ConnectorResponseError(
                (
                    "Connector returned HTTP "
                    f"status "
                    f"{error.response.status_code}."
                )
            ) from error

        try:
            response_data = response.json()
        except ValueError as error:
            raise ConnectorResponseError(
                "Connector returned invalid JSON."
            ) from error

        if not isinstance(
            response_data,
            (dict, list),
        ):
            raise ConnectorResponseError(
                "Connector returned an unsupported "
                "JSON value."
            )

        return response_data

    def close(self) -> None:
        if (
            self._client is not None
            and self._owns_client
        ):
            self._client.close()
            self._client = None

    def __enter__(
        self,
    ) -> "LocalHttpConnector":
        return self

    def __exit__(
        self,
        exception_type,
        exception_value,
        traceback,
    ) -> None:
        self.close()