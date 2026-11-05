from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.database import get_database
from app.models import UserPreference
from app.schemas import (
    PreferenceResponse,
    PreferenceUpsert,
)
from app.services.audit import record_audit_event
from app.services.preferences import (
    delete_preference,
    get_preference,
    list_preferences,
    upsert_preference,
)


router = APIRouter(
    prefix="/preferences",
    tags=["preferences"],
)


PreferenceKey = Annotated[
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


def build_preference_response(
    preference: UserPreference,
) -> PreferenceResponse:
    is_redacted = preference.is_sensitive

    return PreferenceResponse(
        id=preference.id,
        key=preference.key,
        value=(
            None
            if is_redacted
            else preference.value
        ),
        value_type=preference.value_type,
        is_sensitive=preference.is_sensitive,
        redacted=is_redacted,
        created_at=preference.created_at,
        updated_at=preference.updated_at,
    )


@router.get(
    "",
    response_model=list[PreferenceResponse],
)
def read_preferences(
    database: Session = Depends(get_database),
) -> list[PreferenceResponse]:
    preferences = list_preferences(database)

    return [
        build_preference_response(preference)
        for preference in preferences
    ]


@router.get(
    "/{key}",
    response_model=PreferenceResponse,
)
def read_preference(
    key: PreferenceKey,
    database: Session = Depends(get_database),
) -> PreferenceResponse:
    preference = get_preference(
        database,
        key,
    )

    if preference is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Preference not found.",
        )

    return build_preference_response(
        preference
    )


@router.put(
    "/{key}",
    response_model=PreferenceResponse,
)
def write_preference(
    key: PreferenceKey,
    payload: PreferenceUpsert,
    database: Session = Depends(get_database),
) -> PreferenceResponse:
    if payload.is_sensitive:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "Sensitive preference storage is "
                "disabled until encryption is configured."
            ),
        )

    existing_preference = get_preference(
        database,
        key,
    )

    audit_action = (
        "preference_updated"
        if existing_preference is not None
        else "preference_created"
    )

    preference = upsert_preference(
        database,
        key=key,
        value=payload.value,
        value_type=payload.value_type,
        is_sensitive=False,
    )

    record_audit_event(
        database,
        event_type="memory",
        action=audit_action,
        outcome="success",
        resource_type="preference",
        resource_id=preference.key,
    )

    return build_preference_response(
        preference
    )


@router.delete(
    "/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_preference(
    key: PreferenceKey,
    database: Session = Depends(get_database),
) -> Response:
    preference = get_preference(
        database,
        key,
    )

    if preference is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Preference not found.",
        )

    normalized_key = preference.key

    delete_preference(
        database,
        normalized_key,
    )

    record_audit_event(
        database,
        event_type="memory",
        action="preference_deleted",
        outcome="success",
        resource_type="preference",
        resource_id=normalized_key,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )