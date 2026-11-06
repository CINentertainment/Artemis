from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.database import (
    SessionLocal,
    engine,
    initialize_database,
)
from app.routes.conversations import (
    router as conversations_router,
)
from app.routes.preferences import (
    router as preferences_router,
)
from app.schemas import (
    ApplicationResponse,
    HealthResponse,
    ServiceHealthResponse,
    ServiceRegistryResponse,
    StorageHealthResponse,
)
from app.security import configure_security
from app.services.health import (
    check_registered_services,
)
from app.services.lifecycle import (
    record_application_lifecycle_event,
)
from app.services.registry import build_service_registry
from app.services.storage import check_storage_health


settings = get_settings()


@asynccontextmanager
async def lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    initialize_database(engine)

    record_application_lifecycle_event(
        SessionLocal,
        "application_started",
    )

    try:
        yield
    finally:
        record_application_lifecycle_event(
            SessionLocal,
            "application_stopped",
        )


app = FastAPI(
    title=settings.application_name,
    description=(
        "Private, local-first intelligence assistant "
        "developed by CIN Entertainment."
    ),
    version=settings.application_version,
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

configure_security(app)

app.include_router(preferences_router)
app.include_router(conversations_router)


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


@app.get(
    "/storage/health",
    response_model=StorageHealthResponse,
)
def storage_health() -> StorageHealthResponse:
    return check_storage_health(engine)