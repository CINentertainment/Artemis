from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.services.approvals import (
    ApprovalStateError,
    create_approval_request,
    decide_approval_request,
    list_approval_requests,
    read_approval_payload,
    read_decision_reason,
)
from app.services.encryption import (
    is_protected_value,
)


def build_test_engine(
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


def test_create_approval_encrypts_payload(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        private_payload = {
            "symbol": "AAPL",
            "rationale": (
                "Private analytical rationale"
            ),
        }

        approval = create_approval_request(
            database,
            operation="market_signal.create",
            resource_type="market_signal",
            resource_id="AAPL",
            request_payload=private_payload,
        )

        assert approval.status == "pending"

        assert approval.request_payload is not None

        assert is_protected_value(
            approval.request_payload
        )

        assert (
            "Private analytical rationale"
            not in approval.request_payload
        )

        assert read_approval_payload(
            approval
        ) == private_payload

    engine.dispose()


def test_create_approval_without_payload(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="service.refresh",
            resource_type="service",
        )

        assert approval.request_payload is None

        assert (
            read_approval_payload(approval)
            is None
        )

    engine.dispose()


def test_approve_pending_request(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="note.create",
            resource_type="intelligence_note",
        )

        decided = decide_approval_request(
            database,
            approval,
            decision="approved",
            reason="Reviewed and authorized",
        )

        assert decided.status == "approved"
        assert decided.decided_at is not None

        assert decided.decision_reason is not None

        assert is_protected_value(
            decided.decision_reason
        )

        assert read_decision_reason(
            decided
        ) == "Reviewed and authorized"

    engine.dispose()


def test_reject_pending_request(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="case.update",
            resource_type="risk_case",
        )

        decided = decide_approval_request(
            database,
            approval,
            decision="rejected",
        )

        assert decided.status == "rejected"

        assert (
            read_decision_reason(decided)
            is None
        )

    engine.dispose()


def test_decided_request_cannot_be_decided_again(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="asset.delete",
            resource_type="market_asset",
        )

        decide_approval_request(
            database,
            approval,
            decision="rejected",
        )

        with pytest.raises(
            ApprovalStateError,
            match="Only pending",
        ):
            decide_approval_request(
                database,
                approval,
                decision="approved",
            )

    engine.dispose()


def test_invalid_decision_is_rejected(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        approval = create_approval_request(
            database,
            operation="note.create",
            resource_type="intelligence_note",
        )

        with pytest.raises(
            ValueError,
            match="approved or rejected",
        ):
            decide_approval_request(
                database,
                approval,
                decision="maybe",
            )

    engine.dispose()


def test_list_approvals_filters_status(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        pending = create_approval_request(
            database,
            operation="note.create",
            resource_type="intelligence_note",
        )

        rejected = create_approval_request(
            database,
            operation="case.update",
            resource_type="risk_case",
        )

        decide_approval_request(
            database,
            rejected,
            decision="rejected",
        )

        pending_results = (
            list_approval_requests(
                database,
                approval_status="pending",
            )
        )

        rejected_results = (
            list_approval_requests(
                database,
                approval_status="rejected",
            )
        )

        assert [
            item.id
            for item in pending_results
        ] == [pending.id]

        assert [
            item.id
            for item in rejected_results
        ] == [rejected.id]

    engine.dispose()