from typing import Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.orm import Session

from app.database import get_database
from app.models import ApprovalRequest
from app.schemas import (
    ApprovalDecisionCreate,
    ApprovalRequestCreate,
    ApprovalResponse,
)
from app.services.approvals import (
    ApprovalContentError,
    ApprovalStateError,
    create_approval_request,
    decide_approval_request,
    get_approval_request,
    list_approval_requests,
    read_approval_payload,
    read_decision_reason,
)


router = APIRouter(
    prefix="/approvals",
    tags=["approvals"],
)


def get_approval_or_404(
    database: Session,
    approval_id: UUID,
) -> ApprovalRequest:
    approval = get_approval_request(
        database,
        str(approval_id),
    )

    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )

    return approval


def build_approval_response(
    approval: ApprovalRequest,
) -> ApprovalResponse:
    try:
        request_payload = read_approval_payload(
            approval
        )

        decision_reason = read_decision_reason(
            approval
        )
    except ApprovalContentError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Approval data could not be "
                "decrypted."
            ),
        ) from error

    return ApprovalResponse(
        id=approval.id,
        operation=approval.operation,
        resource_type=approval.resource_type,
        resource_id=approval.resource_id,
        request_payload=request_payload,
        status=approval.status,
        decision_reason=decision_reason,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
    )


@router.post(
    "",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_approval(
    payload: ApprovalRequestCreate,
    database: Session = Depends(get_database),
) -> ApprovalResponse:
    try:
        approval = create_approval_request(
            database,
            operation=payload.operation,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            request_payload=(
                payload.request_payload
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(error),
        ) from error

    return build_approval_response(approval)


@router.get(
    "",
    response_model=list[ApprovalResponse],
)
def read_approvals(
    approval_status: Literal[
        "pending",
        "approved",
        "rejected",
    ]
    | None = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
    database: Session = Depends(get_database),
) -> list[ApprovalResponse]:
    approvals = list_approval_requests(
        database,
        approval_status=approval_status,
        limit=limit,
    )

    return [
        build_approval_response(approval)
        for approval in approvals
    ]


@router.get(
    "/{approval_id}",
    response_model=ApprovalResponse,
)
def read_approval(
    approval_id: UUID,
    database: Session = Depends(get_database),
) -> ApprovalResponse:
    approval = get_approval_or_404(
        database,
        approval_id,
    )

    return build_approval_response(approval)


@router.post(
    "/{approval_id}/decision",
    response_model=ApprovalResponse,
)
def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionCreate,
    database: Session = Depends(get_database),
) -> ApprovalResponse:
    approval = get_approval_or_404(
        database,
        approval_id,
    )

    try:
        decided_approval = (
            decide_approval_request(
                database,
                approval,
                decision=payload.decision,
                reason=payload.reason,
            )
        )
    except ApprovalStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(error),
        ) from error

    return build_approval_response(
        decided_approval
    )