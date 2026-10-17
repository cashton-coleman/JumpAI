"""Deliberately small, strictly checked JSON Schema subset (see README)."""

import json
import math


class JumpError(Exception):
    """An actionable catalog, contract, or execution error."""


def canonical(value):
    """Encode JSON deterministically; reject nonfinite or nonserializable values."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def parse_json(text):
    """Decode JSON text or bytes, raising JumpError for invalid or ambiguous data."""
    def invalid(value):
        """Reject the decoder's nonstandard NaN and infinity literals."""
        raise JumpError(f"Non-finite JSON number: {value}")

    def pairs(items):
        """Build an object from decoded pairs while rejecting duplicate keys."""
        result = {}
        for key, value in items:
            if key in result:
                raise JumpError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(text, parse_constant=invalid, object_pairs_hook=pairs)
        canonical(value)  # Also reject exponent overflow to infinity.
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise JumpError(f"Invalid JSON: {exc}") from exc


# Supported schema vocabulary; unknown constraints are rejected, never ignored.
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
KEYS = {"type", "description", "properties", "required", "additionalProperties",
        "items", "enum", "minimum", "maximum", "minLength", "maxLength",
        "minItems", "maxItems"}


def check_schema(schema, path="schema"):
    """Check the supported schema subset recursively; raise JumpError with a path."""
    if not isinstance(schema, dict):
        raise JumpError(f"{path}: expected a schema object")
    unknown = set(schema) - KEYS
    if unknown:
        raise JumpError(f"{path}: unsupported schema keywords: {sorted(unknown)}")
    kind = schema.get("type")
    if not isinstance(kind, str) or kind not in TYPES:
        raise JumpError(f"{path}: type must be one of {sorted(TYPES)}")
    if "description" in schema and not isinstance(schema["description"], str):
        raise JumpError(f"{path}: description must be a string")
    groups = {"object": {"properties", "required", "additionalProperties"},
              "array": {"items", "minItems", "maxItems"},
              "string": {"minLength", "maxLength"},
              "number": {"minimum", "maximum"}, "integer": {"minimum", "maximum"}}
    for target, keys in groups.items():
        if keys & schema.keys() and kind != target and not (target in {"number", "integer"} and kind in {"number", "integer"}):
            raise JumpError(f"{path}: {sorted(keys & schema.keys())} not valid for {kind}")
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not all(isinstance(k, str) for k in properties):
            raise JumpError(f"{path}: properties must be an object")
        if not isinstance(required, list) or not all(isinstance(x, str) for x in required) or len(set(required)) != len(required):
            raise JumpError(f"{path}: required must contain unique strings")
        if set(required) - properties.keys():
            raise JumpError(f"{path}: required fields need property schemas")
        if not isinstance(schema.get("additionalProperties", True), bool):
            raise JumpError(f"{path}: additionalProperties must be boolean")
        for key, child in properties.items():
            check_schema(child, f"{path}.{key}")
    if kind == "array":
        if "items" not in schema:
            raise JumpError(f"{path}: arrays require items")
        check_schema(schema["items"], f"{path}.items")
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in schema:
            v = schema[key]
            if type(v) not in (int, float) or not math.isfinite(v):
                raise JumpError(f"{path}.{key}: expected finite number")
            if key not in {"minimum", "maximum"} and (type(v) is not int or v < 0):
                raise JumpError(f"{path}.{key}: expected nonnegative integer")
    for low, high in (("minimum", "maximum"), ("minLength", "maxLength"), ("minItems", "maxItems")):
        if low in schema and high in schema and schema[low] > schema[high]:
            raise JumpError(f"{path}: {low} exceeds {high}")
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not schema["enum"]:
            raise JumpError(f"{path}: enum must be a nonempty array")
        for value in schema["enum"]:
            validate(value, {k: v for k, v in schema.items() if k != "enum"}, path)


def validate(value, schema, path="input"):
    """Validate a JSON value against a checked schema; raise JumpError on mismatch."""
    kind = schema["type"]
    ok = {"object": isinstance(value, dict), "array": isinstance(value, list),
          "string": isinstance(value, str), "integer": type(value) is int,
          "number": type(value) in (int, float), "boolean": type(value) is bool,
          "null": value is None}[kind]
    if not ok:
        raise JumpError(f"{path}: expected {kind}")
    if kind == "number" and not math.isfinite(value):
        raise JumpError(f"{path}: expected finite number")
    if "enum" in schema and canonical(value) not in [canonical(v) for v in schema["enum"]]:
        raise JumpError(f"{path}: value is not in enum")
    if kind == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise JumpError(f"{path}: missing required fields {sorted(missing)}")
        extra = value.keys() - properties.keys()
        if extra and schema.get("additionalProperties") is False:
            raise JumpError(f"{path}: unexpected fields {sorted(extra)}")
        for key in value.keys() & properties.keys():
            validate(value[key], properties[key], f"{path}.{key}")
    if kind == "array":
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")
    for key, size, lower in (("minimum", value, True), ("maximum", value, False),
                             ("minLength", len(value) if kind == "string" else 0, True),
                             ("maxLength", len(value) if kind == "string" else 0, False),
                             ("minItems", len(value) if kind == "array" else 0, True),
                             ("maxItems", len(value) if kind == "array" else 0, False)):
        if key in schema and (size < schema[key] if lower else size > schema[key]):
            raise JumpError(f"{path}: violates {key}={schema[key]}")
