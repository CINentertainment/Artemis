from app.runtime.contracts import (
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from app.runtime.provider import ModelProvider


class DeterministicModelProvider(
    ModelProvider
):
    def __init__(
        self,
        *,
        default_model: str = (
            "artemis-deterministic"
        ),
        response_prefix: str = (
            "Artemis received"
        ),
    ) -> None:
        self.default_model = default_model
        self.response_prefix = response_prefix

    @property
    def provider_name(self) -> str:
        return "deterministic"

    def generate(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        latest_message = request.messages[-1]

        response_content = (
            f"{self.response_prefix}: "
            f"{latest_message.content}"
        )

        input_tokens = sum(
            len(message.content.split())
            for message in request.messages
        )

        output_tokens = len(
            response_content.split()
        )

        return ModelResponse(
            provider=self.provider_name,
            model=(
                request.model
                or self.default_model
            ),
            content=response_content,
            finish_reason="stop",
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=(
                    input_tokens
                    + output_tokens
                ),
            ),
        )