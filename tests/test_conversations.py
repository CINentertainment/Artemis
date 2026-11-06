from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import Settings
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.services.conversations import (
    ConversationContentError,
    add_message,
    archive_conversation,
    create_conversation,
    list_conversations,
    list_messages,
    read_conversation_title,
    read_message_content,
)
from app.services.encryption import (
    is_protected_value,
)


def build_conversation_test_engine(
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


def test_conversation_title_is_encrypted(
    tmp_path: Path,
):
    engine = build_conversation_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        conversation = create_conversation(
            database,
            title="Private research session",
        )

        assert conversation.title is not None
        assert (
            conversation.title
            != "Private research session"
        )

        assert is_protected_value(
            conversation.title
        )

        assert read_conversation_title(
            conversation
        ) == "Private research session"

    engine.dispose()


def test_message_content_is_encrypted(
    tmp_path: Path,
):
    engine = build_conversation_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        conversation = create_conversation(
            database,
            title="Encryption Test",
        )

        message = add_message(
            database,
            conversation=conversation,
            role="user",
            content="Sensitive analytical question.",
        )

        stored_message = database.scalar(
            select(models.Message).where(
                models.Message.id
                == message.id
            )
        )

        assert stored_message is not None

        assert (
            stored_message.content
            != "Sensitive analytical question."
        )

        assert is_protected_value(
            stored_message.content
        )

        assert read_message_content(
            stored_message
        ) == "Sensitive analytical question."

    engine.dispose()


def test_conversation_and_message_pagination(
    tmp_path: Path,
):
    engine = build_conversation_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        first = create_conversation(
            database,
            title="First",
        )

        create_conversation(
            database,
            title="Second",
        )

        for message_number in range(5):
            add_message(
                database,
                conversation=first,
                role="user",
                content=(
                    f"Message {message_number}"
                ),
            )

        conversations = list_conversations(
            database,
            limit=1,
        )

        messages = list_messages(
            database,
            conversation_id=first.id,
            limit=2,
            offset=2,
        )

        assert len(conversations) == 1
        assert len(messages) == 2

        assert read_message_content(
            messages[0]
        ) == "Message 2"

        assert read_message_content(
            messages[1]
        ) == "Message 3"

    engine.dispose()


def test_invalid_message_role_is_rejected(
    tmp_path: Path,
):
    engine = build_conversation_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        with pytest.raises(
            ValueError,
            match="Unsupported conversation role",
        ):
            add_message(
                database,
                conversation=conversation,
                role="intruder",
                content="Invalid role",
            )

    engine.dispose()


def test_archived_conversation_rejects_messages(
    tmp_path: Path,
):
    engine = build_conversation_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        archive_conversation(
            database,
            conversation,
        )

        with pytest.raises(
            ValueError,
            match="inactive conversation",
        ):
            add_message(
                database,
                conversation=conversation,
                role="user",
                content="This should fail.",
            )

    engine.dispose()


def test_corrupted_content_fails_safely(
    tmp_path: Path,
):
    engine = build_conversation_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        conversation = create_conversation(
            database
        )

        message = models.Message(
            conversation=conversation,
            role="user",
            content="corrupted-plaintext",
        )

        database.add(message)
        database.commit()

        with pytest.raises(
            ConversationContentError,
            match="could not be decrypted",
        ):
            read_message_content(message)

    engine.dispose()