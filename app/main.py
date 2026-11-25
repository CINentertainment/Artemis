import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.routes.voice import router as voice_router

from app.config import get_settings
from app.database import (
    SessionLocal,
    engine,
    initialize_database,
)
from app.routes.approvals import (
    router as approvals_router,
)
from app.routes.conversations import (
    router as conversations_router,
)
from app.routes.information_sources import (
    router as information_sources_router,
)
from app.routes.local_computer import (
    router as local_computer_router,
)
from app.routes.preferences import (
    router as preferences_router,
)
from app.routes.research import (
    router as research_router,
)
from app.routes.research_updates import (
    router as research_updates_router,
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
from app.services.local_computer import workspace_directory
from app.services.registry import build_service_registry
from app.services.research_scheduler import (
    research_scheduler,
    router as research_schedule_router,
)
from app.services.storage import check_storage_health


# Register the task table before database initialization.
from app.services.tasks import ArtemisTask
from app.routes.tasks import router as tasks_router

from app.routes.conversation_folders import router as folders_router

from app.services.task_agents import (
    task_agent_worker,
    router as task_agents_router,
)

from app.services.references import backfill_references

from app.routes.intelligence_workspace import router as intelligence_workspace_router

from app.services.research_workspace_bridge import research_workspace_worker

from app.services.market_automation import worker as market_worker, router as market_router

from app.services.risk_map import worker as atlas_worker, router as atlas_router

settings = get_settings()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    # Imported route modules register their models before table creation.
    initialize_database(engine)
    backfill_references(engine)

    workspace_directory().mkdir(
        parents=True,
        exist_ok=True,
    )

    record_application_lifecycle_event(
        SessionLocal,
        "application_started",
    )

    research_scheduler.start()
    task_agent_worker.start()
    research_workspace_worker.start()
    market_worker.start()
    atlas_worker.start()

    try:
        yield
    finally:
        try:
            await asyncio.to_thread(atlas_worker.stop)
            await asyncio.to_thread(market_worker.stop)
            await asyncio.to_thread(research_workspace_worker.stop)
            await asyncio.to_thread(task_agent_worker.stop)
            await asyncio.to_thread(research_scheduler.stop)
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

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)

app.include_router(approvals_router)
app.include_router(preferences_router)
app.include_router(conversations_router)
app.include_router(task_agents_router)
app.include_router(folders_router)
app.include_router(information_sources_router)
app.include_router(research_router)
app.include_router(research_schedule_router)
app.include_router(research_updates_router)
app.include_router(local_computer_router)
app.include_router(voice_router)
app.include_router(tasks_router)
app.include_router(intelligence_workspace_router)
app.include_router(market_router)
app.include_router(atlas_router)

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
    "/dashboard",
    include_in_schema=False,
)
def dashboard() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html"
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