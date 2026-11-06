import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base, get_database
from app.main import app


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
def isolated_conversation_database():
    app.dependency_overrides[
        get_database
    ] = override_get_database

    with Session(test_engine) as database:
        database.execute(
            delete(models.AuditEvent)
        )

        database.execute(
            delete(models.Message)
        )

        database.execute(
            delete(models.Conversation)
        )

        database.commit()

    yield

    app.dependency_overrides.pop(
        get_database,
        None,
    )


def create_conversation(
    title: str = "Private research session",
) -> dict:
    response = client.post(
        "/conversations",
        json={
            "title": title,
        },
    )

    assert response.status_code == 201

    return response.json()


def test_create_list_and_read_conversation():
    created = create_conversation(
        "Analytical planning session"
    )

    assert created["title"] == (
        "Analytical planning session"
    )

    assert created["status"] == "active"

    conversation_id = created["id"]

    read_response = client.get(
        f"/conversations/{conversation_id}"
    )

    assert read_response.status_code == 200
    assert read_response.json() == created

    list_response = client.get(
        "/conversations"
    )

    assert list_response.status_code == 200

    listed_conversations = list_response.json()

    assert len(listed_conversations) == 1
    assert (
        listed_conversations[0]["id"]
        == conversation_id
    )

    assert (
        listed_conversations[0]["title"]
        == "Analytical planning session"
    )


def test_conversation_title_is_encrypted_at_rest():
    plaintext_title = (
        "Sensitive internal planning title"
    )

    created = create_conversation(
        plaintext_title
    )

    with Session(test_engine) as database:
        stored = database.get(
            models.Conversation,
            created["id"],
        )

        assert stored is not None
        assert stored.title is not None
        assert stored.title != plaintext_title
        assert stored.title.startswith(
            "dpapi:v1:"
        )

        assert (
            plaintext_title
            not in stored.title
        )


def test_message_is_encrypted_at_rest_and_decrypted_by_api():
    created = create_conversation()

    conversation_id = created["id"]

    plaintext_message = (
        "Prepare a private analytical summary."
    )

    create_response = client.post(
        (
            f"/conversations/"
            f"{conversation_id}/messages"
        ),
        json={
            "content": plaintext_message,
        },
    )

    assert create_response.status_code == 201

    created_message = create_response.json()

    assert created_message["content"] == (
        plaintext_message
    )

    assert created_message["role"] == "user"

    assert (
        created_message["conversation_id"]
        == conversation_id
    )

    with Session(test_engine) as database:
        stored = database.get(
            models.Message,
            created_message["id"],
        )

        assert stored is not None
        assert stored.content != plaintext_message
        assert stored.content.startswith(
            "dpapi:v1:"
        )

        assert (
            plaintext_message
            not in stored.content
        )

    list_response = client.get(
        (
            f"/conversations/"
            f"{conversation_id}/messages"
        )
    )

    assert list_response.status_code == 200

    messages = list_response.json()

    assert len(messages) == 1
    assert (
        messages[0]["content"]
        == plaintext_message
    )


def test_message_pagination():
    created = create_conversation()

    conversation_id = created["id"]

    for message_number in range(1, 4):
        response = client.post(
            (
                f"/conversations/"
                f"{conversation_id}/messages"
            ),
            json={
                "content": (
                    f"Message {message_number}"
                ),
            },
        )

        assert response.status_code == 201

    first_page = client.get(
        (
            f"/conversations/"
            f"{conversation_id}/messages"
        ),
        params={
            "limit": 1,
            "offset": 0,
        },
    )

    second_page = client.get(
        (
            f"/conversations/"
            f"{conversation_id}/messages"
        ),
        params={
            "limit": 1,
            "offset": 1,
        },
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200

    assert first_page.json()[0]["content"] == (
        "Message 1"
    )

    assert second_page.json()[0]["content"] == (
        "Message 2"
    )


def test_archived_conversation_rejects_new_messages():
    created = create_conversation()

    conversation_id = created["id"]

    archive_response = client.post(
        (
            f"/conversations/"
            f"{conversation_id}/archive"
        )
    )

    assert archive_response.status_code == 200
    assert (
        archive_response.json()["status"]
        == "archived"
    )

    message_response = client.post(
        (
            f"/conversations/"
            f"{conversation_id}/messages"
        ),
        json={
            "content": (
                "This message must be rejected."
            ),
        },
    )

    assert message_response.status_code == 409

    assert message_response.json() == {
        "detail": (
            "Messages cannot be added to an "
            "inactive conversation."
        )
    }


def test_delete_conversation_removes_messages():
    created = create_conversation()

    conversation_id = created["id"]

    message_response = client.post(
        (
            f"/conversations/"
            f"{conversation_id}/messages"
        ),
        json={
            "content": "Temporary message",
        },
    )

    assert message_response.status_code == 201

    delete_response = client.delete(
        f"/conversations/{conversation_id}"
    )

    assert delete_response.status_code == 204

    missing_response = client.get(
        f"/conversations/{conversation_id}"
    )

    assert missing_response.status_code == 404

    assert missing_response.json() == {
        "detail": "Conversation not found."
    }

    with Session(test_engine) as database:
        conversation = database.get(
            models.Conversation,
            conversation_id,
        )

        remaining_messages = list(
            database.scalars(
                select(models.Message).where(
                    models.Message.conversation_id
                    == conversation_id
                )
            ).all()
        )

        assert conversation is None
        assert remaining_messages == []


def test_conversation_audits_exclude_private_content():
    private_title = (
        "Restricted conversation title"
    )

    private_message = (
        "Restricted conversation message"
    )

    created = create_conversation(
        private_title
    )

    conversation_id = created["id"]

    message_response = client.post(
        (
            f"/conversations/"
            f"{conversation_id}/messages"
        ),
        json={
            "content": private_message,
        },
    )

    assert message_response.status_code == 201

    archive_response = client.post(
        (
            f"/conversations/"
            f"{conversation_id}/archive"
        )
    )

    assert archive_response.status_code == 200

    with Session(test_engine) as database:
        events = list(
            database.scalars(
                select(models.AuditEvent)
                .order_by(
                    models.AuditEvent.id.asc()
                )
            ).all()
        )

        actions = {
            event.action
            for event in events
        }

        assert "conversation_created" in actions
        assert "message_created" in actions
        assert "conversation_archived" in actions

        for event in events:
            audit_values = " ".join(
                str(value)
                for value in (
                    event.event_type,
                    event.action,
                    event.resource_type,
                    event.resource_id,
                    event.outcome,
                    event.detail,
                )
                if value is not None
            )

            assert (
                private_title
                not in audit_values
            )

            assert (
                private_message
                not in audit_values
            )

            assert event.detail is None


def test_missing_and_invalid_conversation_ids():
    missing_id = (
        "11111111-1111-1111-1111-111111111111"
    )

    missing_response = client.get(
        f"/conversations/{missing_id}"
    )

    assert missing_response.status_code == 404

    invalid_response = client.get(
        "/conversations/not-a-valid-uuid"
    )

    assert invalid_response.status_code == 422