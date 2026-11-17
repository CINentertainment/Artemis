from typing import Any
from uuid import UUID

import httpx

from app.connectors.base import (
    ConnectorResponseError,
    ConnectorUnavailableError,
)
from app.connectors.http import LocalHttpConnector


class RiskTrackerAuthenticationError(ConnectorResponseError):
    pass


class RiskTrackerConnector(LocalHttpConnector):
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
        expected_role: str = "Read Only",
    ) -> None:
        cleaned_username = username.strip()
        if not cleaned_username:
            raise ValueError("Risk Tracker username cannot be empty.")
        if not password:
            raise ValueError("Risk Tracker password cannot be empty.")
        if expected_role not in {"Read Only", "Analyst", "Administrator"}:
            raise ValueError("Risk Tracker role must be Read Only, Analyst, or Administrator.")

        capabilities = (
            "risk_tracker.health",
            "risk_tracker.authenticate",
            "risk_tracker.list_cases",
            "risk_tracker.get_case",
            "risk_tracker.list_activities",
            "risk_tracker.list_reviews",
            "risk_tracker.list_indicators",
            "risk_tracker.list_assessments",
            "risk_tracker.list_mitigations",
        )
        if expected_role in {"Analyst", "Administrator"}:
            capabilities += ("risk_tracker.attach_case_evidence",)

        super().__init__(
            connector_key="risk-tracker",
            connector_name="Protective Intelligence Risk Tracker",
            base_url=base_url,
            capabilities=capabilities,
            timeout_seconds=timeout_seconds,
            health_path="/health",
            client=client,
        )
        self.username = cleaned_username
        self._password = password
        self._expected_role = expected_role
        self._authenticated = False

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def _parse_json_response(
        self, response: httpx.Response
    ) -> dict[str, Any] | list[Any]:
        try:
            response_data = response.json()
        except ValueError as error:
            raise ConnectorResponseError(
                "Risk Tracker returned invalid JSON."
            ) from error

        if not isinstance(response_data, (dict, list)):
            raise ConnectorResponseError(
                "Risk Tracker returned an unsupported JSON value."
            )
        return response_data

    def authenticate(self) -> dict[str, Any]:
        self._authenticated = False
        try:
            response = self.get_client().post(
                self.build_url("/auth/login"),
                json={"username": self.username, "password": self._password},
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.RequestError as error:
            raise ConnectorUnavailableError(
                "Risk Tracker is unavailable."
            ) from error
        except httpx.HTTPStatusError as error:
            raise RiskTrackerAuthenticationError(
                "Risk Tracker authentication failed."
            ) from error

        response_data = self._parse_json_response(response)
        if not isinstance(response_data, dict):
            raise RiskTrackerAuthenticationError(
                "Risk Tracker returned an invalid authentication response."
            )

        user = response_data.get("user")
        if not isinstance(user, dict):
            raise RiskTrackerAuthenticationError(
                "Risk Tracker did not return an authenticated user."
            )

        returned_username = user.get("username")
        if (
            not isinstance(returned_username, str)
            or returned_username.strip().lower() != self.username.lower()
        ):
            raise RiskTrackerAuthenticationError(
                "Risk Tracker returned an unexpected authenticated user."
            )

        if user.get("role") != self._expected_role:
            raise RiskTrackerAuthenticationError(
                f"The Artemis Risk Tracker account must have the "
                f"{self._expected_role} role."
            )

        self._authenticated = True
        return user

    def ensure_authenticated(self) -> None:
        if not self._authenticated:
            self.authenticate()

    def _request_authenticated_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int | float | bool] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        self.ensure_authenticated()
        url = self.build_url(path)

        try:
            response = self.get_client().request(
                method,
                url,
                params=params,
                json=payload,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.RequestError as error:
            raise ConnectorUnavailableError(
                "Risk Tracker is unavailable."
            ) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401:
                self._authenticated = False
                raise RiskTrackerAuthenticationError(
                    "Risk Tracker authentication is no longer valid."
                ) from error
            if method == "PUT" and error.response.status_code == 409:
                raise ConnectorResponseError(
                    "This case already has different evidence for this Artemis record."
                ) from error
            raise ConnectorResponseError(
                f"Risk Tracker returned HTTP status {error.response.status_code}."
            ) from error

        return self._parse_json_response(response)

    def _read_authenticated_json(
        self,
        path: str,
        *,
        params: dict[str, str | int | float | bool] | None = None,
    ) -> dict[str, Any] | list[Any]:
        return self._request_authenticated_json("GET", path, params=params)

    def get_current_user(self) -> dict[str, Any]:
        response = self._read_authenticated_json("/auth/me")
        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Risk Tracker returned an invalid user record."
            )
        return response

    def list_cases(
        self, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        response = self._read_authenticated_json(
            "/cases", params={"include_archived": include_archived}
        )
        if not isinstance(response, list):
            raise ConnectorResponseError(
                "Risk Tracker returned an invalid case collection."
            )
        if not all(isinstance(case, dict) for case in response):
            raise ConnectorResponseError(
                "Risk Tracker returned an invalid case record."
            )
        return response

    def get_case(self, case_id: int) -> dict[str, Any]:
        self._validate_case_id(case_id)
        response = self._read_authenticated_json(f"/cases/{case_id}")
        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Risk Tracker returned an invalid case record."
            )
        return response

    def attach_case_evidence(
        self,
        *,
        case_id: int,
        artemis_record_id: UUID,
        title: str,
        source_url: str,
        content: str,
        intel_note_id: int | None = None,
    ) -> int:
        self._validate_case_id(case_id)
        if self._expected_role not in {"Analyst", "Administrator"}:
            raise RiskTrackerAuthenticationError(
                "Attaching case evidence requires an Analyst or Administrator connector."
            )

        record_id = str(artemis_record_id)
        response = self._request_authenticated_json(
            "PUT",
            f"/cases/{case_id}/evidence/{record_id}",
            payload={
                "title": title,
                "source_url": source_url,
                "content": content,
                "intel_note_id": intel_note_id,
            },
        )
        if not isinstance(response, dict):
            raise ConnectorResponseError(
                "Risk Tracker returned an invalid evidence record."
            )

        evidence_id = response.get("id")
        if (
            type(evidence_id) is not int
            or evidence_id < 1
            or response.get("case_id") != case_id
            or response.get("artemis_record_id") != record_id
        ):
            raise ConnectorResponseError(
                "Risk Tracker returned an unexpected evidence record."
            )
        return evidence_id

    def list_case_activities(self, case_id: int) -> list[dict[str, Any]]:
        return self._read_case_collection(case_id, "activities")

    def list_case_reviews(self, case_id: int) -> list[dict[str, Any]]:
        return self._read_case_collection(case_id, "reviews")

    def list_case_indicators(self, case_id: int) -> list[dict[str, Any]]:
        return self._read_case_collection(case_id, "indicators")

    def list_case_assessments(self, case_id: int) -> list[dict[str, Any]]:
        return self._read_case_collection(case_id, "assessments")

    def list_case_mitigations(self, case_id: int) -> list[dict[str, Any]]:
        return self._read_case_collection(case_id, "mitigations")

    def _read_case_collection(
        self, case_id: int, resource_name: str
    ) -> list[dict[str, Any]]:
        self._validate_case_id(case_id)
        response = self._read_authenticated_json(
            f"/cases/{case_id}/{resource_name}"
        )
        if not isinstance(response, list):
            raise ConnectorResponseError(
                f"Risk Tracker returned an invalid {resource_name} collection."
            )
        if not all(isinstance(record, dict) for record in response):
            raise ConnectorResponseError(
                f"Risk Tracker returned an invalid {resource_name} record."
            )
        return response

    @staticmethod
    def _validate_case_id(case_id: int) -> None:
        if case_id < 1:
            raise ValueError("Risk Tracker case ID must be greater than zero.")