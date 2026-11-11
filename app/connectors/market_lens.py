from time import perf_counter
from typing import Any

import httpx

from app.connectors.base import (
    ConnectorHealth,
    ConnectorResponseError,
    ConnectorUnavailableError,
)
from app.connectors.http import (
    LocalHttpConnector,
)


class MarketLensAuthenticationError(
    ConnectorResponseError
):
    pass


class MarketLensConnector(
    LocalHttpConnector
):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "Market Lens API key cannot "
                "be empty."
            )

        if len(api_key) < 32:
            raise ValueError(
                "Market Lens API key must contain "
                "at least 32 characters."
            )

        super().__init__(
            connector_key="market-lens",
            connector_name="Market Lens",
            base_url=base_url,
            capabilities=(
                "market_lens.health",
                "market_lens.list_assets",
                "market_lens.get_asset",
                "market_lens.list_prices",
                "market_lens.get_analytics",
                "market_lens.list_signals",
            ),
            timeout_seconds=timeout_seconds,
            health_path=(
                "/integration/artemis/health"
            ),
            client=client,
        )

        self._api_key = api_key

    def _request_headers(
        self,
    ) -> dict[str, str]:
        return {
            "X-Artemis-Key": self._api_key,
        }

    def _parse_json_response(
        self,
        response: httpx.Response,
    ) -> dict[str, Any] | list[Any]:
        try:
            response_data = response.json()
        except ValueError as error:
            raise ConnectorResponseError(
                "Market Lens returned invalid JSON."
            ) from error

        if not isinstance(
            response_data,
            (dict, list),
        ):
            raise ConnectorResponseError(
                "Market Lens returned an "
                "unsupported JSON value."
            )

        return response_data

    def _read_protected_json(
        self,
        path: str,
        *,
        params: (
            dict[str, str | int | float | bool]
            | None
        ) = None,
    ) -> dict[str, Any] | list[Any]:
        try:
            response = self.get_client().get(
                self.build_url(path),
                headers=self._request_headers(),
                params=params,
                timeout=self.timeout_seconds,
            )

            response.raise_for_status()
        except httpx.RequestError as error:
            raise ConnectorUnavailableError(
                "Market Lens is unavailable."
            ) from error
        except httpx.HTTPStatusError as error:
            if (
                error.response.status_code
                == 401
            ):
                raise (
                    MarketLensAuthenticationError(
                        "Market Lens authentication "
                        "failed."
                    )
                ) from error

            raise ConnectorResponseError(
                (
                    "Market Lens returned HTTP "
                    f"status "
                    f"{error.response.status_code}."
                )
            ) from error

        return self._parse_json_response(
            response
        )

    def check_health(
        self,
    ) -> ConnectorHealth:
        started_at = perf_counter()

        try:
            response = self.get_client().get(
                self.build_url(
                    self.health_path
                ),
                headers=self._request_headers(),
                timeout=self.timeout_seconds,
            )

            response.raise_for_status()

            response_data = (
                self._parse_json_response(
                    response
                )
            )

            if not isinstance(
                response_data,
                dict,
            ):
                raise ConnectorResponseError(
                    "Market Lens returned an invalid "
                    "health response."
                )

            if (
                response_data.get("status")
                != "healthy"
                or response_data.get(
                    "access"
                )
                != "read-only"
            ):
                raise ConnectorResponseError(
                    "Market Lens returned an "
                    "unexpected health response."
                )
        except (
            httpx.RequestError,
            httpx.HTTPStatusError,
            ConnectorResponseError,
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
                    "Market Lens integration is "
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

    def list_assets(
        self,
        *,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValueError(
                "Market Lens asset limit must "
                "be between 1 and 500."
            )

        response = self._read_protected_json(
            "/integration/artemis/assets",
            params={
                "include_inactive": (
                    include_inactive
                ),
                "limit": limit,
            },
        )

        if (
            not isinstance(response, list)
            or not all(
                isinstance(asset, dict)
                for asset in response
            )
        ):
            raise ConnectorResponseError(
                "Market Lens returned an invalid "
                "asset collection."
            )

        return response

    def get_asset(
        self,
        asset_id: int,
    ) -> dict[str, Any]:
        self._validate_asset_id(asset_id)

        response = self._read_protected_json(
            (
                "/integration/artemis/assets/"
                f"{asset_id}"
            )
        )

        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Market Lens returned an invalid "
                "asset record."
            )

        return response

    def list_prices(
        self,
        asset_id: int,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        self._validate_asset_id(asset_id)

        if limit < 1 or limit > 2000:
            raise ValueError(
                "Market Lens price limit must "
                "be between 1 and 2000."
            )

        parameters: dict[
            str,
            str | int | float | bool,
        ] = {
            "limit": limit,
        }

        if start_date is not None:
            cleaned_start_date = (
                start_date.strip()
            )

            if cleaned_start_date:
                parameters[
                    "start_date"
                ] = cleaned_start_date

        if end_date is not None:
            cleaned_end_date = end_date.strip()

            if cleaned_end_date:
                parameters[
                    "end_date"
                ] = cleaned_end_date

        response = self._read_protected_json(
            (
                "/integration/artemis/assets/"
                f"{asset_id}/prices"
            ),
            params=parameters,
        )

        if (
            not isinstance(response, list)
            or not all(
                isinstance(price, dict)
                for price in response
            )
        ):
            raise ConnectorResponseError(
                "Market Lens returned an invalid "
                "price collection."
            )

        return response

    def get_analytics(
        self,
        asset_id: int,
    ) -> dict[str, Any]:
        self._validate_asset_id(asset_id)

        response = self._read_protected_json(
            (
                "/integration/artemis/assets/"
                f"{asset_id}/analytics"
            )
        )

        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Market Lens returned an invalid "
                "analytics response."
            )

        return response

    def list_signals(
        self,
        asset_id: int,
        *,
        include_inactive: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._validate_asset_id(asset_id)

        if limit < 1 or limit > 500:
            raise ValueError(
                "Market Lens signal limit must "
                "be between 1 and 500."
            )

        response = self._read_protected_json(
            (
                "/integration/artemis/assets/"
                f"{asset_id}/signals"
            ),
            params={
                "include_inactive": (
                    include_inactive
                ),
                "limit": limit,
            },
        )

        if (
            not isinstance(response, list)
            or not all(
                isinstance(signal, dict)
                for signal in response
            )
        ):
            raise ConnectorResponseError(
                "Market Lens returned an invalid "
                "signal collection."
            )

        return response

    @staticmethod
    def _validate_asset_id(
        asset_id: int,
    ) -> None:
        if asset_id < 1:
            raise ValueError(
                "Market Lens asset ID must be "
                "greater than zero."
            )