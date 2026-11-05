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


def test_create_and_read_preference():
    create_response = client.put(
        "/preferences/communication_style",
        json={
            "value": "Clear and practical",
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

    assert (
        created["value"]
        == "Clear and practical"
    )

    assert created["redacted"] is False

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


def test_sensitive_write_is_rejected():
    response = client.put(
        "/preferences/api_key",
        json={
            "value": "never-store-this",
            "is_sensitive": True,
        },
    )

    assert response.status_code == 422

    assert response.json() == {
        "detail": (
            "Sensitive preference storage is "
            "disabled until encryption is configured."
        )
    }


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

    delete_response = client.delete(
        "/preferences/voice_speed"
    )

    assert delete_response.status_code == 204

    missing_response = client.get(
        "/preferences/voice_speed"
    )

    assert missing_response.status_code == 404