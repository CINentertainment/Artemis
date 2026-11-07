from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import models
from app.runtime.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from app.runtime.service import ModelRuntime
from app.services.audit import record_audit_event
from app.services.conversations import (
    ConversationContentError,
    add_message,
    list_messages,
    read_message_content,
)


DEFAULT_SYSTEM_PROMPT = (
    "You are Artemis, a private, local-first "
    "intelligence assistant developed by "
    "CIN Entertainment. Provide clear, accurate, "
    "practical responses. Protect private data, "
    "distinguish facts from analysis, and do not "
    "claim capabilities or information you do "
    "not possess."
)

MAXIMUM_HISTORY_MESSAGES = 500


class ConversationOrchestrationError(
    Exception
):
    pass


@dataclass(frozen=True)
class ConversationTurnResult:
    user_message: models.Message
    assistant_message: models.Message
    model_response: ModelResponse


def build_conversation_context(
    database: Session,
    *,
    conversation: models.Conversation,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    history_limit: int = (
        MAXIMUM_HISTORY_MESSAGES
    ),
) -> list[ModelMessage]:
    cleaned_system_prompt = (
        system_prompt.strip()
    )

    if not cleaned_system_prompt:
        raise ValueError(
            "System prompt cannot be empty."
        )

    stored_messages = list_messages(
        database,
        conversation_id=conversation.id,
        limit=history_limit,
    )

    model_messages = [
        ModelMessage(
            role="system",
            content=cleaned_system_prompt,
        )
    ]

    try:
        for stored_message in stored_messages:
            model_messages.append(
                ModelMessage(
                    role=stored_message.role,
                    content=read_message_content(
                        stored_message
                    ),
                )
            )
    except ConversationContentError as error:
        raise ConversationOrchestrationError(
            "Conversation context could not "
            "be decrypted."
        ) from error

    return model_messages


def run_conversation_turn(
    database: Session,
    *,
    conversation: models.Conversation,
    user_content: str,
    runtime: ModelRuntime,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    provider_name: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
) -> ConversationTurnResult:
    user_message = add_message(
        database,
        conversation=conversation,
        role="user",
        content=user_content,
    )

    try:
        model_messages = (
            build_conversation_context(
                database,
                conversation=conversation,
                system_prompt=system_prompt,
            )
        )

        model_request = ModelRequest(
            messages=model_messages,
            model=model,
            temperature=temperature,
            max_output_tokens=(
                max_output_tokens
            ),
        )

        model_response = runtime.generate(
            model_request,
            provider_name=provider_name,
        )

        assistant_message = add_message(
            database,
            conversation=conversation,
            role="assistant",
            content=model_response.content,
            model_name=model_response.model,
        )

        record_audit_event(
            database,
            event_type="conversation",
            action="model_response_generated",
            outcome="success",
            resource_type="conversation",
            resource_id=conversation.id,
            detail=(
                f"provider={model_response.provider}; "
                f"model={model_response.model}; "
                "input_tokens="
                f"{model_response.usage.input_tokens}; "
                "output_tokens="
                f"{model_response.usage.output_tokens}"
            ),
        )
    except ConversationOrchestrationError:
        record_audit_event(
            database,
            event_type="conversation",
            action="model_response_generated",
            outcome="failure",
            resource_type="conversation",
            resource_id=conversation.id,
            detail=(
                "Conversation context could not "
                "be prepared."
            ),
        )

        raise
    except Exception as error:
        record_audit_event(
            database,
            event_type="conversation",
            action="model_response_generated",
            outcome="failure",
            resource_type="conversation",
            resource_id=conversation.id,
            detail=(
                "The configured model runtime "
                "could not generate a response."
            ),
        )

        raise ConversationOrchestrationError(
            "Artemis could not generate a "
            "response."
        ) from error

    return ConversationTurnResult(
        user_message=user_message,
        assistant_message=assistant_message,
        model_response=model_response,
    )