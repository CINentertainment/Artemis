import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
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
def isolated_preference_database():
    app.dependency_overrides[
        get_database
    ] = override_get_database

    with Session(test_engine) as database:
        database.execute(
            delete(models.AuditEvent)
        )

        database.execute(
            delete(models.UserPreference)
        )

        database.commit()

    yield

    app.dependency_overrides.pop(
        get_database,
        None,
    )


def test_create_and_read_encrypted_preference():
    plaintext_value = "Clear and practical"

    create_response = client.put(
        "/preferences/communication_style",
        json={
            "value": plaintext_value,
            "value_type": "text",
            "is_sensitive": False,
        },
    )

    assert create_response.status_code == 200

    created = create_response.json()

    assert (
        created["key"]
        == "communication_style"
    )

    assert created["value"] == plaintext_value
    assert created["redacted"] is False

    with Session(test_engine) as database:
        stored = database.scalar(
            select(models.UserPreference)
            .where(
                models.UserPreference.key
                == "communication_style"
            )
        )

        assert stored is not None
        assert stored.value != plaintext_value

        assert is_protected_value(
            stored.value
        )

        assert (
            plaintext_value
            not in stored.value
        )

    read_response = client.get(
        "/preferences/communication_style"
    )

    assert read_response.status_code == 200
    assert read_response.json() == created


def test_update_preference_is_audited():
    client.put(
        "/preferences/favorite_color",
        json={
            "value": "Black",
        },
    )

    update_response = client.put(
        "/preferences/favorite_color",
        json={
            "value": "Purple",
        },
    )

    assert update_response.status_code == 200

    assert (
        update_response.json()["value"]
        == "Purple"
    )

    with Session(test_engine) as database:
        stored = database.scalar(
            select(models.UserPreference)
            .where(
                models.UserPreference.key
                == "favorite_color"
            )
        )

        assert stored is not None
        assert stored.value != "Purple"

        assert is_protected_value(
            stored.value
        )

        events = database.scalars(
            select(models.AuditEvent)
        ).all()

        actions = {
            event.action
            for event in events
        }

        assert "preference_created" in actions
        assert "preference_updated" in actions

        assert all(
            event.detail is None
            for event in events
        )


def test_sensitive_write_is_encrypted_and_redacted():
    private_value = "never-display-this"

    response = client.put(
        "/preferences/private_context",
        json={
            "value": private_value,
            "is_sensitive": True,
        },
    )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["value"] is None
    assert response_data["is_sensitive"] is True
    assert response_data["redacted"] is True
    assert private_value not in response.text

    with Session(test_engine) as database:
        stored = database.scalar(
            select(models.UserPreference)
            .where(
                models.UserPreference.key
                == "private_context"
            )
        )

        assert stored is not None
        assert stored.value != private_value

        assert is_protected_value(
            stored.value
        )

        assert private_value not in stored.value


def test_existing_sensitive_value_is_redacted():
    with Session(test_engine) as database:
        preference = models.UserPreference(
            key="legacy_secret",
            value="hidden-value",
            is_sensitive=True,
        )

        database.add(preference)
        database.commit()

    response = client.get(
        "/preferences/legacy_secret"
    )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["value"] is None
    assert response_data["is_sensitive"] is True
    assert response_data["redacted"] is True

    assert "hidden-value" not in response.text


def test_legacy_plaintext_value_is_readable():
    with Session(test_engine) as database:
        preference = models.UserPreference(
            key="legacy_setting",
            value="legacy plaintext",
            is_sensitive=False,
        )

        database.add(preference)
        database.commit()

    response = client.get(
        "/preferences/legacy_setting"
    )

    assert response.status_code == 200

    assert (
        response.json()["value"]
        == "legacy plaintext"
    )


def test_list_and_delete_preferences():
    client.put(
        "/preferences/voice_speed",
        json={
            "value": "normal",
        },
    )

    list_response = client.get(
        "/preferences"
    )

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    assert (
        list_response.json()[0]["value"]
        == "normal"
    )

    delete_response = client.delete(
        "/preferences/voice_speed"
    )

    assert delete_response.status_code == 204

    missing_response = client.get(
        "/preferences/voice_speed"
    )

    assert missing_response.status_code == 404