from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.database import get_database
from app.models import Conversation, Message
from app.schemas import (
    ConversationCreate,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
)
from app.services.audit import record_audit_event
from app.services.conversations import (
    ConversationContentError,
    add_message,
    archive_conversation,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    read_conversation_title,
    read_message_content,
)


router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
)


def get_conversation_or_404(
    database: Session,
    conversation_id: UUID,
) -> Conversation:
    conversation = get_conversation(
        database,
        str(conversation_id),
    )

    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    return conversation


def build_conversation_response(
    conversation: Conversation,
) -> ConversationResponse:
    try:
        title = read_conversation_title(
            conversation
        )
    except ConversationContentError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Conversation data could not "
                "be decrypted."
            ),
        ) from error

    return ConversationResponse(
        id=conversation.id,
        title=title,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def build_message_response(
    message: Message,
) -> MessageResponse:
    try:
        content = read_message_content(
            message
        )
    except ConversationContentError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Message data could not "
                "be decrypted."
            ),
        ) from error

    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=content,
        model_name=message.model_name,
        created_at=message.created_at,
    )


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_conversation(
    payload: ConversationCreate,
    database: Session = Depends(get_database),
) -> ConversationResponse:
    conversation = create_conversation(
        database,
        title=payload.title,
    )

    record_audit_event(
        database,
        event_type="conversation",
        action="conversation_created",
        outcome="success",
        resource_type="conversation",
        resource_id=conversation.id,
    )

    return build_conversation_response(
        conversation
    )


@router.get(
    "",
    response_model=list[ConversationResponse],
)
def read_conversations(
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    database: Session = Depends(get_database),
) -> list[ConversationResponse]:
    conversations = list_conversations(
        database,
        limit=limit,
        offset=offset,
    )

    return [
        build_conversation_response(
            conversation
        )
        for conversation in conversations
    ]


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
)
def read_conversation(
    conversation_id: UUID,
    database: Session = Depends(get_database),
) -> ConversationResponse:
    conversation = get_conversation_or_404(
        database,
        conversation_id,
    )

    return build_conversation_response(
        conversation
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user_message(
    conversation_id: UUID,
    payload: MessageCreate,
    database: Session = Depends(get_database),
) -> MessageResponse:
    conversation = get_conversation_or_404(
        database,
        conversation_id,
    )

    try:
        message = add_message(
            database,
            conversation=conversation,
            role="user",
            content=payload.content,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    record_audit_event(
        database,
        event_type="conversation",
        action="message_created",
        outcome="success",
        resource_type="message",
        resource_id=str(message.id),
    )

    return build_message_response(message)


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
def read_messages(
    conversation_id: UUID,
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    database: Session = Depends(get_database),
) -> list[MessageResponse]:
    get_conversation_or_404(
        database,
        conversation_id,
    )

    messages = list_messages(
        database,
        conversation_id=str(
            conversation_id
        ),
        limit=limit,
        offset=offset,
    )

    return [
        build_message_response(message)
        for message in messages
    ]


@router.post(
    "/{conversation_id}/archive",
    response_model=ConversationResponse,
)
def archive_existing_conversation(
    conversation_id: UUID,
    database: Session = Depends(get_database),
) -> ConversationResponse:
    conversation = get_conversation_or_404(
        database,
        conversation_id,
    )

    archive_conversation(
        database,
        conversation,
    )

    record_audit_event(
        database,
        event_type="conversation",
        action="conversation_archived",
        outcome="success",
        resource_type="conversation",
        resource_id=conversation.id,
    )

    return build_conversation_response(
        conversation
    )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_conversation(
    conversation_id: UUID,
    database: Session = Depends(get_database),
) -> Response:
    conversation = get_conversation_or_404(
        database,
        conversation_id,
    )

    resource_id = conversation.id

    delete_conversation(
        database,
        conversation,
    )

    record_audit_event(
        database,
        event_type="conversation",
        action="conversation_deleted",
        outcome="success",
        resource_type="conversation",
        resource_id=resource_id,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )