from sqlalchemy.orm import Session

from app.services.preferences import (
    PreferenceContentError,
    list_preferences,
    read_preference_value,
)


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


def build_identity_context(
    database: Session,
) -> str | None:
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
        return None

    context = "\n".join(
        [
            (
                "Approved persistent Artemis "
                "context:"
            ),
            *context_lines,
            (
                "Use this context only when "
                "relevant. Do not unnecessarily "
                "repeat or expose private details."
            ),
        ]
    )

    return context[
        :MAXIMUM_IDENTITY_CONTEXT_LENGTH
    ]