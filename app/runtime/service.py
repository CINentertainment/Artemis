from app.config import Settings
from app.runtime.contracts import (
    ModelRequest,
    ModelResponse,
)
from app.runtime.deterministic import (
    DeterministicModelProvider,
)
from app.runtime.ollama import (
    OllamaModelProvider,
)
from app.runtime.registry import (
    ModelProviderRegistry,
)


class ModelRuntime:
    def __init__(
        self,
        *,
        registry: ModelProviderRegistry,
        default_model: str,
    ) -> None:
        cleaned_model = default_model.strip()

        if not cleaned_model:
            raise ValueError(
                "Default model cannot be empty."
            )

        self.registry = registry
        self.default_model = cleaned_model

    def generate(
        self,
        request: ModelRequest,
        *,
        provider_name: str | None = None,
    ) -> ModelResponse:
        effective_request = request

        if request.model is None:
            effective_request = (
                request.model_copy(
                    update={
                        "model": self.default_model,
                    }
                )
            )

        if provider_name is None:
            provider = (
                self.registry.get_default()
            )
        else:
            provider = self.registry.get(
                provider_name
            )

        return provider.generate(
            effective_request
        )

    def available_providers(
        self,
    ) -> list[str]:
        return self.registry.provider_names()


def build_model_runtime(
    settings: Settings,
) -> ModelRuntime:
    registry = ModelProviderRegistry(
        providers=[
            DeterministicModelProvider(
                default_model=(
                    settings.model_name
                )
            ),
            OllamaModelProvider(
                base_url=settings.ollama_url,
                default_model=(
                    settings.model_name
                ),
                timeout_seconds=(
                    settings
                    .ollama_timeout_seconds
                ),
            ),
        ]
    )

    registry.set_default(
        settings.model_provider
    )

    return ModelRuntime(
        registry=registry,
        default_model=settings.model_name,
    )