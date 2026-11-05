from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_security_headers_are_present():
    response = client.get("/health")

    assert response.status_code == 200

    assert (
        response.headers["cache-control"]
        == "no-store"
    )

    assert (
        response.headers["x-content-type-options"]
        == "nosniff"
    )

    assert (
        response.headers["x-frame-options"]
        == "DENY"
    )

    assert (
        response.headers["referrer-policy"]
        == "no-referrer"
    )

    assert (
        response.headers[
            "cross-origin-resource-policy"
        ]
        == "same-origin"
    )


def test_untrusted_host_is_rejected():
    response = client.get(
        "/health",
        headers={
            "host": "unauthorized.example",
        },
    )

    assert response.status_code == 400
    assert response.text == "Invalid host header"