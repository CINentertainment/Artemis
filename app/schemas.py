from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


ServiceStatus = Literal[
    "configured",
    "available",
    "unavailable",
    "disabled",
]


class ApplicationResponse(BaseModel):
    application: str
    version: str
    environment: str
    status: str
    private_mode: bool
    telemetry_enabled: bool
    external_access_enabled: bool


class HealthResponse(BaseModel):
    status: str
    application: str
    version: str


class ServiceDefinition(BaseModel):
    key: str = Field(
        min_length=1,
        max_length=50,
    )

    name: str = Field(
        min_length=1,
        max_length=100,
    )

    category: str = Field(
        min_length=1,
        max_length=50,
    )

    base_url: HttpUrl
    description: str
    required: bool = False
    enabled: bool = True
    status: ServiceStatus = "configured"


class ServiceRegistryResponse(BaseModel):
    services: list[ServiceDefinition]
    service_count: int = Field(ge=0)