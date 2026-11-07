import pytest
from pydantic import ValidationError

from app.config import Settings
from app.runtime.contracts import (
    ModelMessage,
    ModelRequest,
)
from app.runtime.ollama import (
    OllamaModelProvider,
)
from app.runtime.registry import (
    ModelProviderNotFoundError,
)
from app.runtime.service import (
    build_model_runtime,
)


def build_test_settings(
    **overrides,
) -> Settings:
    values = {
        "model_provider": "deterministic",
        "model_name": "artemis-test-model",
        "model_temperature": 0.2,
        "model_max_output_tokens": 512,
        "ollama_url": (
            "http://127.0.0.1:11434"
        ),
        "ollama_timeout_seconds": 120.0,
        "_env_file": None,
    }

    values.update(overrides)

    return Settings(**values)


def test_model_settings_have_safe_defaults():
    settings = Settings(
        _env_file=None
    )

    assert (
        settings.model_provider
        == "deterministic"
    )

    assert (
        settings.model_name
        == "artemis-deterministic"
    )

    assert settings.model_temperature == 0.2

    assert (
        settings.model_max_output_tokens
        == 2048
    )

    assert settings.ollama_url == (
        "http://127.0.0.1:11434"
    )

    assert (
        settings.ollama_timeout_seconds
        == 120.0
    )


def test_model_settings_reject_temperature_above_limit():
    with pytest.raises(ValidationError):
        build_test_settings(
            model_temperature=2.1
        )


def test_model_settings_reject_invalid_token_limit():
    with pytest.raises(ValidationError):
        build_test_settings(
            model_max_output_tokens=0
        )


def test_runtime_uses_configured_default_provider():
    settings = build_test_settings()

    runtime = build_model_runtime(
        settings
    )

    response = runtime.generate(
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content="Run analysis",
                ),
            ],
        )
    )

    assert response.provider == (
        "deterministic"
    )

    assert response.model == (
        "artemis-test-model"
    )


def test_request_model_overrides_default_model():
    runtime = build_model_runtime(
        build_test_settings()
    )

    response = runtime.generate(
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content="Use another model",
                ),
            ],
            model="alternate-model",
        )
    )

    assert response.model == (
        "alternate-model"
    )


def test_runtime_lists_available_providers():
    runtime = build_model_runtime(
        build_test_settings()
    )

    assert runtime.available_providers() == [
        "deterministic",
        "ollama",
    ]


def test_runtime_registers_ollama_configuration():
    settings = build_test_settings(
        model_name="qwen3:8b",
        ollama_timeout_seconds=90.0,
    )

    runtime = build_model_runtime(
        settings
    )

    provider = runtime.registry.get(
        "ollama"
    )

    assert isinstance(
        provider,
        OllamaModelProvider,
    )

    assert provider.base_url == (
        "http://127.0.0.1:11434"
    )

    assert provider.default_model == (
        "qwen3:8b"
    )

    assert provider.timeout_seconds == 90.0


def test_ollama_can_be_selected_as_default():
    runtime = build_model_runtime(
        build_test_settings(
            model_provider="ollama",
            model_name="qwen3:8b",
        )
    )

    assert (
        runtime.registry
        .get_default()
        .provider_name
        == "ollama"
    )


def test_runtime_rejects_unknown_provider():
    runtime = build_model_runtime(
        build_test_settings()
    )

    with pytest.raises(
        ModelProviderNotFoundError,
        match="not registered",
    ):
        runtime.generate(
            ModelRequest(
                messages=[
                    ModelMessage(
                        role="user",
                        content="Test",
                    ),
                ],
            ),
            provider_name="missing-provider",
        )