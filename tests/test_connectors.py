import json
import httpx
import pytest

from app.connectors.base import (
    ConnectorResponseError,
    ConnectorSecurityError,
    ConnectorUnavailableError,
)
from app.connectors.http import (
    LocalHttpConnector,
    validate_local_base_url,
    validate_relative_path,
)


def build_test_connector(
    handler,
) -> LocalHttpConnector:
    transport = httpx.MockTransport(
        handler
    )

    client = httpx.Client(
        transport=transport
    )

    return LocalHttpConnector(
        connector_key="test-service",
        connector_name="Test Service",
        base_url="http://127.0.0.1:9000",
        capabilities=(
            "test.search",
            "test.read",
        ),
        timeout_seconds=2.0,
        client=client,
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8000",
        "http://127.1.2.3:8000",
        "http://localhost:8000",
        "http://[::1]:8000",
        "https://localhost:8443",
    ],
)
def test_loopback_urls_are_accepted(
    base_url: str,
):
    assert (
        validate_local_base_url(base_url)
        == base_url
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com",
        "https://192.168.1.20:8000",
        "http://10.0.0.5:8000",
        "http://localhost.evil.example",
        "ftp://127.0.0.1:8000",
        "file:///private/database",
        "http://user:password@127.0.0.1:8000",
        "http://127.0.0.1:8000?secret=value",
        "http://127.0.0.1:8000#fragment",
    ],
)
def test_unsafe_connector_urls_are_rejected(
    base_url: str,
):
    with pytest.raises(
        ConnectorSecurityError,
    ):
        validate_local_base_url(base_url)


@pytest.mark.parametrize(
    "path",
    [
        "health",
        "//example.com/data",
        "http://example.com/data",
        "https://127.0.0.1:9000/data",
        "/safe/../private",
        "",
    ],
)
def test_unsafe_paths_are_rejected(
    path: str,
):
    with pytest.raises(
        ConnectorSecurityError,
    ):
        validate_relative_path(path)


def test_connector_identity_and_capabilities():
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            json={"status": "healthy"},
        )
    )

    assert (
        connector.connector_key
        == "test-service"
    )

    assert (
        connector.connector_name
        == "Test Service"
    )

    assert connector.capabilities == (
        "test.search",
        "test.read",
    )


def test_health_check_reports_available():
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            json={"status": "healthy"},
        )
    )

    result = connector.check_health()

    assert result.available is True
    assert result.status_code == 200
    assert result.error is None
    assert result.response_time_ms >= 0


def test_health_check_fails_safely():
    def unavailable_handler(request):
        raise httpx.ConnectError(
            "Connection refused",
            request=request,
        )

    connector = build_test_connector(
        unavailable_handler
    )

    result = connector.check_health()

    assert result.available is False
    assert result.status_code is None

    assert result.error == (
        "Local connector service is "
        "unavailable."
    )


def test_read_json_returns_dictionary():
    def handler(request):
        assert request.method == "GET"

        assert (
            request.url.path
            == "/items"
        )

        assert (
            request.url.params["limit"]
            == "10"
        )

        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": 1,
                        "name": "Test",
                    }
                ]
            },
        )

    connector = build_test_connector(
        handler
    )

    response = connector.read_json(
        "/items",
        params={"limit": 10},
    )

    assert response == {
        "items": [
            {
                "id": 1,
                "name": "Test",
            }
        ]
    }


def test_read_json_returns_list():
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "symbol": "AAPL",
                }
            ],
        )
    )

    response = connector.read_json(
        "/assets"
    )

    assert response == [
        {
            "symbol": "AAPL",
        }
    ]


def test_request_failure_raises_unavailable():
    def unavailable_handler(request):
        raise httpx.ConnectError(
            "Connection refused",
            request=request,
        )

    connector = build_test_connector(
        unavailable_handler
    )

    with pytest.raises(
        ConnectorUnavailableError,
        match="unavailable",
    ):
        connector.read_json("/items")


def test_http_error_does_not_leak_body():
    private_body = (
        "private connector information"
    )

    connector = build_test_connector(
        lambda request: httpx.Response(
            500,
            text=private_body,
        )
    )

    with pytest.raises(
        ConnectorResponseError,
    ) as captured_error:
        connector.read_json("/items")

    assert "HTTP status 500" in str(
        captured_error.value
    )

    assert private_body not in str(
        captured_error.value
    )


def test_invalid_json_is_rejected():
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            text="not-json",
        )
    )

    with pytest.raises(
        ConnectorResponseError,
        match="invalid JSON",
    ):
        connector.read_json("/items")


@pytest.mark.parametrize(
    "json_value",
    [
        "private text",
        42,
        True,
        None,
    ],
)
def test_unsupported_json_values_are_rejected(
    json_value,
):
    connector = build_test_connector(
        lambda request: httpx.Response(
            200,
            content=json.dumps(
                json_value
            ).encode("utf-8"),
            headers={
                "content-type": (
                    "application/json"
                ),
            },
        )
    )

    with pytest.raises(
        ConnectorResponseError,
        match="unsupported",
    ):
        connector.read_json("/items")