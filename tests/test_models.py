from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app import models
from app.config import Settings
from app.database import (
    Base,
    create_database_engine,
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

    Base.metadata.create_all(
        bind=engine
    )

    return engine


def test_foundational_tables_are_created(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    table_names = set(
        inspect(engine).get_table_names()
    )

    assert table_names == {
        "approval_requests",
        "audit_events",
        "conversations",
        "messages",
        "user_preferences",
    }

    engine.dispose()


def test_user_preference_persists(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        preference = models.UserPreference(
            key="communication_style",
            value="clear and practical",
        )

        database.add(preference)
        database.commit()
        database.refresh(preference)

        assert preference.id is not None
        assert preference.value_type == "text"
        assert preference.is_sensitive is False

    engine.dispose()


def test_conversation_contains_messages(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = models.Conversation(
            title="Foundation Test",
        )

        conversation.messages.append(
            models.Message(
                role="user",
                content="Hello, Artemis.",
            )
        )

        conversation.messages.append(
            models.Message(
                role="assistant",
                content="Hello, Daryl.",
                model_name="test-model",
            )
        )

        database.add(conversation)
        database.commit()
        database.refresh(conversation)

        assert conversation.id is not None
        assert len(conversation.messages) == 2

        assert (
            conversation.messages[0].role
            == "user"
        )

        assert (
            conversation.messages[1].role
            == "assistant"
        )

    engine.dispose()