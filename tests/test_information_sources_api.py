from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import (
    create_database_engine,
    get_database,
    initialize_database,
)
from app.main import app


@pytest.fixture
def client(
    tmp_path: Path,
) -> Generator[TestClient, None, None]:
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    initialize_database(engine)

    def override_get_database():
        with Session(engine) as database:
            yield database

    app.dependency_overrides[
        get_database
    ] = override_get_database

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    engine.dispose()


def build_source_payload() -> dict:
    return {
        "key": "local-research",
        "name": "Local Research Library",
        "source_type": "document",
        "description": (
            "Locally stored research documents."
        ),
        "location": (
            "C:\\Artemis\\Research"
        ),
        "is_enabled": True,
        "is_read_only": True,
        "notes": (
            "Initial controlled information "
            "source."
        ),
    }


def test_register_information_source(
    client: TestClient,
):
    response = client.post(
        "/information-sources",
        json=build_source_payload(),
    )

    assert response.status_code == 201

    response_data = response.json()

    assert response_data["id"] is not None
    assert (
        response_data["key"]
        == "local-research"
    )
    assert (
        response_data["name"]
        == "Local Research Library"
    )
    assert (
        response_data["source_type"]
        == "document"
    )
    assert response_data["status"] == "active"
    assert response_data["is_enabled"] is True
    assert response_data["is_read_only"] is True


def test_duplicate_source_key_is_rejected(
    client: TestClient,
):
    payload = build_source_payload()

    first_response = client.post(
        "/information-sources",
        json=payload,
    )

    second_response = client.post(
        "/information-sources",
        json=payload,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409

    assert second_response.json() == {
        "detail": (
            "An information source with this "
            "key already exists."
        )
    }


def test_read_information_source(
    client: TestClient,
):
    client.post(
        "/information-sources",
        json=build_source_payload(),
    )

    response = client.get(
        "/information-sources/local-research"
    )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["key"]
        == "local-research"
    )
    assert (
        response_data["location"]
        == "C:\\Artemis\\Research"
    )


def test_list_information_sources(
    client: TestClient,
):
    first_payload = build_source_payload()

    second_payload = {
        "key": "manual-notes",
        "name": "Manual Notes",
        "source_type": "manual",
    }

    client.post(
        "/information-sources",
        json=first_payload,
    )

    client.post(
        "/information-sources",
        json=second_payload,
    )

    response = client.get(
        "/information-sources"
    )

    assert response.status_code == 200

    response_data = response.json()

    assert len(response_data) == 2

    source_keys = {
        source["key"]
        for source in response_data
    }

    assert source_keys == {
        "local-research",
        "manual-notes",
    }


def test_update_information_source(
    client: TestClient,
):
    client.post(
        "/information-sources",
        json=build_source_payload(),
    )

    response = client.patch(
        "/information-sources/local-research",
        json={
            "name": "Verified Research Library",
            "status": "inactive",
            "is_enabled": False,
            "notes": "Paused for source review.",
        },
    )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["name"]
        == "Verified Research Library"
    )
    assert response_data["status"] == "inactive"
    assert response_data["is_enabled"] is False
    assert (
        response_data["notes"]
        == "Paused for source review."
    )


def test_archive_information_source(
    client: TestClient,
):
    client.post(
        "/information-sources",
        json=build_source_payload(),
    )

    delete_response = client.delete(
        "/information-sources/local-research"
    )

    assert delete_response.status_code == 204

    active_response = client.get(
        "/information-sources"
    )

    assert active_response.status_code == 200
    assert active_response.json() == []

    archived_response = client.get(
        "/information-sources",
        params={
            "include_archived": True,
        },
    )

    assert archived_response.status_code == 200

    archived_sources = (
        archived_response.json()
    )

    assert len(archived_sources) == 1
    assert (
        archived_sources[0]["status"]
        == "archived"
    )
    assert (
        archived_sources[0]["is_enabled"]
        is False
    )


def test_missing_information_source_returns_404(
    client: TestClient,
):
    response = client.get(
        "/information-sources/missing-source"
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Information source not found."
    }


def test_invalid_source_key_is_rejected(
    client: TestClient,
):
    payload = build_source_payload()
    payload["key"] = "Invalid Source Key"

    response = client.post(
        "/information-sources",
        json=payload,
    )

    assert response.status_code == 422