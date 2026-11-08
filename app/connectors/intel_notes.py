from typing import Any

import httpx

from app.connectors.base import (
    ConnectorResponseError,
)
from app.connectors.http import (
    LocalHttpConnector,
)


class IntelNotesConnector(
    LocalHttpConnector
):
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            connector_key="intel-notes",
            connector_name=(
                "Intel Notes Manager"
            ),
            base_url=base_url,
            capabilities=(
                "intel_notes.health",
                "intel_notes.list",
                "intel_notes.get",
                "intel_notes.search",
                "intel_notes.analytics",
            ),
            timeout_seconds=timeout_seconds,
            health_path="/health",
            client=client,
        )

    def list_notes(
        self,
        *,
        keyword: str | None = None,
        classification: str | None = None,
        discipline: str | None = None,
        reliability: str | None = None,
        credibility: str | None = None,
        location: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        raw_parameters = {
            "keyword": keyword,
            "classification": classification,
            "discipline": discipline,
            "reliability": reliability,
            "credibility": credibility,
            "location": location,
            "start_date": start_date,
            "end_date": end_date,
        }

        parameters = {
            key: value.strip()
            for key, value in raw_parameters.items()
            if (
                value is not None
                and value.strip()
            )
        }

        response = self.read_json(
            "/notes",
            params=parameters or None,
        )

        if not isinstance(response, list):
            raise ConnectorResponseError(
                "Intel Notes returned an invalid "
                "note collection."
            )

        if not all(
            isinstance(note, dict)
            for note in response
        ):
            raise ConnectorResponseError(
                "Intel Notes returned an invalid "
                "note record."
            )

        return response

    def get_note(
        self,
        note_id: int,
    ) -> dict[str, Any]:
        if note_id < 1:
            raise ValueError(
                "Intel Notes note ID must be "
                "greater than zero."
            )

        response = self.read_json(
            f"/notes/{note_id}"
        )

        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Intel Notes returned an invalid "
                "note record."
            )

        return response

    def get_analytics(
        self,
    ) -> dict[str, Any]:
        response = self.read_json(
            "/analytics"
        )

        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Intel Notes returned an invalid "
                "analytics response."
            )

        return response