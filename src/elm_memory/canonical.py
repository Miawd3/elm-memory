"""Strict JSON parsing and RFC 8785 canonicalization for security digests.

ELM's Phase 5A submission schema deliberately excludes binary floating-point
numbers.  The restricted encoder below therefore implements the RFC 8785 rules
needed by that closed schema without adding a runtime dependency.
"""
from __future__ import annotations

import json
from typing import Any


class CanonicalJSONError(ValueError):
    """Input cannot be represented by ELM's closed canonical JSON profile."""


def _reject_constant(value: str) -> None:
    raise CanonicalJSONError(f"Non-finite JSON number is not allowed: {value}")


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalJSONError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def parse_closed_json(text: str) -> Any:
    """Parse JSON while rejecting duplicate keys, floats, and non-finite values."""
    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_float=lambda value: (_ for _ in ()).throw(
                CanonicalJSONError(f"Floating-point JSON number is not allowed: {value}")
            ),
            parse_constant=_reject_constant,
        )
    except CanonicalJSONError:
        raise
    except json.JSONDecodeError as exc:
        raise CanonicalJSONError(f"Invalid JSON: {exc.msg}") from exc


def _validate_string(value: str) -> None:
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise CanonicalJSONError("Lone Unicode surrogate is not valid I-JSON.")


def _utf16_sort_key(value: str) -> bytes:
    _validate_string(value)
    return value.encode("utf-16-be")


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        _validate_string(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int) and not isinstance(value, bool):
        # RFC 8785 is based on I-JSON/ECMAScript numbers. The Phase 5A payload
        # currently contains no integers, but keeping the safe range explicit
        # makes this helper usable by later closed schemas.
        if abs(value) > 9_007_199_254_740_991:
            raise CanonicalJSONError("JSON integer exceeds the I-JSON safe range.")
        return str(value)
    if isinstance(value, float):
        raise CanonicalJSONError("Floating-point values are not allowed in this schema.")
    if isinstance(value, list):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalJSONError("JSON object keys must be strings.")
        parts = []
        for key in sorted(value, key=_utf16_sort_key):
            parts.append(_encode(key) + ":" + _encode(value[key]))
        return "{" + ",".join(parts) + "}"
    raise CanonicalJSONError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return RFC 8785-compatible UTF-8 bytes for ELM's restricted schemas."""
    return _encode(value).encode("utf-8")
