from typing import Any
from uuid import UUID

import httpx

from app.connectors.base import (
    ConnectorResponseError,
    ConnectorUnavailableError,
)
from app.connectors.http import LocalHttpConnector


class IntelNotesConnector(LocalHttpConnector):
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            connector_key="intel-notes",
            connector_name="Intel Notes Manager",
            base_url=base_url,
            capabilities=(
                "intel_notes.health",
                "intel_notes.list",
                "intel_notes.get",
                "intel_notes.search",
                "intel_notes.analytics",
                "intel_notes.import_artemis_note",
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
            if value is not None and value.strip()
        }

        response = self.read_json("/notes", params=parameters or None)

        if not isinstance(response, list):
            raise ConnectorResponseError(
                "Intel Notes returned an invalid note collection."
            )

        if not all(isinstance(note, dict) for note in response):
            raise ConnectorResponseError(
                "Intel Notes returned an invalid note record."
            )

        return response

    def get_note(self, note_id: int) -> dict[str, Any]:
        if note_id < 1:
            raise ValueError("Intel Notes note ID must be greater than zero.")

        response = self.read_json(f"/notes/{note_id}")

        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Intel Notes returned an invalid note record."
            )

        return response

    def get_analytics(self) -> dict[str, Any]:
        response = self.read_json("/analytics")

        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Intel Notes returned an invalid analytics response."
            )

        return response

    def import_artemis_note(
        self,
        *,
        record_id: UUID,
        title: str,
        body: str,
        classification: str,
        discipline: str,
        source: str | None = None,
    ) -> int:
        url = self.build_url(f"/integrations/artemis/notes/{record_id}")
        payload = {
            "title": title,
            "body": body,
            "classification": classification,
            "discipline": discipline,
            "source": source,
        }

        try:
            response = self.get_client().put(
                url,
                json=payload,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.RequestError as error:
            raise ConnectorUnavailableError(
                "Intel Notes Manager is unavailable."
            ) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 409:
                raise ConnectorResponseError(
                    "Intel Notes already imported this Artemis record "
                    "with different data."
                ) from error

            raise ConnectorResponseError(
                f"Intel Notes import returned HTTP {error.response.status_code}."
            ) from error

        try:
            note = response.json()
        except ValueError as error:
            raise ConnectorResponseError(
                "Intel Notes import returned invalid JSON."
            ) from error

        note_id = note.get("id") if isinstance(note, dict) else None
        if type(note_id) is not int or note_id < 1:
            raise ConnectorResponseError(
                "Intel Notes import returned an invalid note ID."
            )

        return note_id