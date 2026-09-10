"""LLM failure modes. All are surfaced to the caller — none is ever swallowed into a default result."""

from __future__ import annotations


class LLMError(RuntimeError):
    """Base: the model did not produce a usable answer."""


class LLMUnavailable(LLMError):
    """Transport-level: Ollama not running, model not pulled, timeout, HTTP error."""


class LLMInvalidJSON(LLMError):
    """The model answered, but not with an object matching the template's output_schema."""
