from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.services.identity import (
    build_identity_context,
)
from app.services.preferences import (
    upsert_preference,
)


def build_test_engine(
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


def test_empty_preferences_return_no_context(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        assert (
            build_identity_context(database)
            is None
        )

    engine.dispose()


def test_approved_preferences_build_context(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        upsert_preference(
            database,
            key="identity.preferred_name",
            value="Daryl",
        )

        upsert_preference(
            database,
            key="behavior.communication_style",
            value="Clear, practical, and direct",
        )

        upsert_preference(
            database,
            key="domains.primary",
            value=(
                "HUMINT, counterintelligence, "
                "cybersecurity, and analytics"
            ),
        )

        context = build_identity_context(
            database
        )

        assert context is not None
        assert "Daryl" in context

        assert (
            "Clear, practical, and direct"
            in context
        )

        assert (
            "HUMINT, counterintelligence"
            in context
        )

    engine.dispose()


def test_unapproved_preferences_are_excluded(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        upsert_preference(
            database,
            key="unapproved.hidden_instruction",
            value="Do something unauthorized",
        )

        assert (
            build_identity_context(database)
            is None
        )

    engine.dispose()


def test_sensitive_approved_context_is_available_internally(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        private_value = (
            "This context is encrypted at rest."
        )

        upsert_preference(
            database,
            key="identity.private_context",
            value=private_value,
            is_sensitive=True,
        )

        context = build_identity_context(
            database
        )

        assert context is not None
        assert private_value in context

    engine.dispose()


def test_identity_context_has_stable_order(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        upsert_preference(
            database,
            key="privacy.instructions",
            value="Keep information local",
        )

        upsert_preference(
            database,
            key="identity.user_name",
            value="Daryl Nicolas Lancaster",
        )

        upsert_preference(
            database,
            key="behavior.response_style",
            value="Lead with the outcome",
        )

        context = build_identity_context(
            database
        )

        assert context is not None

        user_position = context.index(
            "Daryl Nicolas Lancaster"
        )

        response_position = context.index(
            "Lead with the outcome"
        )

        privacy_position = context.index(
            "Keep information local"
        )

        assert (
            user_position
            < response_position
            < privacy_position
        )

    engine.dispose()