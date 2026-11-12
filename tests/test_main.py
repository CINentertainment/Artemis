from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_read_root():
    response = client.get("/")

    assert response.status_code == 200

    assert response.json() == {
        "application": "Artemis",
        "version": "0.1.0",
        "environment": "development",
        "status": "operational",
        "private_mode": True,
        "telemetry_enabled": False,
        "external_access_enabled": False,
    }


def test_dashboard():
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers[
        "content-type"
    ]
    assert "Artemis" in response.text
    assert "/static/styles.css" in response.text
    assert "/static/app.js" in response.text


def test_dashboard_stylesheet():
    response = client.get(
        "/static/styles.css"
    )

    assert response.status_code == 200
    assert "text/css" in response.headers[
        "content-type"
    ]
    assert len(response.text.strip()) > 0


def test_dashboard_javascript():
    response = client.get(
        "/static/app.js"
    )

    assert response.status_code == 200
    assert (
        "javascript"
        in response.headers["content-type"]
    )
    assert len(response.text.strip()) > 0


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200

    assert response.json() == {
        "status": "healthy",
        "application": "Artemis",
        "version": "0.1.0",
    }


def test_list_services():
    response = client.get("/services")

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["service_count"] == 5
    assert len(response_data["services"]) == 5

    service_keys = {
        service["key"]
        for service in response_data["services"]
    }

    assert service_keys == {
        "odysseus",
        "chroma",
        "intel-notes",
        "risk-tracker",
        "market-lens",
    }


def test_chroma_is_required():
    response = client.get("/services")

    services = response.json()["services"]

    chroma_service = next(
        service
        for service in services
        if service["key"] == "chroma"
    )

    assert chroma_service["required"] is True
    assert chroma_service["enabled"] is True


def test_odysseus_is_transitional():
    response = client.get("/services")

    services = response.json()["services"]

    odysseus_service = next(
        service
        for service in services
        if service["key"] == "odysseus"
    )

    assert (
        odysseus_service["category"]
        == "temporary-interface"
    )

    assert odysseus_service["required"] is False