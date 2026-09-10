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
    "provider_for",
    "render",
    "schema_contract",
    "validate",
]


def provider_for(tier: str):
    """local → Ollama on the owner's GPU (free); hosted → OpenRouter (paid, judgment work)."""
    from app.llm import ollama, openrouter

    if tier == "local":
        return ollama
    if tier == "hosted":
        return openrouter
    raise LLMError(f"unknown prompt tier '{tier}' (expected 'local' or 'hosted')")


def schema_contract(schema: dict) -> str:
    """The response-format block appended to every prompt.

    We validate answers against the template's `output_schema`, so the model has to be SHOWN that schema —
    otherwise it invents a reasonable-looking shape of its own and every answer fails validation. (Template
    01 survived without this only because its system prompt happens to enumerate every field in prose;
    templates 02-04 describe the task, not the keys. Verified 2026-09-10 — see docs/DECISIONS.md #32.)

    The schema lives in the owner's prompt library already; this only puts it in front of the model.
    """
    import json as _json

    return (
        "\n\nRESPONSE FORMAT — return exactly ONE JSON object that validates against this JSON Schema.\n"
        "Use these key names verbatim. Do not rename, nest, wrap, or add keys. Do not key the object by\n"
        "symbol. No prose and no markdown fences — JSON only.\n"
        "Every key listed in \"required\" must be present on EVERY object, including negative results such\n"
        "as a rejection or a 'skip'. A key that is NOT required and does not apply must be OMITTED\n"
        "entirely — never send it as an empty string or null. Any field with an \"enum\" must carry one of\n"
        "its listed values exactly.\n\n"
        f"{_json.dumps(schema, separators=(',', ':'))}"
    )


def complete_json(spec: PromptSpec, variables: dict, *, model: str | None = None, timeout: float | None = None,
                  retries: int | None = None) -> tuple[dict, dict]:
    """Render `spec`, call the provider its tier selects, validate against the template's output_schema.

    Returns (payload, meta). Raises LLMError if the model never produced a schema-valid object —
    the caller marks the unit of work failed rather than substituting a default.
    """
    provider = provider_for(spec.tier)
    # hosted models are slower and the plan prompts are much larger than the condenser's
    timeout = timeout if timeout is not None else (120.0 if spec.tier == "local" else 240.0)
    # the hosted schemas are far richer (nested arrays of objects), so one extra correction round is
    # worth the ~$0.0007 it costs; the local condenser's single-object schema does not need it
    retries = retries if retries is not None else (1 if spec.tier == "local" else 2)

    system = spec.system_prompt
    base_user = render(spec.user_template, variables) + schema_contract(spec.output_schema)
    user = base_user
    use_model = model or spec.model
    attempts: list[str] = []

    for attempt in range(retries + 1):
        payload = provider.chat_json(system, user, model=use_model, timeout=timeout)
        failure = validate(payload, spec.output_schema)
        if failure is None:
            return payload, {"model": use_model, "tier": spec.tier, "attempts": attempt + 1,
                             "prior_errors": attempts}
        attempts.append(str(failure))
        if attempt == retries:
            break
        # retry once with the validator's complaint appended, exactly as template 01's Notes prescribe
        user = (f"{base_user}\n\n"
                f"Your previous answer was rejected by the schema validator: {failure}\n"
                f"Return a corrected JSON object that satisfies the schema. Output JSON only.")

    raise LLMInvalidJSON(f"{spec.slug}: schema validation failed after {retries + 1} attempt(s): "
                         + " | ".join(attempts))
