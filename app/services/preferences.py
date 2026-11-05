from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models


def normalize_preference_key(
    key: str,
) -> str:
    return key.strip().lower()


def get_preference(
    database: Session,
    key: str,
) -> models.UserPreference | None:
    normalized_key = normalize_preference_key(
        key
    )

    statement = select(
        models.UserPreference
    ).where(
        models.UserPreference.key
        == normalized_key
    )

    return database.scalar(statement)


def list_preferences(
    database: Session,
) -> list[models.UserPreference]:
    statement = select(
        models.UserPreference
    ).order_by(
        models.UserPreference.key.asc()
    )

    return list(
        database.scalars(statement).all()
    )


def upsert_preference(
    database: Session,
    *,
    key: str,
    value: str,
    value_type: str = "text",
    is_sensitive: bool = False,
) -> models.UserPreference:
    normalized_key = normalize_preference_key(
        key
    )

    preference = get_preference(
        database,
        normalized_key,
    )

    if preference is None:
        preference = models.UserPreference(
            key=normalized_key,
            value=value,
            value_type=value_type,
            is_sensitive=is_sensitive,
        )

        database.add(preference)

    else:
        preference.value = value
        preference.value_type = value_type
        preference.is_sensitive = (
            is_sensitive
        )

    try:
        database.commit()
        database.refresh(preference)
    except Exception:
        database.rollback()
        raise

    return preference


def delete_preference(
    database: Session,
    key: str,
) -> bool:
    preference = get_preference(
        database,
        key,
    )

    if preference is None:
        return False

    try:
        database.delete(preference)
        database.commit()
    except Exception:
        database.rollback()
        raise

    return True