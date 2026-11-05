from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import StorageHealthResponse


client = TestClient(app)


def test_storage_health_endpoint():
    health_result = StorageHealthResponse(
        status="healthy",
        database_initialized=True,
        readable=True,
        writable=True,
        checked_at=datetime.now(timezone.utc),
    )

    with patch(
        "app.main.check_storage_health",
        return_value=health_result,
    ):
        response = client.get(
            "/storage/health"
        )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["status"] == "healthy"

    assert (
        response_data["database_initialized"]
        is True
    )

    assert response_data["readable"] is True
    assert response_data["writable"] is True

    assert "database_path" not in response_data
    assert "database_url" not in response_data