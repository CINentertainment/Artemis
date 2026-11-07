import json

import httpx
import pytest

from app.runtime.contracts import (
    ModelMessage,
    ModelRequest,
)
from app.runtime.ollama import (
    OllamaModelProvider,
)
from app.runtime.provider import (
    ModelProviderResponseError,
    ModelProviderUnavailableError,
)


def build_request(
    *,
    model: str | None = None,
    max_output_tokens: int | None = None,
) -> ModelRequest:
    return ModelRequest(
        messages=[
            ModelMessage(
                role="system",
                content="You are Artemis.",
            ),
            ModelMessage(
                role="user",
                content="Provide a concise summary.",
            ),
        ],
        model=model,
        temperature=0.3,
        max_output_tokens=max_output_tokens,
    )


def test_ollama_provider_builds_local_chat_request():
    captured_payload = {}

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        captured_payload.update(
            json.loads(
                request.content.decode(
                    "utf-8"
                )
            )
        )

        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {
                    "role": "assistant",
                    "content": "Local response",
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 12,
                "eval_count": 4,
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        )
    )

    provider = OllamaModelProvider(
        base_url="http://127.0.0.1:11434/",
        default_model="qwen3:8b",
        client=client,
    )

    response = provider.generate(
        build_request(
            max_output_tokens=256
        )
    )

    assert captured_payload["model"] == (
        "qwen3:8b"
    )

    assert captured_payload["stream"] is False
    assert captured_payload["think"] is False

    assert (
        captured_payload["options"][
            "temperature"
        ]
        == 0.3
    )

    assert (
        captured_payload["options"][
            "num_predict"
        ]
        == 256
    )

    assert len(
        captured_payload["messages"]
    ) == 2

    assert response.provider == "ollama"
    assert response.model == "qwen3:8b"
    assert response.content == "Local response"
    assert response.finish_reason == "stop"
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 4
    assert response.usage.total_tokens == 16

    client.close()


def test_request_model_overrides_ollama_default():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        payload = json.loads(
            request.content.decode("utf-8")
        )

        assert payload["model"] == (
            "alternate-local-model"
        )

        return httpx.Response(
            200,
            json={
                "model": (
                    "alternate-local-model"
                ),
                "message": {
                    "content": "Alternate response",
                },
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        )
    )

    provider = OllamaModelProvider(
        base_url="http://127.0.0.1:11434",
        default_model="qwen3:8b",
        client=client,
    )

    response = provider.generate(
        build_request(
            model="alternate-local-model"
        )
    )

    assert response.model == (
        "alternate-local-model"
    )

    client.close()


def test_ollama_connection_failure_is_safe():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        raise httpx.ConnectError(
            "Connection failed",
            request=request,
        )

    client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        )
    )

    provider = OllamaModelProvider(
        base_url="http://127.0.0.1:11434",
        default_model="qwen3:8b",
        client=client,
    )

    with pytest.raises(
        ModelProviderUnavailableError,
        match="unavailable",
    ):
        provider.generate(
            build_request()
        )

    client.close()


def test_ollama_http_failure_is_safe():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "error": "private server detail",
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        )
    )

    provider = OllamaModelProvider(
        base_url="http://127.0.0.1:11434",
        default_model="qwen3:8b",
        client=client,
    )

    with pytest.raises(
        ModelProviderResponseError,
        match="HTTP status 500",
    ):
        provider.generate(
            build_request()
        )

    client.close()


def test_ollama_rejects_invalid_json():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
        )

    client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        )
    )

    provider = OllamaModelProvider(
        base_url="http://127.0.0.1:11434",
        default_model="qwen3:8b",
        client=client,
    )

    with pytest.raises(
        ModelProviderResponseError,
        match="invalid JSON",
    ):
        provider.generate(
            build_request()
        )

    client.close()


def test_ollama_rejects_missing_message():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "done": True,
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        )
    )

    provider = OllamaModelProvider(
        base_url="http://127.0.0.1:11434",
        default_model="qwen3:8b",
        client=client,
    )

    with pytest.raises(
        ModelProviderResponseError,
        match="did not contain a message",
    ):
        provider.generate(
            build_request()
        )

    client.close()


def test_ollama_rejects_empty_content():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen3:8b",
                "message": {
                    "content": "",
                },
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        )
    )

    provider = OllamaModelProvider(
        base_url="http://127.0.0.1:11434",
        default_model="qwen3:8b",
        client=client,
    )

    with pytest.raises(
        ModelProviderResponseError,
        match="empty model content",
    ):
        provider.generate(
            build_request()
        )

    client.close()