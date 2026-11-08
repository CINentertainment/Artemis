from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import Settings
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.services.approvals import (
    create_approval_request,
    decide_approval_request,
)
from app.services.authorization import (
    OperationApprovalInvalidError,
    OperationApprovalRequiredError,
    OperationProhibitedError,
    authorize_operation,
)


def build_authorization_test_engine(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    initialize_database(engine)

    return engine


def test_read_only_operation_is_authorized(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        authorization = authorize_operation(
            database,
            operation="intel_notes.search",
            resource_type="intelligence_note",
        )

        assert authorization.authorized is True
        assert (
            authorization.operation
            == "intel_notes.search"
        )

        assert authorization.approval_id is None

    engine.dispose()


def test_consequential_operation_requires_approval(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        with pytest.raises(
            OperationApprovalRequiredError,
            match="requires an approved",
        ):
            authorize_operation(
                database,
                operation="intel_notes.create",
                resource_type="intelligence_note",
            )

    engine.dispose()


def test_pending_approval_is_rejected(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="intel_notes.create",
            resource_type="intelligence_note",
        )

        with pytest.raises(
            OperationApprovalInvalidError,
            match="has not been approved",
        ):
            authorize_operation(
                database,
                operation="intel_notes.create",
                resource_type="intelligence_note",
                approval_id=approval.id,
            )

    engine.dispose()


def test_rejected_approval_is_rejected(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="intel_notes.create",
            resource_type="intelligence_note",
        )

        decide_approval_request(
            database,
            approval,
            decision="rejected",
        )

        with pytest.raises(
            OperationApprovalInvalidError,
            match="has not been approved",
        ):
            authorize_operation(
                database,
                operation="intel_notes.create",
                resource_type="intelligence_note",
                approval_id=approval.id,
            )

    engine.dispose()


def test_matching_approved_request_authorizes_operation(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="market_lens.export",
            resource_type="market_report",
            resource_id="weekly-summary",
        )

        decide_approval_request(
            database,
            approval,
            decision="approved",
            reason="Authorized for local export.",
        )

        authorization = authorize_operation(
            database,
            operation="market_lens.export",
            resource_type="market_report",
            resource_id="weekly-summary",
            approval_id=approval.id,
        )

        assert authorization.authorized is True

        assert (
            authorization.approval_id
            == approval.id
        )

        assert (
            authorization.disposition
            == "approval_required"
        )

    engine.dispose()


@pytest.mark.parametrize(
    (
        "requested_operation",
        "requested_resource_type",
        "requested_resource_id",
        "expected_message",
    ),
    [
        (
            "intel_notes.delete",
            "intelligence_note",
            "note-1",
            "operation does not match",
        ),
        (
            "intel_notes.update",
            "different_resource",
            "note-1",
            "resource type does not match",
        ),
        (
            "intel_notes.update",
            "intelligence_note",
            "note-2",
            "resource ID does not match",
        ),
    ],
)
def test_mismatched_approval_is_rejected(
    tmp_path: Path,
    requested_operation: str,
    requested_resource_type: str,
    requested_resource_id: str,
    expected_message: str,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="intel_notes.update",
            resource_type="intelligence_note",
            resource_id="note-1",
        )

        decide_approval_request(
            database,
            approval,
            decision="approved",
        )

        with pytest.raises(
            OperationApprovalInvalidError,
            match=expected_message,
        ):
            authorize_operation(
                database,
                operation=requested_operation,
                resource_type=(
                    requested_resource_type
                ),
                resource_id=(
                    requested_resource_id
                ),
                approval_id=approval.id,
            )

    engine.dispose()


def test_missing_approval_is_rejected(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        with pytest.raises(
            OperationApprovalInvalidError,
            match="not found",
        ):
            authorize_operation(
                database,
                operation="risk_tracker.update",
                resource_type="risk_case",
                resource_id="case-1",
                approval_id=(
                    "00000000-0000-0000-0000-"
                    "000000000000"
                ),
            )

    engine.dispose()


def test_prohibited_operation_cannot_be_approved(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="artemis.disable_security",
            resource_type="security_control",
        )

        decide_approval_request(
            database,
            approval,
            decision="approved",
        )

        with pytest.raises(
            OperationProhibitedError,
            match="prohibited",
        ):
            authorize_operation(
                database,
                operation="artemis.disable_security",
                resource_type="security_control",
                approval_id=approval.id,
            )

    engine.dispose()


def test_unknown_operation_fails_closed(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        with pytest.raises(
            OperationApprovalRequiredError,
        ):
            authorize_operation(
                database,
                operation=(
                    "future_tool.experimental"
                ),
                resource_type="future_resource",
            )

    engine.dispose()


def test_authorization_events_do_not_store_details(
    tmp_path: Path,
):
    engine = build_authorization_test_engine(
        tmp_path
    )

    with Session(engine) as database:
        authorize_operation(
            database,
            operation="market_lens.search",
            resource_type="market_asset",
            resource_id="AAPL",
        )

        events = database.scalars(
            select(models.AuditEvent).where(
                models.AuditEvent.event_type
                == "authorization"
            )
        ).all()

        assert len(events) == 1
        assert events[0].outcome == "authorized"
        assert events[0].detail is None

    engine.dispose()