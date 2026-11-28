from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


ServiceStatus = Literal[
    "configured",
    "available",
    "unavailable",
    "disabled",
]

SystemHealthStatus = Literal[
    "healthy",
    "degraded",
    "unavailable",
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


class ServiceHealthResult(BaseModel):
    key: str
    name: str
    required: bool
    status: ServiceStatus

    response_time_ms: float | None = Field(
        default=None,
        ge=0,
    )


class ServiceHealthResponse(BaseModel):
    overall_status: SystemHealthStatus
    checked_at: datetime
    services: list[ServiceHealthResult]


class StorageHealthResponse(BaseModel):
    status: SystemHealthStatus
    database_initialized: bool
    readable: bool
    writable: bool
    checked_at: datetime


class PreferenceUpsert(BaseModel):
    value: str = Field(
        min_length=1,
        max_length=10000,
    )

    value_type: Literal[
        "text",
        "integer",
        "number",
        "boolean",
        "json",
    ] = "text"

    is_sensitive: bool = False


class PreferenceResponse(BaseModel):
    id: int
    key: str
    value: str | None
    value_type: str
    is_sensitive: bool
    redacted: bool
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    title: str | None = Field(
        default=None,
        max_length=200,
    )


class ConversationResponse(BaseModel):
    id: str
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=100000,
    )


class MessageResponse(BaseModel):
    id: int
    conversation_id: str
    role: str
    content: str
    model_name: str | None
    created_at: datetime


class ConversationTurnCreate(BaseModel):
    use_document_library: bool = False
    document_context_revision: int | None = Field(default=None,ge=0)
    # None reuses this conversation context; [] explicitly clears it.
    document_ids: list[str] | None = Field(default=None, max_length=8)

    content: str = Field(
        min_length=1,
        max_length=100000,
    )

    information_records_query: str | None = Field(
        default=None,
        min_length=1,
        max_length=1500,
    )
    use_intel_notes: bool = False

    intel_notes_query: str | None = Field(
        default=None,
        min_length=1,
        max_length=10000,
    )

    use_risk_tracker: bool = False

    risk_tracker_case_id: int | None = Field(
        default=None,
        ge=1,
    )

    risk_tracker_include_archived: bool = False
    risk_tracker_include_related: bool = True

    use_market_lens: bool = False

    market_lens_asset_id: int | None = Field(
        default=None,
        ge=1,
    )

    market_lens_include_inactive: bool = False
    market_lens_include_prices: bool = True
    market_lens_include_analytics: bool = True
    market_lens_include_signals: bool = True

    market_lens_start_date: str | None = Field(
        default=None,
        min_length=1,
        max_length=10,
    )

    market_lens_end_date: str | None = Field(
        default=None,
        min_length=1,
        max_length=10,
    )


class ConversationTurnResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse
    provider: str
    model: str
    finish_reason: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int


class ApprovalRequestCreate(BaseModel):
    operation: str = Field(
        min_length=1,
        max_length=100,
    )

    resource_type: str = Field(
        min_length=1,
        max_length=100,
    )

    resource_id: str | None = Field(
        default=None,
        max_length=100,
    )

    request_payload: dict[str, Any] | None = None


class ApprovalDecisionCreate(BaseModel):
    decision: Literal[
        "approved",
        "rejected",
    ]

    reason: str | None = Field(
        default=None,
        max_length=2000,
    )


class ApprovalResponse(BaseModel):
    id: str
    operation: str
    resource_type: str
    resource_id: str | None
    request_payload: dict[str, Any] | None
    status: str
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None

InformationSourceType = Literal[
    "manual",
    "document",
    "url",
    "api",
    "connector",
    "feed",
]

InformationSourceStatus = Literal[
    "active",
    "inactive",
    "archived",
]


class InformationSourceCreate(BaseModel):
    key: str = Field(
        min_length=1,
        max_length=100,
        pattern=(
            r"^[a-z]"
            r"[a-z0-9_.-]{0,99}$"
        ),
    )

    name: str = Field(
        min_length=1,
        max_length=200,
    )

    source_type: InformationSourceType = (
        "manual"
    )

    description: str | None = Field(
        default=None,
        max_length=10000,
    )

    location: str | None = Field(
        default=None,
        max_length=5000,
    )

    is_enabled: bool = True
    is_read_only: bool = True

    notes: str | None = Field(
        default=None,
        max_length=10000,
    )


class InformationSourceUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )

    source_type: (
        InformationSourceType | None
    ) = None

    description: str | None = Field(
        default=None,
        max_length=10000,
    )

    location: str | None = Field(
        default=None,
        max_length=5000,
    )

    status: InformationSourceStatus | None = None
    is_enabled: bool | None = None
    is_read_only: bool | None = None

    notes: str | None = Field(
        default=None,
        max_length=10000,
    )


class InformationSourceResponse(BaseModel):
    id: str
    key: str
    name: str
    source_type: str
    description: str | None
    location: str | None
    status: str
    is_enabled: bool
    is_read_only: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime