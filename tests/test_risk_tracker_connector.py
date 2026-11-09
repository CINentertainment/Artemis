import httpx
import pytest

from app.connectors.base import (
    ConnectorResponseError,
    ConnectorSecurityError,
    ConnectorUnavailableError,
)
from app.connectors.risk_tracker import (
    RiskTrackerAuthenticationError,
    RiskTrackerConnector,
)


BASE_URL = "http://127.0.0.1:8000"
USERNAME = "artemis_readonly"
PASSWORD = "private-test-password"


def build_connector(
    handler,
) -> RiskTrackerConnector:
    transport = httpx.MockTransport(
        handler
    )

    client = httpx.Client(
        transport=transport,
        follow_redirects=False,
    )

    return RiskTrackerConnector(
        base_url=BASE_URL,
        username=USERNAME,
        password=PASSWORD,
        client=client,
    )


def authentication_response(
    request: httpx.Request,
) -> httpx.Response:
    assert request.method == "POST"
    assert request.url.path == "/auth/login"

    request_data = request.content.decode(
        "utf-8"
    )

    assert USERNAME in request_data
    assert PASSWORD in request_data

    return httpx.Response(
        200,
        json={
            "message": "Login successful.",
            "user": {
                "id": 7,
                "username": USERNAME,
                "full_name": (
                    "Artemis Read-Only Service"
                ),
                "role": "Read Only",
                "is_active": True,
            },
        },
        headers={
            "set-cookie": (
                "pirt_access_token=test-token; "
                "HttpOnly; Path=/; SameSite=Strict"
            ),
        },
    )


def test_connector_metadata():
    connector = build_connector(
        authentication_response
    )

    assert (
        connector.connector_key
        == "risk-tracker"
    )

    assert (
        connector.connector_name
        == (
            "Protective Intelligence "
            "Risk Tracker"
        )
    )

    assert (
        "risk_tracker.list_cases"
        in connector.capabilities
    )

    assert (
        "risk_tracker.get_case"
        in connector.capabilities
    )

    assert all(
        "create" not in capability
        and "update" not in capability
        and "delete" not in capability
        and "archive" not in capability
        for capability in connector.capabilities
    )

    connector.close()


def test_connector_only_accepts_loopback_url():
    with pytest.raises(
        ConnectorSecurityError,
        match="loopback",
    ):
        RiskTrackerConnector(
            base_url=(
                "https://risk-tracker.example.com"
            ),
            username=USERNAME,
            password=PASSWORD,
        )


def test_empty_username_is_rejected():
    with pytest.raises(
        ValueError,
        match="username",
    ):
        RiskTrackerConnector(
            base_url=BASE_URL,
            username="   ",
            password=PASSWORD,
        )


def test_empty_password_is_rejected():
    with pytest.raises(
        ValueError,
        match="password",
    ):
        RiskTrackerConnector(
            base_url=BASE_URL,
            username=USERNAME,
            password="",
        )


def test_authentication_requires_read_only_role():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": "Login successful.",
                "user": {
                    "id": 1,
                    "username": USERNAME,
                    "role": "Administrator",
                    "is_active": True,
                },
            },
        )

    connector = build_connector(handler)

    with pytest.raises(
        RiskTrackerAuthenticationError,
        match="Read Only",
    ):
        connector.authenticate()

    assert connector.authenticated is False

    connector.close()


def test_authentication_failure_is_private():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "detail": (
                    "Invalid username or password."
                ),
            },
        )

    connector = build_connector(handler)

    with pytest.raises(
        RiskTrackerAuthenticationError,
        match="authentication failed",
    ) as error:
        connector.authenticate()

    error_message = str(error.value)

    assert PASSWORD not in error_message
    assert USERNAME not in error_message
    assert connector.authenticated is False

    connector.close()


def test_authentication_marks_connector_authenticated():
    connector = build_connector(
        authentication_response
    )

    user = connector.authenticate()

    assert connector.authenticated is True
    assert user["username"] == USERNAME
    assert user["role"] == "Read Only"

    connector.close()


