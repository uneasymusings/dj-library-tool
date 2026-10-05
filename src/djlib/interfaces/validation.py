"""Actionable validation locations without reflecting private input values or contexts."""

import re


def validation_message(error):
    reasons = {
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
        "greater_than_equal": "below allowed minimum",
        "less_than_equal": "above allowed maximum",
        "string_too_short": "text is too short",
        "string_too_long": "text is too long",
        "too_short": "too few entries",
        "too_long": "too many entries",
        "value_error": "value violates this field's constraints",
    }
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
        reason = reasons.get(detail.get("type"), "invalid value for this field")
        fields.append(f"{label}: {reason}")
    return "Invalid input: " + "; ".join(fields)
