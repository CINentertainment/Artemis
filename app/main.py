from fastapi import FastAPI

from app.config import get_settings
from app.schemas import (
    ApplicationResponse,
    HealthResponse,
    ServiceHealthResponse,
    ServiceRegistryResponse,
)
from app.security import configure_security
from app.services.health import (
    check_registered_services,
)
from app.services.registry import build_service_registry


settings = get_settings()


app = FastAPI(
    title=settings.application_name,
    description=(
        "Private, local-first intelligence assistant "
        "developed by CIN Entertainment."
    ),
    version=settings.application_version,
    docs_url="/docs",
    redoc_url=None,
)

configure_security(app)


@app.get(
    "/",
    response_model=ApplicationResponse,
)
def read_root() -> ApplicationResponse:
    return ApplicationResponse(
        application=settings.application_name,
        version=settings.application_version,
        environment=settings.environment,
        status="operational",
        private_mode=(
            not settings.external_access_enabled
        ),
        telemetry_enabled=(
            settings.telemetry_enabled
        ),
        external_access_enabled=(
            settings.external_access_enabled
        ),
    )


@app.get(
    "/health",
    response_model=HealthResponse,
)
def health_check() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        application=settings.application_name,
        version=settings.application_version,
    )


@app.get(
    "/services",
    response_model=ServiceRegistryResponse,
)
def list_services() -> ServiceRegistryResponse:
    services = build_service_registry(settings)

    return ServiceRegistryResponse(
        services=services,
        service_count=len(services),
    )


@app.get(
    "/services/health",
    response_model=ServiceHealthResponse,
)
async def service_health() -> ServiceHealthResponse:
    return await check_registered_services(
        settings
    )