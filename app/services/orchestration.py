from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import models
from app.connectors.intel_notes import (
    IntelNotesConnector,
)
from app.connectors.risk_tracker import (
    RiskTrackerConnector,
)
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
from app.services.identity import (
    IdentityContextError,
    build_identity_context,
)
from app.services.retrieval import (
    RetrievalResult,
    retrieve_intel_notes,
)
from app.services.risk_retrieval import (
    RiskRetrievalResult,
    retrieve_risk_tracker_case,
    retrieve_risk_tracker_cases,
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
    retrieval_result: (
        RetrievalResult | None
    ) = None
    risk_retrieval_result: (
        RiskRetrievalResult | None
    ) = None


def build_conversation_context(
    database: Session,
    *,
    conversation: models.Conversation,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    history_limit: int = (
        MAXIMUM_HISTORY_MESSAGES
    ),
    supplemental_context: str | None = None,
) -> list[ModelMessage]:
    cleaned_system_prompt = (
        system_prompt.strip()
    )

    if not cleaned_system_prompt:
        raise ValueError(
            "System prompt cannot be empty."
        )

    try:
        identity_context = (
            build_identity_context(database)
        )
    except IdentityContextError as error:
        raise ConversationOrchestrationError(
            "Persistent identity context could "
            "not be prepared."
        ) from error

    effective_system_prompt = (
        cleaned_system_prompt
    )

    if identity_context is not None:
        effective_system_prompt = (
            f"{effective_system_prompt}\n\n"
            f"{identity_context}"
        )

    if supplemental_context is not None:
        cleaned_supplemental_context = (
            supplemental_context.strip()
        )

        if cleaned_supplemental_context:
            effective_system_prompt = (
                f"{effective_system_prompt}\n\n"
                "Supplemental retrieved context "
                "for this turn follows. Current "
                "retrieved records are the evidence "
                "for this turn and take priority over "
                "conflicting claims made in earlier "
                "assistant messages. Earlier assistant "
                "messages are conversation history, "
                "not authoritative evidence. Do not "
                                "invent definitions for classification, "
                "reliability, credibility, risk, "
                "priority, status, or any other label "
                "or rating unless its definition is "
                "explicitly present in the retrieved "
                "records or provided context. Never "
                "interpret, translate, rank, compare, "
                "minimize, or intensify an undefined "
                "label. Report an undefined code, "
                "rating, priority, or status exactly as "
                "recorded and state that its meaning is "
                "not defined in the available context. "
                "A recorded value such as Moderate, "
                "Elevated, Planned, Open, or similar "
                "describes only the stored value. Do "
                "not infer urgency, severity, progress, "
                "completion, actions taken, or actions "
                "not taken solely from such a value. "
                "Any recommendation or hypothesis must "
                "be clearly labeled as analysis and "
                "must identify the specific recorded "
                "facts supporting it. Clearly "
                "distinguish recorded facts, source "
                "reporting, assessments, missing "
                "information, and your own analysis.\n"
                f"{cleaned_supplemental_context}"
            )

    stored_messages = list_messages(
        database,
        conversation_id=conversation.id,
        limit=history_limit,
    )

    model_messages = [
        ModelMessage(
            role="system",
            content=effective_system_prompt,
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
    use_intel_notes: bool = False,
    intel_notes_query: str | None = None,
    intel_notes_connector: (
        IntelNotesConnector | None
    ) = None,
    use_risk_tracker: bool = False,
    risk_tracker_case_id: int | None = None,
    risk_tracker_include_archived: bool = False,
    risk_tracker_include_related: bool = True,
    risk_tracker_connector: (
        RiskTrackerConnector | None
    ) = None,
) -> ConversationTurnResult:
    user_message = add_message(
        database,
        conversation=conversation,
        role="user",
        content=user_content,
    )

    retrieval_result: (
        RetrievalResult | None
    ) = None

    risk_retrieval_result: (
        RiskRetrievalResult | None
    ) = None

    supplemental_contexts: list[str] = []

    try:
        if (
            use_intel_notes
            and intel_notes_connector is not None
        ):
            retrieval_query = (
                intel_notes_query
                if intel_notes_query is not None
                else user_content
            )

            retrieval_result = (
                retrieve_intel_notes(
                    database,
                    connector=(
                        intel_notes_connector
                    ),
                    query=retrieval_query,
                )
            )

            if retrieval_result.context:
                supplemental_contexts.append(
                    retrieval_result.context
                )

        if (
            use_risk_tracker
            and risk_tracker_connector is not None
        ):
            if risk_tracker_case_id is None:
                risk_retrieval_result = (
                    retrieve_risk_tracker_cases(
                        database,
                        connector=(
                            risk_tracker_connector
                        ),
                        include_archived=(
                            risk_tracker_include_archived
                        ),
                    )
                )
            else:
                risk_retrieval_result = (
                    retrieve_risk_tracker_case(
                        database,
                        connector=(
                            risk_tracker_connector
                        ),
                        case_id=(
                            risk_tracker_case_id
                        ),
                        include_related=(
                            risk_tracker_include_related
                        ),
                    )
                )

            if risk_retrieval_result.context:
                supplemental_contexts.append(
                    risk_retrieval_result.context
                )

        supplemental_context = (
            "\n\n".join(
                supplemental_contexts
            )
            if supplemental_contexts
            else None
        )

        model_messages = (
            build_conversation_context(
                database,
                conversation=conversation,
                system_prompt=system_prompt,
                supplemental_context=(
                    supplemental_context
                ),
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

        intel_notes_record_count = (
            retrieval_result.record_count
            if retrieval_result is not None
            else 0
        )

        risk_case_count = (
            risk_retrieval_result.case_count
            if risk_retrieval_result is not None
            else 0
        )

        risk_related_record_count = (
            risk_retrieval_result
            .related_record_count
            if risk_retrieval_result is not None
            else 0
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
                f"{model_response.usage.output_tokens}; "
                "intel_notes_records="
                f"{intel_notes_record_count}; "
                "risk_tracker_cases="
                f"{risk_case_count}; "
                "risk_tracker_related_records="
                f"{risk_related_record_count}"
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
        retrieval_result=retrieval_result,
        risk_retrieval_result=(
            risk_retrieval_result
        ),
    )