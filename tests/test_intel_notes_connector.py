import httpx
import pytest

from app.connectors.base import (
    ConnectorResponseError,
)
from app.connectors.intel_notes import (
    IntelNotesConnector,
)


def build_connector(
    handler,
) -> IntelNotesConnector:
    client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        )
    )

    return IntelNotesConnector(
        base_url="http://127.0.0.1:8001",
        timeout_seconds=2.0,
        client=client,
    )


def test_connector_identity_and_capabilities():
    connector = build_connector(
        lambda request: httpx.Response(
            200,
            json={"status": "healthy"},
        )
    )

    assert (
        connector.connector_key
        == "intel-notes"
    )

    assert (
        connector.connector_name
        == "Intel Notes Manager"
    )

    assert connector.capabilities == (
        "intel_notes.health",
        "intel_notes.list",
        "intel_notes.get",
        "intel_notes.search",
        "intel_notes.analytics",
    )


def test_health_uses_verified_endpoint():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/health"

        return httpx.Response(
            200,
            json={"status": "healthy"},
        )

    connector = build_connector(handler)

    health = connector.check_health()

    assert health.available is True
    assert health.status_code == 200


def test_list_notes_without_filters():
    notes = [
        {
            "id": 2,
            "title": "Second note",
        },
        {
            "id": 1,
            "title": "First note",
        },
    ]

    def handler(request):
        assert request.url.path == "/notes"
        assert not request.url.params

        return httpx.Response(
            200,
            json=notes,
        )

    connector = build_connector(handler)

    assert connector.list_notes() == notes


def test_list_notes_sends_verified_filters():
    def handler(request):
        assert request.url.path == "/notes"

        assert (
            request.url.params["keyword"]
            == "infrastructure"
        )

        assert (
            request.url.params[
                "classification"
            ]
            == "UNCLASSIFIED"
        )

        assert (
            request.url.params["discipline"]
            == "OSINT"
        )

        assert (
            request.url.params["reliability"]
            == "A"
        )

        assert (
            request.url.params["credibility"]
            == "1"
        )

        assert (
            request.url.params["location"]
            == "San Antonio"
        )

        assert (
            request.url.params["start_date"]
            == "2026-01-01"
        )

        assert (
            request.url.params["end_date"]
            == "2026-12-31"
        )

        return httpx.Response(
            200,
            json=[],
        )

    connector = build_connector(handler)

    response = connector.list_notes(
        keyword=" infrastructure ",
        classification=" UNCLASSIFIED ",
        discipline=" OSINT ",
        reliability=" A ",
        credibility=" 1 ",
        location=" San Antonio ",
        start_date=" 2026-01-01 ",
        end_date=" 2026-12-31 ",
    )

    assert response == []


def test_blank_filters_are_omitted():
    def handler(request):
        assert request.url.path == "/notes"
        assert not request.url.params

        return httpx.Response(
            200,
            json=[],
        )

    connector = build_connector(handler)

    connector.list_notes(
        keyword=" ",
        location="",
    )


def test_get_note_uses_verified_endpoint():
    note = {
        "id": 42,
        "title": "Verified intelligence note",
        "body": "Private analytical content",
    }

    def handler(request):
        assert (
            request.url.path
            == "/notes/42"
        )

        return httpx.Response(
            200,
            json=note,
        )

    connector = build_connector(handler)

    assert connector.get_note(42) == note


@pytest.mark.parametrize(
    "note_id",
    [
        0,
        -1,
        -100,
    ],
)
def test_invalid_note_id_is_rejected(
    note_id: int,
):
    connector = build_connector(
        lambda request: httpx.Response(
            200,
            json={},
        )
    )

    with pytest.raises(
        ValueError,
        match="greater than zero",
    ):
        connector.get_note(note_id)


def test_get_analytics_uses_verified_endpoint():
    analytics = {
        "total_notes": 12,
        "classification_distribution": [
            {
                "label": "UNCLASSIFIED",
                "count": 12,
            }
        ],
        "discipline_distribution": [],
        "reliability_distribution": [],
        "credibility_distribution": [],
        "source_evaluation_distribution": [],
        "location_distribution": [],
        "tag_distribution": [],
        "monthly_distribution": [],
    }

    def handler(request):
        assert (
            request.url.path
            == "/analytics"
        )

        return httpx.Response(
            200,
            json=analytics,
        )

    connector = build_connector(handler)

    assert (
        connector.get_analytics()
        == analytics
    )


@pytest.mark.parametrize(
    (
        "method_name",
        "response_value",
        "expected_message",
    ),
    [
        (
            "list_notes",
            {"notes": []},
            "invalid note collection",
        ),
        (
            "list_notes",
            ["invalid-note"],
            "invalid note record",
        ),
        (
            "get_note",
            [{"id": 1}],
            "invalid note record",
        ),
        (
            "get_analytics",
            [],
            "invalid analytics response",
        ),
    ],
)
def test_invalid_response_shapes_are_rejected(
    method_name: str,
    response_value,
    expected_message: str,
):
    connector = build_connector(
        lambda request: httpx.Response(
            200,
            json=response_value,
        )
    )

    method = getattr(
        connector,
        method_name,
    )

    with pytest.raises(
        ConnectorResponseError,
        match=expected_message,
    ):
        if method_name == "get_note":
            method(1)
        else:
            method()