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

from app.config import get_settings
from app.connectors.intel_notes import (
    IntelNotesConnector,
)
from app.connectors.market_lens import (
    MarketLensConnector,
)
from app.connectors.risk_tracker import (
    RiskTrackerConnector,
)
from app.database import get_database
from app.models import Conversation, Message
from app.runtime.service import (
    ModelRuntime,
    build_model_runtime,
)
from app.schemas import (
    ConversationCreate,
    ConversationResponse,
    ConversationTurnCreate,
    ConversationTurnResponse,
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
from app.services.orchestration import (
    ConversationOrchestrationError,
    run_conversation_turn,
)


router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
)


def get_model_runtime() -> ModelRuntime:
    return build_model_runtime(
        get_settings()
    )


def get_intel_notes_connector():
    connector = IntelNotesConnector(
        base_url=(
            get_settings().intel_notes_url
        )
    )

    try:
        yield connector
    finally:
        connector.close()


def get_risk_tracker_connector():
    settings = get_settings()

    password = (
        settings.risk_tracker_password
    )

    if password is None:
        yield None
        return

    password_value = (
        password.get_secret_value()
    )

    if not password_value:
        yield None
        return

    connector = RiskTrackerConnector(
        base_url=settings.risk_tracker_url,
        username=(
            settings.risk_tracker_username
        ),
        password=password_value,
        timeout_seconds=(
            settings
            .risk_tracker_timeout_seconds
        ),
    )

    try:
        yield connector
    finally:
        connector.close()


def get_market_lens_connector():
    settings = get_settings()

    api_key = settings.market_lens_api_key

    if api_key is None:
        yield None
        return

    api_key_value = (
        api_key.get_secret_value()
    )

    if not api_key_value:
        yield None
        return

    connector = MarketLensConnector(
        base_url=settings.market_lens_url,
        api_key=api_key_value,
        timeout_seconds=(
            settings
            .market_lens_timeout_seconds
        ),
    )

    try:
        yield connector
    finally:
        connector.close()


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


@router.post(
    "/{conversation_id}/respond",
    response_model=ConversationTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_conversation_response(
    conversation_id: UUID,
    payload: ConversationTurnCreate,
    database: Session = Depends(get_database),
    runtime: ModelRuntime = Depends(
        get_model_runtime
    ),
    intel_notes_connector: (
        IntelNotesConnector
    ) = Depends(
        get_intel_notes_connector
    ),
    risk_tracker_connector: (
        RiskTrackerConnector | None
    ) = Depends(
        get_risk_tracker_connector
    ),
    market_lens_connector: (
        MarketLensConnector | None
    ) = Depends(
        get_market_lens_connector
    ),
) -> ConversationTurnResponse:
    conversation = get_conversation_or_404(
        database,
        conversation_id,
    )

    if (
        payload.use_risk_tracker
        and risk_tracker_connector is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Risk Tracker credentials are "
                "not configured."
            ),
        )

    if (
        payload.use_market_lens
        and market_lens_connector is None
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Market Lens API key is not "
                "configured."
            ),
        )

    try:
        result = run_conversation_turn(
            database,
            conversation=conversation,
            user_content=payload.content,
            runtime=runtime,
            use_intel_notes=(
                payload.use_intel_notes
            ),
            intel_notes_query=(
                payload.intel_notes_query
            ),
            intel_notes_connector=(
                intel_notes_connector
            ),
            use_risk_tracker=(
                payload.use_risk_tracker
            ),
            risk_tracker_case_id=(
                payload.risk_tracker_case_id
            ),
            risk_tracker_include_archived=(
                payload
                .risk_tracker_include_archived
            ),
            risk_tracker_include_related=(
                payload
                .risk_tracker_include_related
            ),
            risk_tracker_connector=(
                risk_tracker_connector
            ),
            use_market_lens=(
                payload.use_market_lens
            ),
            market_lens_asset_id=(
                payload.market_lens_asset_id
            ),
            market_lens_include_inactive=(
                payload
                .market_lens_include_inactive
            ),
            market_lens_include_prices=(
                payload
                .market_lens_include_prices
            ),
            market_lens_include_analytics=(
                payload
                .market_lens_include_analytics
            ),
            market_lens_include_signals=(
                payload
                .market_lens_include_signals
            ),
            market_lens_start_date=(
                payload.market_lens_start_date
            ),
            market_lens_end_date=(
                payload.market_lens_end_date
            ),
            market_lens_connector=(
                market_lens_connector
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ConversationOrchestrationError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Artemis could not generate a "
                "response."
            ),
        ) from error

    usage = result.model_response.usage

    return ConversationTurnResponse(
        user_message=build_message_response(
            result.user_message
        ),
        assistant_message=build_message_response(
            result.assistant_message
        ),
        provider=result.model_response.provider,
        model=result.model_response.model,
        finish_reason=(
            result.model_response.finish_reason
        ),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )