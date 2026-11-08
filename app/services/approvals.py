import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.services.audit import record_audit_event
from app.services.encryption import (
    DataProtectionError,
    protect_text,
    unprotect_text,
)


VALID_APPROVAL_DECISIONS = {
    "approved",
    "rejected",
}

MAXIMUM_APPROVAL_QUERY_LIMIT = 500
MAXIMUM_APPROVAL_PAYLOAD_LENGTH = 100000
MAXIMUM_DECISION_REASON_LENGTH = 2000


class ApprovalContentError(Exception):
    pass


class ApprovalStateError(Exception):
    pass


def current_utc_time() -> datetime:
    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


def create_approval_request(
    database: Session,
    *,
    operation: str,
    resource_type: str,
    resource_id: str | None = None,
    request_payload: (
        dict[str, Any] | None
    ) = None,
) -> models.ApprovalRequest:
    cleaned_operation = operation.strip()
    cleaned_resource_type = (
        resource_type.strip()
    )

    if not cleaned_operation:
        raise ValueError(
            "Approval operation cannot be empty."
        )

    if len(cleaned_operation) > 100:
        raise ValueError(
            "Approval operation is too long."
        )

    if not cleaned_resource_type:
        raise ValueError(
            "Approval resource type cannot "
            "be empty."
        )

    if len(cleaned_resource_type) > 100:
        raise ValueError(
            "Approval resource type is too long."
        )

    cleaned_resource_id = None

    if resource_id is not None:
        cleaned_resource_id = (
            resource_id.strip() or None
        )

        if (
            cleaned_resource_id is not None
            and len(cleaned_resource_id) > 100
        ):
            raise ValueError(
                "Approval resource ID is too long."
            )

    encrypted_payload = None

    if request_payload is not None:
        serialized_payload = json.dumps(
            request_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        if (
            len(serialized_payload)
            > MAXIMUM_APPROVAL_PAYLOAD_LENGTH
        ):
            raise ValueError(
                "Approval payload is too large."
            )

        encrypted_payload = protect_text(
            serialized_payload
        )

    approval = models.ApprovalRequest(
        operation=cleaned_operation,
        resource_type=cleaned_resource_type,
        resource_id=cleaned_resource_id,
        request_payload=encrypted_payload,
        status="pending",
    )

    try:
        database.add(approval)
        database.commit()
        database.refresh(approval)
    except Exception:
        database.rollback()
        raise

    record_audit_event(
        database,
        event_type="approval",
        action="approval_requested",
        outcome="success",
        resource_type="approval_request",
        resource_id=approval.id,
    )

    return approval


def get_approval_request(
    database: Session,
    approval_id: str,
) -> models.ApprovalRequest | None:
    return database.get(
        models.ApprovalRequest,
        approval_id,
    )


def list_approval_requests(
    database: Session,
    *,
    approval_status: str | None = None,
    limit: int = 100,
) -> list[models.ApprovalRequest]:
    safe_limit = max(
        1,
        min(
            limit,
            MAXIMUM_APPROVAL_QUERY_LIMIT,
        ),
    )

    statement = select(
        models.ApprovalRequest
    )

    if approval_status is not None:
        normalized_status = (
            approval_status.strip().lower()
        )

        statement = statement.where(
            models.ApprovalRequest.status
            == normalized_status
        )

    statement = (
        statement
        .order_by(
            models.ApprovalRequest.created_at.desc(),
            models.ApprovalRequest.id.desc(),
        )
        .limit(safe_limit)
    )

    return list(
        database.scalars(statement).all()
    )


def read_approval_payload(
    approval: models.ApprovalRequest,
) -> dict[str, Any] | None:
    if approval.request_payload is None:
        return None

    try:
        serialized_payload = unprotect_text(
            approval.request_payload
        )

        payload = json.loads(
            serialized_payload
        )
    except (
        DataProtectionError,
        json.JSONDecodeError,
    ) as error:
        raise ApprovalContentError(
            "Approval payload could not be "
            "decrypted."
        ) from error

    if not isinstance(payload, dict):
        raise ApprovalContentError(
            "Approval payload is invalid."
        )

    return payload


def read_decision_reason(
    approval: models.ApprovalRequest,
) -> str | None:
    if approval.decision_reason is None:
        return None

    try:
        return unprotect_text(
            approval.decision_reason
        )
    except DataProtectionError as error:
        raise ApprovalContentError(
            "Approval decision reason could not "
            "be decrypted."
        ) from error


def decide_approval_request(
    database: Session,
    approval: models.ApprovalRequest,
    *,
    decision: str,
    reason: str | None = None,
) -> models.ApprovalRequest:
    normalized_decision = (
        decision.strip().lower()
    )

    if (
        normalized_decision
        not in VALID_APPROVAL_DECISIONS
    ):
        raise ValueError(
            "Approval decision must be "
            "approved or rejected."
        )

    if approval.status != "pending":
        raise ApprovalStateError(
            "Only pending approval requests "
            "can be decided."
        )

    encrypted_reason = None

    if reason is not None:
        cleaned_reason = reason.strip()

        if cleaned_reason:
            if (
                len(cleaned_reason)
                > MAXIMUM_DECISION_REASON_LENGTH
            ):
                raise ValueError(
                    "Approval decision reason "
                    "is too long."
                )

            encrypted_reason = protect_text(
                cleaned_reason
            )

    approval.status = normalized_decision
    approval.decision_reason = (
        encrypted_reason
    )
    approval.decided_at = current_utc_time()

    try:
        database.commit()
        database.refresh(approval)
    except Exception:
        database.rollback()
        raise

    record_audit_event(
        database,
        event_type="approval",
        action="approval_decided",
        outcome=normalized_decision,
        resource_type="approval_request",
        resource_id=approval.id,
    )

    return approval