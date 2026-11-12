from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.schemas import (
    ServiceHealthResponse,
    ServiceHealthResult,
)
from app.services import health


client = TestClient(app)


def build_result(
    key: str,
    required: bool,
    status: str,
) -> ServiceHealthResult:
    return ServiceHealthResult(
        key=key,
        name=key,
        required=required,
        status=status,
        response_time_ms=1.0,
    )


@pytest.mark.asyncio
async def test_all_services_available(
    monkeypatch,
):
    async def fake_check_service(
        service,
        client,
    ):
        return build_result(
            key=service.key,
            required=service.required,
            status="available",
        )

    monkeypatch.setattr(
        health,
        "check_service",
        fake_check_service,
    )

    result = await health.check_registered_services(
        get_settings()
    )

    assert result.overall_status == "healthy"


@pytest.mark.asyncio
async def test_transitional_service_failure_is_healthy(
    monkeypatch,
):
    async def fake_check_service(
        service,
        client,
    ):
        service_status = (
            "unavailable"
            if service.key == "odysseus"
            else "available"
        )

        return build_result(
            key=service.key,
            required=service.required,
            status=service_status,
        )

    monkeypatch.setattr(
        health,
        "check_service",
        fake_check_service,
    )

    result = await health.check_registered_services(
        get_settings()
    )

    assert result.overall_status == "healthy"


@pytest.mark.asyncio
async def test_active_optional_failure_is_degraded(
    monkeypatch,
):
    async def fake_check_service(
        service,
        client,
    ):
        service_status = (
            "unavailable"
            if service.key == "risk-tracker"
            else "available"
        )

        return build_result(
            key=service.key,
            required=service.required,
            status=service_status,
        )

    monkeypatch.setattr(
        health,
        "check_service",
        fake_check_service,
    )

    result = await health.check_registered_services(
        get_settings()
    )

    assert result.overall_status == "degraded"


@pytest.mark.asyncio
async def test_required_service_failure_is_unavailable(
    monkeypatch,
):
    async def fake_check_service(
        service,
        client,
    ):
        service_status = (
            "unavailable"
            if service.key == "chroma"
            else "available"
        )

        return build_result(
            key=service.key,
            required=service.required,
            status=service_status,
        )

    monkeypatch.setattr(
        health,
        "check_service",
        fake_check_service,
    )

    result = await health.check_registered_services(
        get_settings()
    )

    assert result.overall_status == "unavailable"


def test_service_health_endpoint():
    mocked_response = ServiceHealthResponse(
        overall_status="healthy",
        checked_at=datetime.now(timezone.utc),
        services=[
            build_result(
                key="chroma",
                required=True,
                status="available",
            )
        ],
    )

    with patch(
        "app.main.check_registered_services",
        new_callable=AsyncMock,
        return_value=mocked_response,
    ):
        response = client.get(
            "/services/health"
        )

    assert response.status_code == 200

    assert (
        response.json()["overall_status"]
        == "healthy"
    )

    assert (
        response.json()["services"][0]["key"]
        == "chroma"
    )