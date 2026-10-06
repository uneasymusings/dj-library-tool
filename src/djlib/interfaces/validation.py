"""Actionable validation locations without reflecting private input values or contexts."""

import re

REASONS = {
    "missing": "required field",
    "extra_forbidden": "unexpected field",
    "json_invalid": "invalid JSON",
    "string_type": "expected text",
    "int_type": "expected integer",
    "int_parsing": "expected integer",
    "float_type": "expected number",
    "float_parsing": "expected number",
    "bool_type": "expected boolean",
    "bool_parsing": "expected boolean",
    "list_type": "expected list",
    "dict_type": "expected object",
    "model_type": "expected object",
    "literal_error": "unsupported choice",
    "enum": "unsupported choice",
    "greater_than_equal": "below allowed minimum",
    "less_than_equal": "above allowed maximum",
    "string_too_short": "text is too short",
    "string_too_long": "text is too long",
    "too_short": "too few entries",
    "too_long": "too many entries",
    "value_error": "value violates this field's constraints",
}
# Limits that come from the schema, never from the input, so they are safe to repeat.
LIMITS = {
    "greater_than_equal": ("ge", "at least {}"),
    "less_than_equal": ("le", "at most {}"),
    "string_too_short": ("min_length", "at least {} characters"),
    "string_too_long": ("max_length", "at most {} characters"),
    "too_short": ("min_length", "at least {} entries"),
    "too_long": ("max_length", "at most {} entries"),
}


def reason(detail: dict) -> str:
    kind = detail.get("type")
    context = detail.get("ctx") or {}
    if kind in {"literal_error", "enum"} and context.get("expected"):
        # The allowed choices are part of the schema, e.g. "'pause', 'resume' or 'cancel'".
        return f"choose {str(context['expected'])[:300]}"
    if kind in LIMITS and context.get(LIMITS[kind][0]) is not None:
        key, template = LIMITS[kind]
        return f"{REASONS[kind]}; use {template.format(context[key])}"
    if kind == "value_error":
        message = validator_message(detail)
        if message:
            return message
    return REASONS.get(kind, "invalid value for this field")


def validator_message(detail: dict) -> str | None:
    """A field validator's own explanation, unless it would repeat the input."""
    message = str(detail.get("msg") or "").removeprefix("Value error, ").strip()
    if not message or len(message) > 300 or any(ord(c) < 32 for c in message):
        return None
    if any(len(text) >= 3 and text in message for text in strings(detail.get("input"))):
        return None
    return message


def strings(value, depth: int = 0) -> list[str]:
    """Text found in an input value, to keep it out of messages."""
    if isinstance(value, str):
        return [value.strip()]
    if depth >= 3:
        return []
    if isinstance(value, dict):
        value = [*value.values()]
    if isinstance(value, list | tuple):
        return [text for item in value[:100] for text in strings(item, depth + 1)]
    return []


def validation_message(error):
    fields = []
    for detail in error.errors()[:20]:
        location = detail.get("loc", ())
        label = (
            ".".join(
                str(part)
                if isinstance(part, int)
                else re.sub(r"[^A-Za-z0-9_-]", "_", str(part))[:64]
                for part in location
            )
            or "input"
        )
        fields.append(f"{label}: {reason(detail)}")
    return "Invalid input: " + "; ".join(fields)
