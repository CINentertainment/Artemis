import pytest

from app.services.policy import (
    classify_operation,
    normalize_operation,
)


@pytest.mark.parametrize(
    "operation",
    [
        "intel_notes.list",
        "intel_notes.get",
        "intel_notes.search",
        "intel_notes.analytics",
        "risk_tracker.read",
        "risk_tracker.status",
        "market_lens.analyze",
        "market_lens.preview",
        "services.health",
    ],
)
def test_read_only_operations_are_allowed(
    operation: str,
):
    decision = classify_operation(operation)

    assert decision.operation == operation
    assert decision.disposition == "allowed"
    assert decision.is_allowed is True
    assert decision.requires_approval is False
    assert decision.is_prohibited is False


@pytest.mark.parametrize(
    "operation",
    [
        "intel_notes.create",
        "intel_notes.update",
        "intel_notes.delete",
        "risk_tracker.archive",
        "risk_tracker.write",
        "market_lens.export",
        "market_lens.generate",
        "communications.send",
        "reports.publish",
    ],
)
def test_consequential_operations_require_approval(
    operation: str,
):
    decision = classify_operation(operation)

    assert (
        decision.disposition
        == "approval_required"
    )

    assert decision.is_allowed is False
    assert decision.requires_approval is True
    assert decision.is_prohibited is False


@pytest.mark.parametrize(
    "operation",
    [
        "artemis.disable_security",
        "artemis.disable_audit",
        "artemis.disable_encryption",
        "artemis.expose_network",
        "credentials.export",
        "credentials.read_plaintext",
        "database.drop",
        "database.export_unencrypted",
        "security.disable",
        "shell.execute_arbitrary",
    ],
)
def test_dangerous_operations_are_prohibited(
    operation: str,
):
    decision = classify_operation(operation)

    assert decision.disposition == "prohibited"
    assert decision.is_allowed is False
    assert decision.requires_approval is False
    assert decision.is_prohibited is True


@pytest.mark.parametrize(
    "operation",
    [
        "tools.bypass_approval",
        "runtime.disable_approval",
        "runtime.disable_security_checks",
        "database.drop_database_now",
        "network.expose_network_publicly",
        "secrets.export_credentials",
    ],
)
def test_prohibited_fragments_fail_closed(
    operation: str,
):
    decision = classify_operation(operation)

    assert decision.is_prohibited is True


def test_unknown_operation_requires_approval():
    decision = classify_operation(
        "future_connector.experimental_action"
    )

    assert (
        decision.disposition
        == "approval_required"
    )

    assert decision.requires_approval is True

    assert "Unknown operations" in (
        decision.reason
    )


def test_operation_is_normalized():
    decision = classify_operation(
        "  MARKET_LENS.SEARCH  "
    )

    assert (
        decision.operation
        == "market_lens.search"
    )

    assert decision.is_allowed is True


@pytest.mark.parametrize(
    "operation",
    [
        "",
        " ",
        "\n",
    ],
)
def test_empty_operation_is_rejected(
    operation: str,
):
    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        normalize_operation(operation)


def test_overly_long_operation_is_rejected():
    with pytest.raises(
        ValueError,
        match="too long",
    ):
        normalize_operation(
            "a" * 101
        )