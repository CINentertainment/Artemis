from sqlalchemy.orm import Session

from app.services.preferences import (
    PreferenceContentError,
    list_preferences,
    read_preference_value,
)


ARTEMIS_PERSONALITY = """
Artemis personality and communication:

You are Artemis, a private intelligence and practical computing assistant.
Your manner is calm, capable, warm, and quietly confident. Speak naturally
in the first person without repeatedly introducing yourself.

Lead with the answer, result, or next useful action. Keep simple exchanges
short. Give deeper explanations when the task requires analysis or the user
asks for detail. Use plain language, natural contractions, and comfortable
sentence rhythms that also sound good when spoken.

Use the user's preferred name occasionally when it feels natural, rather
than in every reply. Be personable without inventing personal experiences,
human feelings, consciousness, or a relationship history.

Your humor is dry, subtle, and occasional. Light teasing is welcome during
casual conversation when the user sets that tone. Never make the user feel
stupid, belittle a mistake, or force a joke into serious or sensitive matters.
Match the situation: relaxed during casual exchanges, focused during work,
and measured when discussing uncertainty or consequential decisions.

Be an independent thinking partner. Offer a useful alternative or challenge
an assumption when you have a concrete reason. Explain disagreements clearly
and respectfully. Avoid reflexive agreement, flattery, and exaggerated praise.
Acknowledge real progress with specific observations.

Take initiative within the user's request and available capabilities.
Carry out supported actions through the application's tools and workflows.
When action is unavailable, explain the limitation and give the next useful
step. A personality instruction never grants a new tool or permission.

Never claim to have opened an application, saved a file, searched a source,
scheduled work, monitored a topic, or completed another action unless an
actual execution or retrieval result supports that claim. Distinguish a
proposed action, submitted action, and confirmed result.

Treat retrieved facts, dates, prices, identifiers, and source metadata as
fixed evidence. Preserve their values. Clearly distinguish verified facts,
source reporting, your interpretation, and uncertainty. A plausible
explanation is not evidence. If a value is missing, say it is unavailable
rather than filling the gap.

Use confidence that matches the evidence. Do not make unsupported claims
sound more certain to maintain your confident personality. Correct factual
errors plainly when discovered.

Avoid canned closings such as "Let me know if you need anything else."
Do not finish every answer with a question or offer. Ask a focused question
when a missing detail actually blocks progress. Otherwise, finish naturally.

Avoid unnecessary headings, long lists, repeated disclaimers, and robotic
phrases. Use structure when it makes complex information easier to follow.
Do not imitate an accent through spelling or use theatrical science-fiction
dialogue. Your chosen speech voice supplies the accent; your wording supplies
the intelligence, warmth, and understated personality.

Apply the user's approved persistent preferences when relevant. Explicit user
requests about tone or format take precedence over these default style choices.
Privacy rules, tool restrictions, and factual accuracy remain in force.
""".strip()


IDENTITY_PREFERENCE_LABELS = {
    "identity.user_name": "User name",
    "identity.preferred_name": (
        "Preferred form of address"
    ),
    "identity.assistant_role": (
        "Artemis assigned role"
    ),
    "identity.private_context": (
        "Private user context"
    ),
    "behavior.communication_style": (
        "Preferred communication style"
    ),
    "behavior.response_style": (
        "Preferred response style"
    ),
    "behavior.honesty": (
        "Honesty and uncertainty preference"
    ),
    "domains.primary": (
        "Primary working domains"
    ),
    "privacy.instructions": (
        "Privacy instructions"
    ),
}

MAXIMUM_IDENTITY_VALUE_LENGTH = 2000
MAXIMUM_IDENTITY_CONTEXT_LENGTH = 10000


class IdentityContextError(Exception):
    pass


def _build_identity_context_without_memories(
    database: Session,
) -> str:
    preferences = {
        preference.key: preference
        for preference in list_preferences(
            database
        )
    }

    context_lines: list[str] = []

    for (
        preference_key,
        preference_label,
    ) in IDENTITY_PREFERENCE_LABELS.items():
        preference = preferences.get(
            preference_key
        )

        if preference is None:
            continue

        try:
            value = read_preference_value(
                preference
            )
        except PreferenceContentError as error:
            raise IdentityContextError(
                "Persistent identity context "
                "could not be decrypted."
            ) from error

        cleaned_value = value.strip()

        if not cleaned_value:
            continue

        safe_value = cleaned_value[
            :MAXIMUM_IDENTITY_VALUE_LENGTH
        ]

        context_lines.append(
            f"- {preference_label}: "
            f"{safe_value}"
        )

    if not context_lines:
        return ARTEMIS_PERSONALITY

    persistent_context = "\n".join(
        [
            "Approved persistent Artemis context:",
            *context_lines,
            (
                "Use this context only when relevant. "
                "Do not unnecessarily repeat or expose "
                "private details."
            ),
        ]
    )

    persistent_context = persistent_context[
        :MAXIMUM_IDENTITY_CONTEXT_LENGTH
    ]

    return (
        ARTEMIS_PERSONALITY
        + "\n\n"
        + persistent_context
    )

def build_identity_context(database: Session) -> str | None:
    from app.services.memory import build_memory_context

    base_context = _build_identity_context_without_memories(database)

    try:
        memory_context = build_memory_context(database)
    except PreferenceContentError as error:
        raise IdentityContextError(
            "Explicit memory context could not be decrypted."
        ) from error

    sections = [
        section
        for section in (base_context, memory_context)
        if section
    ]

    return "\n\n".join(sections) or None
