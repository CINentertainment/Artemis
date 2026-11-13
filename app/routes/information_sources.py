from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.database import get_database
from app.models import InformationSource
from app.schemas import (
    InformationSourceCreate,
    InformationSourceResponse,
    InformationSourceUpdate,
)
from app.services.audit import record_audit_event
from app.services.information_sources import (
    InformationSourceAlreadyExistsError,
    archive_information_source,
    create_information_source,
    get_information_source,
    list_information_sources,
    update_information_source,
)


router = APIRouter(
    prefix="/information-sources",
    tags=["information-sources"],
)


InformationSourceKey = Annotated[
    str,
    Path(
        min_length=1,
        max_length=100,
        pattern=(
            r"^[A-Za-z]"
            r"[A-Za-z0-9_.-]{0,99}$"
        ),
    ),
]


def build_information_source_response(
    source: InformationSource,
) -> InformationSourceResponse:
    return InformationSourceResponse(
        id=source.id,
        key=source.key,
        name=source.name,
        source_type=source.source_type,
        description=source.description,
        location=source.location,
        status=source.status,
        is_enabled=source.is_enabled,
        is_read_only=source.is_read_only,
        notes=source.notes,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@router.post(
    "",
    response_model=InformationSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_information_source(
    payload: InformationSourceCreate,
    database: Session = Depends(get_database),
) -> InformationSourceResponse:
    try:
        source = create_information_source(
            database,
            key=payload.key,
            name=payload.name,
            source_type=payload.source_type,
            description=payload.description,
            location=payload.location,
            is_enabled=payload.is_enabled,
            is_read_only=payload.is_read_only,
            notes=payload.notes,
        )
    except InformationSourceAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "An information source with this "
                "key already exists."
            ),
        ) from error

    record_audit_event(
        database,
        event_type="information",
        action="information_source_created",
        outcome="success",
        resource_type="information_source",
        resource_id=source.id,
        detail=f"Registered source: {source.key}",
    )

    return build_information_source_response(
        source
    )


@router.get(
    "",
    response_model=list[
        InformationSourceResponse
    ],
)
def read_information_sources(
    include_archived: bool = Query(
        default=False,
    ),
    database: Session = Depends(get_database),
) -> list[InformationSourceResponse]:
    sources = list_information_sources(
        database,
        include_archived=include_archived,
    )

    return [
        build_information_source_response(source)
        for source in sources
    ]


@router.get(
    "/{key}",
    response_model=InformationSourceResponse,
)
def read_information_source(
    key: InformationSourceKey,
    database: Session = Depends(get_database),
) -> InformationSourceResponse:
    source = get_information_source(
        database,
        key,
    )

    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Information source not found.",
        )

    return build_information_source_response(
        source
    )


@router.patch(
    "/{key}",
    response_model=InformationSourceResponse,
)
def revise_information_source(
    key: InformationSourceKey,
    payload: InformationSourceUpdate,
    database: Session = Depends(get_database),
) -> InformationSourceResponse:
    source = get_information_source(
        database,
        key,
    )

    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Information source not found.",
        )

    changes = payload.model_dump(
        exclude_unset=True
    )

    for required_field in (
        "name",
        "source_type",
        "status",
    ):
        if changes.get(required_field) is None:
            changes.pop(
                required_field,
                None,
            )

    source = update_information_source(
        database,
        source,
        changes=changes,
    )

    record_audit_event(
        database,
        event_type="information",
        action="information_source_updated",
        outcome="success",
        resource_type="information_source",
        resource_id=source.id,
        detail=f"Updated source: {source.key}",
    )

    return build_information_source_response(
        source
    )


@router.delete(
    "/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_information_source(
    key: InformationSourceKey,
    database: Session = Depends(get_database),
) -> Response:
    source = get_information_source(
        database,
        key,
    )

    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Information source not found.",
        )

    archive_information_source(
        database,
        source,
    )

    record_audit_event(
        database,
        event_type="information",
        action="information_source_archived",
        outcome="success",
        resource_type="information_source",
        resource_id=source.id,
        detail=f"Archived source: {source.key}",
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )