import pytest

from app.runtime.contracts import (
    ModelMessage,
    ModelRequest,
)
from app.runtime.deterministic import (
    DeterministicModelProvider,
)
from app.runtime.registry import (
    ModelProviderNotFoundError,
    ModelProviderRegistry,
)


def test_deterministic_provider_generates_response():
    provider = DeterministicModelProvider()

    response = provider.generate(
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content="Review this report",
                ),
            ],
        )
    )

    assert response.provider == (
        "deterministic"
    )

    assert response.model == (
        "artemis-deterministic"
    )

    assert response.content == (
        "Artemis received: Review this report"
    )

    assert response.finish_reason == "stop"

    assert (
        response.usage.total_tokens
        == (
            response.usage.input_tokens
            + response.usage.output_tokens
        )
    )


def test_deterministic_provider_honors_model():
    provider = DeterministicModelProvider()

    response = provider.generate(
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content="Test",
                ),
            ],
            model="requested-model",
        )
    )

    assert response.model == (
        "requested-model"
    )


def test_registry_uses_first_provider_as_default():
    provider = DeterministicModelProvider()

    registry = ModelProviderRegistry(
        providers=[
            provider,
        ]
    )

    assert (
        registry.get_default()
        is provider
    )


def test_registry_lookup_is_case_insensitive():
    provider = DeterministicModelProvider()

    registry = ModelProviderRegistry(
        providers=[
            provider,
        ]
    )

    assert (
        registry.get(" DETERMINISTIC ")
        is provider
    )


def test_registry_rejects_duplicate_provider():
    registry = ModelProviderRegistry()

    registry.register(
        DeterministicModelProvider()
    )

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        registry.register(
            DeterministicModelProvider()
        )


def test_registry_can_replace_provider():
    original = DeterministicModelProvider(
        response_prefix="Original"
    )

    replacement = DeterministicModelProvider(
        response_prefix="Replacement"
    )

    registry = ModelProviderRegistry(
        providers=[
            original,
        ]
    )

    registry.register(
        replacement,
        replace=True,
    )

    assert (
        registry.get("deterministic")
        is replacement
    )


def test_registry_rejects_unknown_provider():
    registry = ModelProviderRegistry()

    with pytest.raises(
        ModelProviderNotFoundError,
        match="not registered",
    ):
        registry.get("missing-provider")


def test_empty_registry_has_no_default():
    registry = ModelProviderRegistry()

    with pytest.raises(
        ModelProviderNotFoundError,
        match="No default",
    ):
        registry.get_default()