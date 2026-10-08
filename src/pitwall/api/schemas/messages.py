"""Response envelopes for the Claude-native ``/v1/messages`` surface.

Request validation lives in ``pitwall.api.anthropic_translate`` (the single
translation contract); these models document the Anthropic response and error
shapes the route emits.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AnthropicUsage(BaseModel):
    input_tokens: int
    output_tokens: int


class AnthropicTextBlock(BaseModel):
    type: Literal["text"]
    text: str


class AnthropicMessagesResponse(BaseModel):
    id: str
    type: Literal["message"]
    role: Literal["assistant"]
    model: str
    content: list[AnthropicTextBlock]
    stop_reason: str | None = None
    usage: AnthropicUsage


class AnthropicErrorDetail(BaseModel):
    type: str
    message: str


class AnthropicErrorEnvelope(BaseModel):
    type: Literal["error"]
    error: AnthropicErrorDetail


__all__ = [
    "AnthropicErrorDetail",
    "AnthropicErrorEnvelope",
    "AnthropicMessagesResponse",
    "AnthropicTextBlock",
    "AnthropicUsage",
]
