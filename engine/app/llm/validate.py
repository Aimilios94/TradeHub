"""Minimal JSON-Schema validation for LLM output — stdlib only, no jsonschema dependency.

Covers exactly the subset the prompt library uses (see the `## Output schema` block of each template):
type, required, properties, enum, minimum/maximum, items, maxItems/minItems, additionalProperties.
Anything richer belongs in a real validator; keeping it small keeps the failure messages readable,
and the message is fed straight back to the model on the retry.
"""

from __future__ import annotations

from dataclasses import dataclass

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "number": (int, float), "integer": int, "null": type(None),
}


@dataclass(slots=True)
class ValidationFailure:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


def _type_ok(value, expected: str) -> bool:
    py = _TYPES.get(expected)
    if py is None:
        return True
    if expected in ("number", "integer") and isinstance(value, bool):
        return False        # bool is an int in Python; never accept it as a score
    return isinstance(value, py)


def _check(value, schema: dict, path: str) -> ValidationFailure | None:
    t = schema.get("type")
    if t and not _type_ok(value, t):
        return ValidationFailure(path, f"expected {t}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        return ValidationFailure(path, f"{value!r} is not one of {schema['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return ValidationFailure(path, f"{value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            return ValidationFailure(path, f"{value} is above maximum {schema['maximum']}")

    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return ValidationFailure(path, f"{len(value)} items, maximum is {schema['maxItems']}")
        if "minItems" in schema and len(value) < schema["minItems"]:
            return ValidationFailure(path, f"{len(value)} items, minimum is {schema['minItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                bad = _check(item, item_schema, f"{path}[{i}]")
                if bad:
                    return bad

    if isinstance(value, dict):
        props: dict = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                return ValidationFailure(path or "$", f"missing required key '{key}'")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(props))
            if extra:
                return ValidationFailure(path or "$", f"unexpected key(s) {extra}")
        for key, sub in props.items():
            if key in value and isinstance(sub, dict):
                bad = _check(value[key], sub, f"{path}.{key}" if path else key)
                if bad:
                    return bad
    return None


def validate(payload, schema: dict) -> ValidationFailure | None:
    """None when valid; otherwise the FIRST failure, phrased for the model's retry prompt."""
    return _check(payload, schema, "")
