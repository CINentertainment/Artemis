import asyncio
from datetime import datetime, timezone
from time import perf_counter

import httpx

from app.config import Settings
from app.schemas import (
    ServiceDefinition,
    ServiceHealthResponse,
    ServiceHealthResult,
)
from app.services.registry import build_service_registry


async def check_service(
    service: ServiceDefinition,
    client: httpx.AsyncClient,
) -> ServiceHealthResult:
    if not service.enabled:
        return ServiceHealthResult(
            key=service.key,
            name=service.name,
            required=service.required,
            status="disabled",
        )

    started_at = perf_counter()

    try:
        response = await client.get(
            str(service.base_url)
        )

        response_time_ms = round(
            (perf_counter() - started_at) * 1000,
            2,
        )

        status = (
            "available"
            if response.status_code < 500
            else "unavailable"
        )

        return ServiceHealthResult(
            key=service.key,
            name=service.name,
            required=service.required,
            status=status,
            response_time_ms=response_time_ms,
        )

    except httpx.HTTPError:
        return ServiceHealthResult(
            key=service.key,
            name=service.name,
            required=service.required,
            status="unavailable",
        )


async def check_registered_services(
    settings: Settings,
) -> ServiceHealthResponse:
    services = build_service_registry(settings)

    timeout = httpx.Timeout(1.5)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        results = await asyncio.gather(
            *[
                check_service(service, client)
                for service in services
            ]
        )

    required_service_failed = any(
        result.required
        and result.status != "available"
        for result in results
    )

    optional_service_failed = any(
        not result.required
        and result.status == "unavailable"
        for result in results
    )

    if required_service_failed:
        overall_status = "unavailable"
    elif optional_service_failed:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return ServiceHealthResponse(
        overall_status=overall_status,
        checked_at=datetime.now(timezone.utc),
        services=results,
    )