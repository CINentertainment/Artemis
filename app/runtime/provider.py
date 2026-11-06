from abc import ABC, abstractmethod

from app.runtime.contracts import (
    ModelRequest,
    ModelResponse,
)


class ModelProviderError(RuntimeError):
    """Base error raised by a model provider."""


class ModelProviderUnavailableError(
    ModelProviderError
):
    """Raised when a provider cannot be reached."""


class ModelProviderResponseError(
    ModelProviderError
):
    """Raised when a provider returns invalid data."""


class ModelProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the stable provider identifier."""

    @abstractmethod
    def generate(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        """Generate one model response."""