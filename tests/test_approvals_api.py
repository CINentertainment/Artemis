import pytest
from fastapi.testclient import TestClient
from sqlalchemy import (
    create_engine,
    delete,
    select,
)
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base, get_database
from app.main import app
from app.services.encryption import (
    is_protected_value,
)


test_engine = create_engine(
    "sqlite://",
    connect_args={
        "check_same_thread": False,
    },
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)

Base.metadata.create_all(bind=test_engine)

client = TestClient(app)


def override_get_database():
    database = TestingSessionLocal()

    try:
        yield database
    finally:
        database.close()


@pytest.fixture(autouse=True)
def isolated_approval_database():
    app.dependency_overrides[
        get_database
    ] = override_get_database

    with Session(test_engine) as database:
        database.execute(
            delete(models.AuditEvent)
        )

        database.execute(
            delete(models.ApprovalRequest)
        )

        database.commit()

    yield

    app.dependency_overrides.pop(
        get_database,
        None,
    )


def create_test_approval():
    return client.post(
        "/approvals",
        json={
            "operation": "market_lens.export_report",
            "resource_type": "market_report",
            "resource_id": "weekly-summary",
            "request_payload": {
                "format": "pdf",
                "include_positions": True,
            },
        },
    )


def test_create_and_read_encrypted_approval():
    response = create_test_approval()

    assert response.status_code == 201

    created = response.json()

    assert (
        created["operation"]
        == "market_lens.export_report"
    )

    assert created["status"] == "pending"

    assert created["request_payload"] == {
        "format": "pdf",
        "include_positions": True,
    }

    assert created["decision_reason"] is None
    assert created["decided_at"] is None

    with Session(test_engine) as database:
        stored = database.scalar(
            select(models.ApprovalRequest)
        )

        assert stored is not None

        assert (
            stored.request_payload is not None
        )

        assert is_protected_value(
            stored.request_payload
        )

        assert (
            "weekly-summary"
            not in stored.request_payload
        )

        events = database.scalars(
            select(models.AuditEvent)
        ).all()

        assert len(events) == 1

        assert (
            events[0].action
            == "approval_requested"
        )

        assert events[0].detail is None

    read_response = client.get(
        f"/approvals/{created['id']}"
    )

    assert read_response.status_code == 200
    assert read_response.json() == created


def test_list_and_filter_approvals():
    first = create_test_approval().json()

    second_response = client.post(
        "/approvals",
        json={
            "operation": "intel_notes.create_note",
            "resource_type": "intelligence_note",
            "request_payload": {
                "title": "Private note",
            },
        },
    )

    assert second_response.status_code == 201

    decision_response = client.post(
        (
            f"/approvals/{first['id']}"
            "/decision"
        ),
        json={
            "decision": "approved",
        },
    )

    assert decision_response.status_code == 200

    pending_response = client.get(
        "/approvals?status=pending"
    )

    approved_response = client.get(
        "/approvals?status=approved"
    )

    assert pending_response.status_code == 200
    assert approved_response.status_code == 200

    assert len(pending_response.json()) == 1
    assert len(approved_response.json()) == 1

    assert (
        pending_response.json()[0]["status"]
        == "pending"
    )

    assert (
        approved_response.json()[0]["status"]
        == "approved"
    )


def test_approve_request_encrypts_reason():
    created = create_test_approval().json()

    private_reason = (
        "Reviewed and authorized by Daryl."
    )

    response = client.post(
        (
            f"/approvals/{created['id']}"
            "/decision"
        ),
        json={
            "decision": "approved",
            "reason": private_reason,
        },
    )

    assert response.status_code == 200

    decided = response.json()

    assert decided["status"] == "approved"

    assert (
        decided["decision_reason"]
        == private_reason
    )

    assert decided["decided_at"] is not None

    with Session(test_engine) as database:
        stored = database.get(
            models.ApprovalRequest,
            created["id"],
        )

        assert stored is not None
        assert stored.status == "approved"

        assert (
            stored.decision_reason is not None
        )

        assert (
            stored.decision_reason
            != private_reason
        )

        assert is_protected_value(
            stored.decision_reason
        )

        events = database.scalars(
            select(models.AuditEvent)
            .order_by(models.AuditEvent.id)
        ).all()

        actions = [
            event.action
            for event in events
        ]

        assert actions == [
            "approval_requested",
            "approval_decided",
        ]

        assert all(
            event.detail is None
            for event in events
        )


def test_reject_request():
    created = create_test_approval().json()

    response = client.post(
        (
            f"/approvals/{created['id']}"
            "/decision"
        ),
        json={
            "decision": "rejected",
            "reason": "Action not authorized.",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_decided_request_cannot_be_decided_again():
    created = create_test_approval().json()

    first_response = client.post(
        (
            f"/approvals/{created['id']}"
            "/decision"
        ),
        json={
            "decision": "approved",
        },
    )

    assert first_response.status_code == 200

    second_response = client.post(
        (
            f"/approvals/{created['id']}"
            "/decision"
        ),
        json={
            "decision": "rejected",
        },
    )

    assert second_response.status_code == 409

    assert (
        second_response.json()["detail"]
        == (
            "Only pending approval requests "
            "can be decided."
        )
    )


def test_missing_approval_returns_not_found():
    response = client.get(
        (
            "/approvals/"
            "00000000-0000-0000-0000-000000000000"
        )
    )

    assert response.status_code == 404


def test_invalid_decision_is_rejected():
    created = create_test_approval().json()

    response = client.post(
        (
            f"/approvals/{created['id']}"
            "/decision"
        ),
        json={
            "decision": "maybe",
        },
    )

    assert response.status_code == 422


def test_corrupted_payload_fails_safely():
    with Session(test_engine) as database:
        approval = models.ApprovalRequest(
            operation="test.corrupted",
            resource_type="test_resource",
            request_payload="corrupted-plaintext",
            status="pending",
        )

        database.add(approval)
        database.commit()
        database.refresh(approval)

        approval_id = approval.id

    response = client.get(
        f"/approvals/{approval_id}"
    )

    assert response.status_code == 500

    assert response.json()["detail"] == (
        "Approval data could not be decrypted."
    )

    assert "corrupted-plaintext" not in response.text