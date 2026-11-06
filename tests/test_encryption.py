import pytest

from app.services.encryption import (
    DataProtectionError,
    is_protected_value,
    protect_text,
    unprotect_text,
)


def test_encryption_round_trip():
    original_value = (
        "Artemis private conversation data."
    )

    protected_value = protect_text(
        original_value
    )

    assert protected_value != original_value
    assert original_value not in protected_value
    assert is_protected_value(
        protected_value
    )

    restored_value = unprotect_text(
        protected_value
    )

    assert restored_value == original_value


def test_same_text_uses_different_ciphertext():
    first_value = protect_text(
        "Repeated private value"
    )

    second_value = protect_text(
        "Repeated private value"
    )

    assert first_value != second_value

    assert unprotect_text(
        first_value
    ) == "Repeated private value"

    assert unprotect_text(
        second_value
    ) == "Repeated private value"


def test_empty_text_round_trip():
    protected_value = protect_text("")

    assert is_protected_value(
        protected_value
    )

    assert unprotect_text(
        protected_value
    ) == ""


def test_plain_text_cannot_be_decrypted():
    with pytest.raises(
        DataProtectionError
    ):
        unprotect_text(
            "This is not encrypted."
        )


def test_malformed_value_is_rejected():
    with pytest.raises(
        DataProtectionError
    ):
        unprotect_text(
            "dpapi:v1:not-valid-base64!!!"
        )