from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.services.encryption import (
    DataProtectionError,
    protect_text,
    unprotect_text,
)


VALID_MESSAGE_ROLES = {
    "system",
    "user",
    "assistant",
    "tool",
}

MAXIMUM_MESSAGE_LENGTH = 100000
MAXIMUM_MESSAGE_QUERY_LIMIT = 500
MAXIMUM_CONVERSATION_QUERY_LIMIT = 200


class ConversationContentError(Exception):
    pass


def current_utc_time() -> datetime:
    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


def create_conversation(
    database: Session,
    *,
    title: str | None = None,
) -> models.Conversation:
    encrypted_title = None

    if title is not None:
        cleaned_title = title.strip()

        if cleaned_title:
            encrypted_title = protect_text(
                cleaned_title
            )

    conversation = models.Conversation(
        title=encrypted_title,
        status="active",
    )

    try:
        database.add(conversation)
        database.commit()
        database.refresh(conversation)
    except Exception:
        database.rollback()
        raise

    return conversation


def get_conversation(
    database: Session,
    conversation_id: str,
) -> models.Conversation | None:
    return database.get(
        models.Conversation,
        conversation_id,
    )


def list_conversations(
    database: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[models.Conversation]:
    safe_limit = max(
        1,
        min(
            limit,
            MAXIMUM_CONVERSATION_QUERY_LIMIT,
        ),
    )

    safe_offset = max(0, offset)

    statement = (
        select(models.Conversation)
        .order_by(
            models.Conversation.updated_at.desc(),
            models.Conversation.created_at.desc(),
        )
        .limit(safe_limit)
        .offset(safe_offset)
    )

    return list(
        database.scalars(statement).all()
    )


def read_conversation_title(
    conversation: models.Conversation,
) -> str | None:
    if conversation.title is None:
        return None

    try:
        return unprotect_text(
            conversation.title
        )
    except DataProtectionError as error:
        raise ConversationContentError(
            "The conversation title could not "
            "be decrypted."
        ) from error


def add_message(
    database: Session,
    *,
    conversation: models.Conversation,
    role: str,
    content: str,
    model_name: str | None = None,
) -> models.Message:
    normalized_role = role.strip().lower()

    if normalized_role not in VALID_MESSAGE_ROLES:
        raise ValueError(
            "Unsupported conversation role."
        )

    if conversation.status != "active":
        raise ValueError(
            "Messages cannot be added to an "
            "inactive conversation."
        )

    if not content:
        raise ValueError(
            "Message content cannot be empty."
        )

    if len(content) > MAXIMUM_MESSAGE_LENGTH:
        raise ValueError(
            "Message content exceeds the "
            "maximum length."
        )

    encrypted_content = protect_text(
        content
    )

    message = models.Message(
        conversation=conversation,
        role=normalized_role,
        content=encrypted_content,
        model_name=model_name,
    )

    conversation.updated_at = (
        current_utc_time()
    )

    try:
        database.add(message)
        database.commit()
        database.refresh(message)
    except Exception:
        database.rollback()
        raise

    return message


def list_messages(
    database: Session,
    *,
    conversation_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[models.Message]:
    safe_limit = max(
        1,
        min(
            limit,
            MAXIMUM_MESSAGE_QUERY_LIMIT,
        ),
    )

    safe_offset = max(0, offset)

    statement = (
        select(models.Message)
        .where(
            models.Message.conversation_id
            == conversation_id
        )
        .order_by(
            models.Message.created_at.asc(),
            models.Message.id.asc(),
        )
        .limit(safe_limit)
        .offset(safe_offset)
    )

    return list(
        database.scalars(statement).all()
    )


def read_message_content(
    message: models.Message,
) -> str:
    try:
        return unprotect_text(
            message.content
        )
    except DataProtectionError as error:
        raise ConversationContentError(
            "The message content could not "
            "be decrypted."
        ) from error


def archive_conversation(
    database: Session,
    conversation: models.Conversation,
) -> models.Conversation:
    conversation.status = "archived"
    conversation.updated_at = current_utc_time()

    try:
        database.commit()
        database.refresh(conversation)
    except Exception:
        database.rollback()
        raise

    return conversation


def delete_conversation(
    database: Session,
    conversation: models.Conversation,
) -> None:
    try:
        database.delete(conversation)
        database.commit()
    except Exception:
        database.rollback()
        raise