from typing import Any

import httpx

from app.runtime.contracts import (
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from app.runtime.provider import (
    ModelProvider,
    ModelProviderResponseError,
    ModelProviderUnavailableError,
)


class OllamaModelProvider(ModelProvider):
    def __init__(
        self,
        *,
        base_url: str,
        default_model: str,
        timeout_seconds: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        cleaned_url = base_url.strip().rstrip(
            "/"
        )

        cleaned_model = default_model.strip()

        if not cleaned_url:
            raise ValueError(
                "Ollama URL cannot be empty."
            )

        if not cleaned_model:
            raise ValueError(
                "Ollama default model cannot "
                "be empty."
            )

        self.base_url = cleaned_url
        self.default_model = cleaned_model
        self.timeout_seconds = timeout_seconds
        self._client = client

    @property
    def provider_name(self) -> str:
        return "ollama"

    def generate(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        selected_model = (
            request.model
            or self.default_model
        )

        options: dict[str, Any] = {
            "temperature": request.temperature,
        }

        if request.max_output_tokens is not None:
            options["num_predict"] = (
                request.max_output_tokens
            )

        payload = {
            "model": selected_model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in request.messages
            ],
            "stream": False,
            "options": options,
        }

        try:
            if self._client is None:
                with httpx.Client(
                    timeout=self.timeout_seconds
                ) as client:
                    response = client.post(
                        f"{self.base_url}/api/chat",
                        json=payload,
                    )
            else:
                response = self._client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout_seconds,
                )

            response.raise_for_status()
        except httpx.RequestError as error:
            raise ModelProviderUnavailableError(
                "The local Ollama service is "
                "unavailable."
            ) from error
        except httpx.HTTPStatusError as error:
            raise ModelProviderResponseError(
                (
                    "Ollama returned HTTP status "
                    f"{error.response.status_code}."
                )
            ) from error

        try:
            response_data = response.json()
        except ValueError as error:
            raise ModelProviderResponseError(
                "Ollama returned invalid JSON."
            ) from error

        return self._build_response(
            response_data=response_data,
            requested_model=selected_model,
        )

    def _build_response(
        self,
        *,
        response_data: Any,
        requested_model: str,
    ) -> ModelResponse:
        if not isinstance(
            response_data,
            dict,
        ):
            raise ModelProviderResponseError(
                "Ollama returned an invalid response."
            )

        message_data = response_data.get(
            "message"
        )

        if not isinstance(
            message_data,
            dict,
        ):
            raise ModelProviderResponseError(
                "Ollama response did not contain "
                "a message."
            )

        content = message_data.get(
            "content"
        )

        if (
            not isinstance(content, str)
            or not content.strip()
        ):
            raise ModelProviderResponseError(
                "Ollama returned empty model content."
            )

        model_name = response_data.get(
            "model",
            requested_model,
        )

        if not isinstance(model_name, str):
            model_name = requested_model

        input_tokens = self._safe_token_count(
            response_data.get(
                "prompt_eval_count"
            )
        )

        output_tokens = self._safe_token_count(
            response_data.get(
                "eval_count"
            )
        )

        finish_reason = response_data.get(
            "done_reason"
        )

        if not isinstance(
            finish_reason,
            str,
        ):
            finish_reason = None

        return ModelResponse(
            provider=self.provider_name,
            model=model_name,
            content=content,
            finish_reason=finish_reason,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=(
                    input_tokens
                    + output_tokens
                ),
            ),
        )

    @staticmethod
    def _safe_token_count(
        value: Any,
    ) -> int:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            return value

        return 0