import pytest
from pydantic import ValidationError

from app.runtime.contracts import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from app.runtime.provider import (
    ModelProvider,
    ModelProviderError,
    ModelProviderResponseError,
    ModelProviderUnavailableError,
)


class DeterministicTestProvider(
    ModelProvider
):
    @property
    def provider_name(self) -> str:
        return "deterministic-test"

    def generate(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        latest_message = request.messages[-1]

        return ModelResponse(
            provider=self.provider_name,
            model=request.model or "test-model",
            content=(
                f"Received: "
                f"{latest_message.content}"
            ),
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=3,
                output_tokens=4,
                total_tokens=7,
            ),
        )


def test_model_request_accepts_valid_messages():
    request = ModelRequest(
        messages=[
            ModelMessage(
                role="system",
                content="You are Artemis.",
            ),
            ModelMessage(
                role="user",
                content="Provide a summary.",
            ),
        ],
        model="local-test-model",
        temperature=0.3,
        max_output_tokens=500,
    )

    assert len(request.messages) == 2
    assert request.messages[1].role == "user"
    assert request.temperature == 0.3
    assert request.max_output_tokens == 500


def test_model_request_requires_messages():
    with pytest.raises(ValidationError):
        ModelRequest(
            messages=[],
        )


def test_model_message_rejects_invalid_role():
    with pytest.raises(ValidationError):
        ModelMessage(
            role="administrator",
            content="Invalid role",
        )


def test_model_request_rejects_invalid_temperature():
    with pytest.raises(ValidationError):
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content="Test",
                ),
            ],
            temperature=3.0,
        )


def test_provider_contract_generates_response():
    provider = DeterministicTestProvider()

    response = provider.generate(
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content="Hello Artemis",
                ),
            ],
        )
    )

    assert (
        provider.provider_name
        == "deterministic-test"
    )

    assert response.provider == (
        "deterministic-test"
    )

    assert response.model == "test-model"

    assert response.content == (
        "Received: Hello Artemis"
    )

    assert response.finish_reason == "stop"
    assert response.usage.total_tokens == 7


def test_provider_errors_share_base_type():
    assert issubclass(
        ModelProviderUnavailableError,
        ModelProviderError,
    )

    assert issubclass(
        ModelProviderResponseError,
        ModelProviderError,
    )