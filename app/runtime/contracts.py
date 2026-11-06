from typing import Literal

from pydantic import BaseModel, Field


ModelMessageRole = Literal[
    "system",
    "user",
    "assistant",
    "tool",
]


class ModelMessage(BaseModel):
    role: ModelMessageRole

    content: str = Field(
        min_length=1,
        max_length=100000,
    )


class ModelRequest(BaseModel):
    messages: list[ModelMessage] = Field(
        min_length=1,
        max_length=500,
    )

    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )

    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
    )

    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=100000,
    )


class TokenUsage(BaseModel):
    input_tokens: int = Field(
        default=0,
        ge=0,
    )

    output_tokens: int = Field(
        default=0,
        ge=0,
    )

    total_tokens: int = Field(
        default=0,
        ge=0,
    )


class ModelResponse(BaseModel):
    provider: str = Field(
        min_length=1,
        max_length=100,
    )

    model: str = Field(
        min_length=1,
        max_length=200,
    )

    content: str = Field(
        min_length=1,
        max_length=100000,
    )

    finish_reason: str | None = Field(
        default=None,
        max_length=100,
    )

    usage: TokenUsage = Field(
        default_factory=TokenUsage
    )