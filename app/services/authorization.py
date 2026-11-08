from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.approvals import (
    get_approval_request,
)
from app.services.audit import record_audit_event
from app.services.policy import (
    OperationDisposition,
    classify_operation,
    normalize_operation,
)


class OperationAuthorizationError(Exception):
    pass


class OperationProhibitedError(
    OperationAuthorizationError
):
    pass


class OperationApprovalRequiredError(
    OperationAuthorizationError
):
    pass


class OperationApprovalInvalidError(
    OperationAuthorizationError
):
    pass


@dataclass(frozen=True)
class OperationAuthorization:
    operation: str
    resource_type: str
    resource_id: str | None
    disposition: OperationDisposition
    approval_id: str | None

    @property
    def authorized(self) -> bool:
        return True


def normalize_resource_type(
    resource_type: str,
) -> str:
    normalized_resource_type = (
        resource_type.strip().lower()
    )

    if not normalized_resource_type:
        raise ValueError(
            "Resource type cannot be empty."
        )

    if len(normalized_resource_type) > 100:
        raise ValueError(
            "Resource type is too long."
        )

    return normalized_resource_type


def normalize_resource_id(
    resource_id: str | None,
) -> str | None:
    if resource_id is None:
        return None

    normalized_resource_id = (
        resource_id.strip() or None
    )

    if (
        normalized_resource_id is not None
        and len(normalized_resource_id) > 100
    ):
        raise ValueError(
            "Resource ID is too long."
        )

    return normalized_resource_id


def record_authorization_event(
    database: Session,
    *,
    operation: str,
    outcome: str,
    resource_id: str | None,
) -> None:
    record_audit_event(
        database,
        event_type="authorization",
        action=operation,
        outcome=outcome,
        resource_type="operation",
        resource_id=resource_id,
    )


def authorize_operation(
    database: Session,
    *,
    operation: str,
    resource_type: str,
    resource_id: str | None = None,
    approval_id: str | None = None,
) -> OperationAuthorization:
    policy_decision = classify_operation(
        operation
    )

    normalized_operation = (
        policy_decision.operation
    )

    normalized_resource_type = (
        normalize_resource_type(
            resource_type
        )
    )

    normalized_resource_id = (
        normalize_resource_id(
            resource_id
        )
    )

    if policy_decision.is_prohibited:
        record_authorization_event(
            database,
            operation=normalized_operation,
            outcome="prohibited",
            resource_id=normalized_resource_id,
        )

        raise OperationProhibitedError(
            policy_decision.reason
        )

    if policy_decision.is_allowed:
        record_authorization_event(
            database,
            operation=normalized_operation,
            outcome="authorized",
            resource_id=normalized_resource_id,
        )

        return OperationAuthorization(
            operation=normalized_operation,
            resource_type=(
                normalized_resource_type
            ),
            resource_id=normalized_resource_id,
            disposition=(
                policy_decision.disposition
            ),
            approval_id=None,
        )

    if approval_id is None:
        record_authorization_event(
            database,
            operation=normalized_operation,
            outcome="approval_required",
            resource_id=normalized_resource_id,
        )

        raise OperationApprovalRequiredError(
            "This operation requires an "
            "approved approval request."
        )

    approval = get_approval_request(
        database,
        approval_id,
    )

    if approval is None:
        record_authorization_event(
            database,
            operation=normalized_operation,
            outcome="invalid_approval",
            resource_id=normalized_resource_id,
        )

        raise OperationApprovalInvalidError(
            "Approval request was not found."
        )

    approval_operation = normalize_operation(
        approval.operation
    )

    approval_resource_type = (
        normalize_resource_type(
            approval.resource_type
        )
    )

    approval_resource_id = (
        normalize_resource_id(
            approval.resource_id
        )
    )

    if (
        approval_operation
        != normalized_operation
    ):
        record_authorization_event(
            database,
            operation=normalized_operation,
            outcome="invalid_approval",
            resource_id=normalized_resource_id,
        )

        raise OperationApprovalInvalidError(
            "Approval operation does not match "
            "the requested operation."
        )

    if (
        approval_resource_type
        != normalized_resource_type
    ):
        record_authorization_event(
            database,
            operation=normalized_operation,
            outcome="invalid_approval",
            resource_id=normalized_resource_id,
        )

        raise OperationApprovalInvalidError(
            "Approval resource type does not "
            "match the requested resource."
        )

    if (
        approval_resource_id
        != normalized_resource_id
    ):
        record_authorization_event(
            database,
            operation=normalized_operation,
            outcome="invalid_approval",
            resource_id=normalized_resource_id,
        )

        raise OperationApprovalInvalidError(
            "Approval resource ID does not "
            "match the requested resource."
        )

    if approval.status != "approved":
        record_authorization_event(
            database,
            operation=normalized_operation,
            outcome="approval_not_approved",
            resource_id=normalized_resource_id,
        )

        raise OperationApprovalInvalidError(
            "Approval request has not been "
            "approved."
        )

    record_authorization_event(
        database,
        operation=normalized_operation,
        outcome="authorized",
        resource_id=normalized_resource_id,
    )

    return OperationAuthorization(
        operation=normalized_operation,
        resource_type=normalized_resource_type,
        resource_id=normalized_resource_id,
        disposition=(
            policy_decision.disposition
        ),
        approval_id=approval.id,
    )