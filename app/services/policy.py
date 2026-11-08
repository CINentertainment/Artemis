from dataclasses import dataclass
from typing import Literal


OperationDisposition = Literal[
    "allowed",
    "approval_required",
    "prohibited",
]


READ_ONLY_ACTIONS = {
    "get",
    "health",
    "list",
    "read",
    "search",
    "status",
    "summarize",
    "analyze",
    "analytics",
    "preview",
}

APPROVAL_REQUIRED_ACTIONS = {
    "archive",
    "create",
    "delete",
    "execute",
    "export",
    "generate",
    "publish",
    "send",
    "update",
    "write",
}

PROHIBITED_OPERATIONS = {
    "artemis.disable_security",
    "artemis.disable_audit",
    "artemis.disable_encryption",
    "artemis.expose_network",
    "credentials.export",
    "credentials.read_plaintext",
    "database.drop",
    "database.export_unencrypted",
    "security.disable",
    "shell.execute_arbitrary",
}

PROHIBITED_FRAGMENTS = {
    "bypass_approval",
    "disable_approval",
    "disable_audit",
    "disable_encryption",
    "disable_security",
    "drop_database",
    "export_credentials",
    "expose_network",
    "read_plaintext_credentials",
}


@dataclass(frozen=True)
class OperationPolicyDecision:
    operation: str
    disposition: OperationDisposition
    reason: str

    @property
    def is_allowed(self) -> bool:
        return self.disposition == "allowed"

    @property
    def requires_approval(self) -> bool:
        return (
            self.disposition
            == "approval_required"
        )

    @property
    def is_prohibited(self) -> bool:
        return self.disposition == "prohibited"


def normalize_operation(
    operation: str,
) -> str:
    normalized_operation = (
        operation.strip().lower()
    )

    if not normalized_operation:
        raise ValueError(
            "Operation cannot be empty."
        )

    if len(normalized_operation) > 100:
        raise ValueError(
            "Operation is too long."
        )

    return normalized_operation


def read_action_name(
    operation: str,
) -> str:
    return operation.rsplit(".", 1)[-1]


def classify_operation(
    operation: str,
) -> OperationPolicyDecision:
    normalized_operation = normalize_operation(
        operation
    )

    if (
        normalized_operation
        in PROHIBITED_OPERATIONS
    ):
        return OperationPolicyDecision(
            operation=normalized_operation,
            disposition="prohibited",
            reason=(
                "This operation is prohibited by "
                "the Artemis safety policy."
            ),
        )

    if any(
        fragment in normalized_operation
        for fragment in PROHIBITED_FRAGMENTS
    ):
        return OperationPolicyDecision(
            operation=normalized_operation,
            disposition="prohibited",
            reason=(
                "This operation contains a "
                "prohibited capability."
            ),
        )

    action_name = read_action_name(
        normalized_operation
    )

    if action_name in READ_ONLY_ACTIONS:
        return OperationPolicyDecision(
            operation=normalized_operation,
            disposition="allowed",
            reason=(
                "This is a non-destructive, "
                "read-only operation."
            ),
        )

    if (
        action_name
        in APPROVAL_REQUIRED_ACTIONS
    ):
        return OperationPolicyDecision(
            operation=normalized_operation,
            disposition="approval_required",
            reason=(
                "This operation may create, "
                "change, export, or transmit data."
            ),
        )

    return OperationPolicyDecision(
        operation=normalized_operation,
        disposition="approval_required",
        reason=(
            "Unknown operations require explicit "
            "approval by default."
        ),
    )