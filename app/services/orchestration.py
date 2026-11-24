import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import models
from app.connectors.intel_notes import (
    IntelNotesConnector,
)
from app.connectors.market_lens import (
    MarketLensConnector,
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
from app.services.information_record_retrieval import (
    retrieve_information_record_context,
)
from app.services.market_retrieval import (
    MarketRetrievalResult,
    retrieve_market_lens_asset,
    retrieve_market_lens_assets,
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
    "not possess. "
    "Artemis has a configured on-demand web research workflow, including "
    "source collection and event-note saving when successful. Explicit research "
    "requests are handled by the application before normal model generation. "
    "Do not claim that Artemis universally lacks internet or current-information "
    "access merely because this model turn has no web evidence. For current "
    "facts, explain when research has not been performed rather than inventing "
    "an update. Suggest a direct 'Research TOPIC' request when fresh collection "
    "is needed. Do not claim a note was saved, a reminder was set, or continuous "
    "monitoring started without a successful application result."
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
    market_retrieval_result: (
        MarketRetrievalResult | None
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
                "priority, status, market signals, "
                "trends, or any other label or rating "
                "unless its definition is explicitly "
                "present in the retrieved records or "
                "provided context. Never interpret, "
                "translate, rank, compare, minimize, "
                "or intensify an undefined label. "
                "Report an undefined code, rating, "
                "priority, status, signal, or trend "
                "exactly as recorded and state that "
                "its meaning is not defined in the "
                "available context. A recorded value "
                "such as Moderate, Elevated, Planned, "
                "Open, Watch, Upward, or similar "
                "describes only the stored value. Do "
                "not infer urgency, severity, progress, "
                "completion, future performance, "
                "actions taken, or actions not taken "
                "solely from such a value. Market "
                "prices, analytics, signals, trends, "
                "and historical values may be delayed, "
                "stale, incomplete, or inaccurate and "
                "must not be presented as guaranteed "
                "future performance or personalized "
                "financial advice. Do not claim that "
                "Artemis executed a trade, changed a "
                "portfolio, synchronized market data, "
                "or performed any write operation. "
                "Any recommendation or hypothesis must "
                "be clearly labeled as analysis and "
                "must identify the specific recorded "
                "facts supporting it. Clearly "
                "distinguish recorded facts, source "
                "reporting, calculated analytics, "
                "configured signals, assessments, "
                "missing information, and your own "
                "analysis.\n"
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

def build_grounded_assistant_content(
    *,
    model_content: str,
    market_retrieval_result: (
        MarketRetrievalResult | None
    ),
) -> str:
    cleaned_model_content = (
        model_content.strip()
    )

    market_facts = (
        market_retrieval_result.facts
        if market_retrieval_result
        is not None
        else None
    )

    if not market_facts:
        return cleaned_model_content

    cleaned_market_facts = (
        market_facts.strip()
    )

    if not cleaned_model_content:
        return cleaned_market_facts

    return (
        f"{cleaned_market_facts}\n\n"
        "**Model Analysis (inference):**\n"
        f"{cleaned_model_content}"
    )
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
    information_records_query: str | None = None,
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
    use_market_lens: bool = False,
    market_lens_asset_id: int | None = None,
    market_lens_include_inactive: bool = False,
    market_lens_include_prices: bool = True,
    market_lens_include_analytics: bool = True,
    market_lens_include_signals: bool = True,
    market_lens_start_date: str | None = None,
    market_lens_end_date: str | None = None,
    market_lens_connector: (
        MarketLensConnector | None
    ) = None,
) -> ConversationTurnResult:
    user_message = add_message(
        database,
        conversation=conversation,
        role="user",
        content=user_content,
    )

    from app.services.local_commands import run_local_command

    local_content = run_local_command(database, user_content)

    if local_content is not None:
        local_response = ModelResponse(
            provider="local-computer",
            model="local-command-router",
            content=local_content,
        )

        assistant_message = add_message(
            database,
            conversation=conversation,
            role="assistant",
            content=local_content,
            model_name=local_response.model,
        )

        record_audit_event(
            database,
            event_type="conversation",
            action="local_command_handled",
            outcome="success",
            resource_type="conversation",
            resource_id=conversation.id,
            detail="Local command handled; see the response and action execution record for its outcome.",
        )

        return ConversationTurnResult(
            user_message=user_message,
            assistant_message=assistant_message,
            model_response=local_response,
        )

    retrieval_result: (
        RetrievalResult | None
    ) = None

    risk_retrieval_result: (
        RiskRetrievalResult | None
    ) = None

    market_retrieval_result: (
        MarketRetrievalResult | None
    ) = None

    supplemental_contexts: list[str] = []

    try:
        if information_records_query is not None:
            supplemental_contexts.append(
                retrieve_information_record_context(
                    database,
                    query=information_records_query,
                )
            )

        if (
            use_intel_notes
            and intel_notes_connector is not None
        ):
            retrieval_query = (intel_notes_query or "").strip()
            if not retrieval_query:
                message = user_content.strip()
                brief_request = re.fullmatch(
                    r"brief\s+me\s+on\s+(.+?)[?!.]*",
                    message,
                    flags=re.IGNORECASE,
                )
                retrieval_query = (
                    brief_request.group(1).strip()
                    if brief_request
                    else message
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

        if (
            use_market_lens
            and market_lens_connector is not None
        ):
            if market_lens_asset_id is None:
                market_retrieval_result = (
                    retrieve_market_lens_assets(
                        database,
                        connector=(
                            market_lens_connector
                        ),
                        include_inactive=(
                            market_lens_include_inactive
                        ),
                    )
                )
            else:
                market_retrieval_result = (
                    retrieve_market_lens_asset(
                        database,
                        connector=(
                            market_lens_connector
                        ),
                        asset_id=(
                            market_lens_asset_id
                        ),
                        include_prices=(
                            market_lens_include_prices
                        ),
                        include_analytics=(
                            market_lens_include_analytics
                        ),
                        include_signals=(
                            market_lens_include_signals
                        ),
                        start_date=(
                            market_lens_start_date
                        ),
                        end_date=(
                            market_lens_end_date
                        ),
                    )
                )

            if market_retrieval_result.context:
                supplemental_contexts.append(
                    (
                        "The application will add a "
                        "deterministic Retrieved Facts "
                        "section to the final response. "
                        "Provide analysis only. Do not "
                        "restate, rewrite, round, label, "
                        "or attach new interpretations "
                        "to exact asset fields, record "
                        "counts, dates, prices, calculated "
                        "analytics, or signal counts. Do "
                        "not contradict the authoritative "
                        "retrieved facts. A zero signal "
                        "count means only that no signals "
                        "were retrieved; it does not prove "
                        "that recommendations do or do not "
                        "exist. Do not assign qualitative "
                        "labels such as low, moderate, or "
                        "high unless the retrieved context "
                        "explicitly defines that scale.\n\n"
                        f"{market_retrieval_result.context}"
                    )
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

        assistant_content = (
            build_grounded_assistant_content(
                model_content=(
                    model_response.content
                ),
                market_retrieval_result=(
                    market_retrieval_result
                ),
            )
        )

        assistant_message = add_message(
            database,
            conversation=conversation,
            role="assistant",
            content=assistant_content,
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

        market_asset_count = (
            market_retrieval_result.asset_count
            if market_retrieval_result is not None
            else 0
        )

        market_price_count = (
            market_retrieval_result.price_count
            if market_retrieval_result is not None
            else 0
        )

        market_signal_count = (
            market_retrieval_result.signal_count
            if market_retrieval_result is not None
            else 0
        )

        market_analytics_count = (
            market_retrieval_result.analytics_count
            if market_retrieval_result is not None
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
                f"{risk_related_record_count}; "
                "market_lens_assets="
                f"{market_asset_count}; "
                "market_lens_prices="
                f"{market_price_count}; "
                "market_lens_signals="
                f"{market_signal_count}; "
                "market_lens_analytics="
                f"{market_analytics_count}"
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
        market_retrieval_result=(
            market_retrieval_result
        ),
    )
