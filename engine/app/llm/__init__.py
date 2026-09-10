"""LLM access for the engine — local (Ollama) now, hosted (OpenRouter) in Phase 4.

Plan §10 two-tier design: local Hermes 3 8B does high-volume / low-stakes work (the sentiment
condenser, template 01); the hosted 70B/405B models do judgment (templates 02-07, Phase 4).
Kept provider-split so adding OpenRouter never touches the Phase 3 call sites.

Every model call in TradeHub goes through here so three rules hold everywhere (plan §33):
  - strict JSON in, strict JSON out — validated against the template's own `output_schema`
  - one retry with the validation error appended, then FAIL — a bad answer is never smoothed over
  - never fabricate: on failure the caller records the failure, it does not invent a result
"""

from __future__ import annotations

from app.llm.errors import LLMError, LLMInvalidJSON, LLMUnavailable
from app.llm.prompts import PromptSpec, load, render
from app.llm.validate import ValidationFailure, validate

__all__ = [
    "LLMError",
    "LLMInvalidJSON",
    "LLMUnavailable",
    "PromptSpec",
    "ValidationFailure",
    "complete_json",
    "load",
    "render",
    "validate",
]


def complete_json(spec: PromptSpec, variables: dict, *, model: str | None = None, timeout: float = 120.0,
                  retries: int = 1) -> tuple[dict, dict]:
    """Render `spec`, call the right provider, validate against the template's output_schema.

    Returns (payload, meta). Raises LLMError if the model never produced a schema-valid object —
    the caller marks the unit of work failed rather than substituting a default.
    """
    from app.llm import ollama

    if spec.tier != "local":
        raise LLMError(f"template '{spec.slug}' is tier '{spec.tier}'; hosted tier arrives in Phase 4")

    system = spec.system_prompt
    user = render(spec.user_template, variables)
    use_model = model or spec.model
    attempts: list[str] = []

    for attempt in range(retries + 1):
        payload = ollama.chat_json(system, user, model=use_model, timeout=timeout)
        failure = validate(payload, spec.output_schema)
        if failure is None:
            return payload, {"model": use_model, "attempts": attempt + 1, "prior_errors": attempts}
        attempts.append(str(failure))
        if attempt == retries:
            break
        # retry once with the validator's complaint appended, exactly as template 01's Notes prescribe
        user = (f"{render(spec.user_template, variables)}\n\n"
                f"Your previous answer was rejected by the schema validator: {failure}\n"
                f"Return a corrected JSON object that satisfies the schema. Output JSON only.")

    raise LLMInvalidJSON(f"{spec.slug}: schema validation failed after {retries + 1} attempt(s): "
                         + " | ".join(attempts))