def test_list_cases_authenticates_and_uses_cookie():
    request_paths: list[str] = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        request_paths.append(
            request.url.path
        )

        if request.url.path == "/auth/login":
            return authentication_response(
                request
            )

        assert request.method == "GET"
        assert request.url.path == "/cases"

        assert (
            request.url.params[
                "include_archived"
            ]
            == "false"
        )

        assert (
            "pirt_access_token=test-token"
            in request.headers.get(
                "cookie",
                "",
            )
        )

        return httpx.Response(
            200,
            json=[
                {
                    "id": 4,
                    "case_number": "PIRT-004",
                    "title": "Protective case",
                    "summary": "Private summary",
                    "status": "Open",
                    "priority": "Routine",
                    "risk_level": "Moderate",
                    "is_archived": False,
                },
            ],
        )

    connector = build_connector(handler)

    cases = connector.list_cases()

    assert request_paths == [
        "/auth/login",
        "/cases",
    ]

    assert len(cases) == 1
    assert cases[0]["id"] == 4
    assert connector.authenticated is True

    connector.close()


def test_authenticated_session_is_reused():
    login_count = 0

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal login_count

        if request.url.path == "/auth/login":
            login_count += 1

            return authentication_response(
                request
            )

        if request.url.path == "/cases":
            return httpx.Response(
                200,
                json=[],
            )

        return httpx.Response(
            200,
            json={
                "id": 2,
                "case_number": "PIRT-002",
            },
        )

    connector = build_connector(handler)

    connector.list_cases()
    connector.get_case(2)

    assert login_count == 1

    connector.close()


def test_get_case_validates_case_id():
    connector = build_connector(
        authentication_response
    )

    with pytest.raises(
        ValueError,
        match="greater than zero",
    ):
        connector.get_case(0)

    connector.close()


@pytest.mark.parametrize(
    (
        "method_name",
        "expected_path",
    ),
    [
        (
            "list_case_activities",
            "/cases/3/activities",
        ),
        (
            "list_case_reviews",
            "/cases/3/reviews",
        ),
        (
            "list_case_indicators",
            "/cases/3/indicators",
        ),
        (
            "list_case_assessments",
            "/cases/3/assessments",
        ),
        (
            "list_case_mitigations",
            "/cases/3/mitigations",
        ),
    ],
)
def test_case_collection_methods(
    method_name: str,
    expected_path: str,
):
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.path == "/auth/login":
            return authentication_response(
                request
            )

        assert request.method == "GET"
        assert request.url.path == expected_path

        return httpx.Response(
            200,
            json=[
                {
                    "id": 11,
                    "case_id": 3,
                },
            ],
        )

    connector = build_connector(handler)

    method = getattr(
        connector,
        method_name,
    )

    records = method(3)

    assert len(records) == 1
    assert records[0]["case_id"] == 3

    connector.close()


def test_invalid_case_collection_is_rejected():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.path == "/auth/login":
            return authentication_response(
                request
            )

        return httpx.Response(
            200,
            json={
                "unexpected": "object",
            },
        )

    connector = build_connector(handler)

    with pytest.raises(
        ConnectorResponseError,
        match="collection",
    ):
        connector.list_case_indicators(3)

    connector.close()


def test_expired_authentication_is_rejected():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.path == "/auth/login":
            return authentication_response(
                request
            )

        return httpx.Response(
            401,
            json={
                "detail": (
                    "Authentication required."
                ),
            },
        )

    connector = build_connector(handler)

    with pytest.raises(
        RiskTrackerAuthenticationError,
        match="no longer valid",
    ):
        connector.list_cases()

    assert connector.authenticated is False

    connector.close()


def test_unavailable_service_is_reported():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        raise httpx.ConnectError(
            "Connection refused.",
            request=request,
        )

    connector = build_connector(handler)

    with pytest.raises(
        ConnectorUnavailableError,
        match="unavailable",
    ):
        connector.authenticate()

    connector.close()