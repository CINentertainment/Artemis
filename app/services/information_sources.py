from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models


class InformationSourceAlreadyExistsError(
    Exception
):
    pass


def normalize_information_source_key(
    key: str,
) -> str:
    return key.strip().lower()


def normalize_optional_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    normalized_value = value.strip()

    return normalized_value or None


def get_information_source(
    database: Session,
    key: str,
) -> models.InformationSource | None:
    normalized_key = (
        normalize_information_source_key(key)
    )

    statement = select(
        models.InformationSource
    ).where(
        models.InformationSource.key
        == normalized_key
    )

    return database.scalar(statement)


def list_information_sources(
    database: Session,
    *,
    include_archived: bool = False,
) -> list[models.InformationSource]:
    statement = select(
        models.InformationSource
    )

    if not include_archived:
        statement = statement.where(
            models.InformationSource.status
            != "archived"
        )

    statement = statement.order_by(
        models.InformationSource.name.asc(),
        models.InformationSource.key.asc(),
    )

    return list(
        database.scalars(statement).all()
    )


def create_information_source(
    database: Session,
    *,
    key: str,
    name: str,
    source_type: str = "manual",
    description: str | None = None,
    location: str | None = None,
    is_enabled: bool = True,
    is_read_only: bool = True,
    notes: str | None = None,
) -> models.InformationSource:
    normalized_key = (
        normalize_information_source_key(key)
    )

    existing_source = get_information_source(
        database,
        normalized_key,
    )

    if existing_source is not None:
        raise InformationSourceAlreadyExistsError(
            "An information source with this "
            "key already exists."
        )

    source = models.InformationSource(
        key=normalized_key,
        name=name.strip(),
        source_type=source_type,
        description=normalize_optional_text(
            description
        ),
        location=normalize_optional_text(
            location
        ),
        is_enabled=is_enabled,
        is_read_only=is_read_only,
        notes=normalize_optional_text(notes),
    )

    database.add(source)

    try:
        database.commit()
        database.refresh(source)
    except IntegrityError as error:
        database.rollback()

        raise InformationSourceAlreadyExistsError(
            "An information source with this "
            "key already exists."
        ) from error
    except Exception:
        database.rollback()
        raise

    return source


def update_information_source(
    database: Session,
    source: models.InformationSource,
    *,
    changes: dict[str, Any],
) -> models.InformationSource:
    allowed_fields = {
        "name",
        "source_type",
        "description",
        "location",
        "status",
        "is_enabled",
        "is_read_only",
        "notes",
    }

    optional_text_fields = {
        "description",
        "location",
        "notes",
    }

    for field_name, value in changes.items():
        if field_name not in allowed_fields:
            continue

        if field_name == "name" and isinstance(
            value,
            str,
        ):
            value = value.strip()

        if field_name in optional_text_fields:
            value = normalize_optional_text(value)

        setattr(
            source,
            field_name,
            value,
        )

    try:
        database.commit()
        database.refresh(source)
    except Exception:
        database.rollback()
        raise

    return source


def archive_information_source(
    database: Session,
    source: models.InformationSource,
) -> models.InformationSource:
    source.status = "archived"
    source.is_enabled = False

    try:
        database.commit()
        database.refresh(source)
    except Exception:
        database.rollback()
        raise

    return source