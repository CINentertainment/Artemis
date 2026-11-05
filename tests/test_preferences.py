from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.services.preferences import (
    delete_preference,
    get_preference,
    list_preferences,
    upsert_preference,
)


def build_preference_test_engine(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    initialize_database(engine)

    return engine


def test_create_preference(
    tmp_path: Path,
):
    engine = build_preference_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        preference = upsert_preference(
            database,
            key="Communication_Style",
            value="Clear and practical",
        )

        assert (
            preference.key
            == "communication_style"
        )

        assert (
            preference.value
            == "Clear and practical"
        )

        assert preference.is_sensitive is False

    engine.dispose()


def test_update_existing_preference(
    tmp_path: Path,
):
    engine = build_preference_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        original = upsert_preference(
            database,
            key="favorite_color",
            value="Black",
        )

        updated = upsert_preference(
            database,
            key="favorite_color",
            value="Purple",
        )

        assert updated.id == original.id
        assert updated.value == "Purple"

        preferences = list_preferences(
            database
        )

        assert len(preferences) == 1

    engine.dispose()


def test_list_preferences_is_sorted(
    tmp_path: Path,
):
    engine = build_preference_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        upsert_preference(
            database,
            key="voice_speed",
            value="normal",
        )

        upsert_preference(
            database,
            key="communication_style",
            value="direct",
        )

        preferences = list_preferences(
            database
        )

        assert [
            preference.key
            for preference in preferences
        ] == [
            "communication_style",
            "voice_speed",
        ]

    engine.dispose()


def test_delete_preference(
    tmp_path: Path,
):
    engine = build_preference_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        upsert_preference(
            database,
            key="temporary_setting",
            value="remove me",
        )

        deleted = delete_preference(
            database,
            "temporary_setting",
        )

        assert deleted is True

        assert get_preference(
            database,
            "temporary_setting",
        ) is None

        assert delete_preference(
            database,
            "missing_setting",
        ) is False

    engine.dispose()