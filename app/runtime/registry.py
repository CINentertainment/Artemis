from collections.abc import Iterable

from app.runtime.provider import (
    ModelProvider,
    ModelProviderError,
)


class ModelProviderNotFoundError(
    ModelProviderError
):
    """Raised when a provider is not registered."""


class ModelProviderRegistry:
    def __init__(
        self,
        providers: Iterable[
            ModelProvider
        ] | None = None,
        *,
        default_provider: str | None = None,
    ) -> None:
        self._providers: dict[
            str,
            ModelProvider,
        ] = {}

        self._default_provider: (
            str | None
        ) = None

        if providers is not None:
            for provider in providers:
                self.register(provider)

        if default_provider is not None:
            self.set_default(
                default_provider
            )

    @staticmethod
    def normalize_name(
        provider_name: str,
    ) -> str:
        return provider_name.strip().lower()

    def register(
        self,
        provider: ModelProvider,
        *,
        replace: bool = False,
    ) -> None:
        provider_name = self.normalize_name(
            provider.provider_name
        )

        if not provider_name:
            raise ValueError(
                "Provider name cannot be empty."
            )

        if (
            provider_name in self._providers
            and not replace
        ):
            raise ValueError(
                (
                    "A model provider named "
                    f"'{provider_name}' is already "
                    "registered."
                )
            )

        self._providers[
            provider_name
        ] = provider

        if self._default_provider is None:
            self._default_provider = (
                provider_name
            )

    def get(
        self,
        provider_name: str,
    ) -> ModelProvider:
        normalized_name = self.normalize_name(
            provider_name
        )

        provider = self._providers.get(
            normalized_name
        )

        if provider is None:
            raise ModelProviderNotFoundError(
                (
                    "Model provider "
                    f"'{normalized_name}' is not "
                    "registered."
                )
            )

        return provider

    def set_default(
        self,
        provider_name: str,
    ) -> None:
        provider = self.get(
            provider_name
        )

        self._default_provider = (
            self.normalize_name(
                provider.provider_name
            )
        )

    def get_default(self) -> ModelProvider:
        if self._default_provider is None:
            raise ModelProviderNotFoundError(
                "No default model provider is configured."
            )

        return self.get(
            self._default_provider
        )

    def provider_names(self) -> list[str]:
        return sorted(
            self._providers.keys()
        )