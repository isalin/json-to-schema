#!/usr/bin/env python3
"""
Infer a JSON Schema from a JSON instance in file.json.

Usage:
  python json_to_schema.py            # reads file.json, prints schema to stdout
  echo '{"a": 1}' | python json_to_schema.py
  python json_to_schema.py -o schema.json
  python json_to_schema.py --minify
  python json_to_schema.py -i payload.json --validate schema.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALID_JSON_TYPES = {"null", "boolean", "integer", "number", "string", "array", "object"}


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    # Fallback (rare)
    return "string"


def merge_types(t1: Any, t2: Any) -> Any:
    """
    Merge JSON Schema 'type' fields.
    Can be a string or a list of strings.
    """
    def to_set(t: Any) -> Set[str]:
        if t is None:
            return set()
        if isinstance(t, str):
            return {t}
        if isinstance(t, list):
            return set(t)
        return set()

    s = to_set(t1) | to_set(t2)
    if not s:
        return None
    if len(s) == 1:
        return next(iter(s))
    return sorted(s)


def merge_required(r1: Optional[List[str]], r2: Optional[List[str]]) -> Optional[List[str]]:
    if not r1 and not r2:
        return None
    s1 = set(r1 or [])
    s2 = set(r2 or [])
    inter = s1 & s2
    return sorted(inter) if inter else None


def merge_schemas(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two inferred schemas into a more general schema.
    Tries to keep it valid JSON Schema while not overfitting.
    """
    out: Dict[str, Any] = {}

    # Merge type
    out_type = merge_types(a.get("type"), b.get("type"))
    if out_type is not None:
        out["type"] = out_type

    # If either is an anyOf, flatten by wrapping the other
    # (simple approach: just use anyOf for mixed structures)
    if a.get("anyOf") or b.get("anyOf"):
        anyof: List[Dict[str, Any]] = []
        for s in (a.get("anyOf") or [a]):
            anyof.append(s)
        for s in (b.get("anyOf") or [b]):
            anyof.append(s)
        # de-dupe naive by JSON string
        seen = set()
        deduped = []
        for s in anyof:
            key = json.dumps(s, sort_keys=True)
            if key not in seen:
                seen.add(key)
                deduped.append(s)
        return {"anyOf": deduped}

    # Merge object details
    if "properties" in a or "properties" in b or (a.get("type") == "object" or b.get("type") == "object"):
        props_a = a.get("properties", {})
        props_b = b.get("properties", {})
        merged_props: Dict[str, Any] = {}
        keys = set(props_a) | set(props_b)
        for k in keys:
            if k in props_a and k in props_b:
                merged_props[k] = merge_schemas(props_a[k], props_b[k])
            elif k in props_a:
                merged_props[k] = deepcopy(props_a[k])
            else:
                merged_props[k] = deepcopy(props_b[k])

        out["properties"] = merged_props

        req = merge_required(a.get("required"), b.get("required"))
        if req:
            out["required"] = req

        # additionalProperties: if either schema allows extras, allow extras
        # If both explicitly false, keep false; otherwise default to true/unspecified.
        ap_a = a.get("additionalProperties", None)
        ap_b = b.get("additionalProperties", None)
        if ap_a is False and ap_b is False:
            out["additionalProperties"] = False

    # Merge array details
    if "items" in a or "items" in b or (a.get("type") == "array" or b.get("type") == "array"):
        items_a = a.get("items")
        items_b = b.get("items")
        if items_a and items_b:
            out["items"] = merge_schemas(items_a, items_b)
        elif items_a:
            out["items"] = deepcopy(items_a)
        elif items_b:
            out["items"] = deepcopy(items_b)

    # Merge numeric constraints if both present (conservative)
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in a and key in b:
            if key in ("minimum", "minLength", "minItems"):
                out[key] = min(a[key], b[key])
            else:
                out[key] = max(a[key], b[key])
        elif key in a:
            out[key] = a[key]
        elif key in b:
            out[key] = b[key]

    # Merge enum values by set union while preserving first-seen order.
    if "enum" in a or "enum" in b:
        merged_enum: List[Any] = []
        seen = set()
        for candidate in (a.get("enum", []), b.get("enum", [])):
            for value in candidate:
                key = json.dumps(value, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    merged_enum.append(value)
        if merged_enum:
            out["enum"] = merged_enum

    return out


def parse_bool_flag(value: str) -> bool:
    parsed = value.strip().lower()
    if parsed == "true":
        return True
    if parsed == "false":
        return False
    raise argparse.ArgumentTypeError("must be one of: true, false")


def parse_field_list(values: Sequence[str]) -> Set[str]:
    fields: Set[str] = set()
    for raw in values:
        for part in raw.split(","):
            name = part.strip()
            if name:
                fields.add(name)
    return fields


def should_infer_for_field(
    field_name: Optional[str], fields: Set[str], infer_all: bool
) -> bool:
    return infer_all or (field_name is not None and field_name in fields)


def infer_schema(
    value: Any,
    *,
    additional_properties: bool = False,
    infer_bounds_fields: Optional[Set[str]] = None,
    infer_enum_fields: Optional[Set[str]] = None,
    infer_all_bounds: bool = False,
    infer_all_enum: bool = False,
    field_name: Optional[str] = None,
) -> Dict[str, Any]:
    t = json_type(value)
    bounds_fields = infer_bounds_fields or set()
    enum_fields = infer_enum_fields or set()
    infer_bounds_here = should_infer_for_field(field_name, bounds_fields, infer_all_bounds)
    infer_enum_here = should_infer_for_field(field_name, enum_fields, infer_all_enum)

    # Handle nullability by including "null" in type if needed in merges;
    # at leaf, just set type = "null"
    if t == "object":
        props: Dict[str, Any] = {}
        required: List[str] = []
        for k, v in value.items():
            props[k] = infer_schema(
                v,
                additional_properties=additional_properties,
                infer_bounds_fields=bounds_fields,
                infer_enum_fields=enum_fields,
                infer_all_bounds=infer_all_bounds,
                infer_all_enum=infer_all_enum,
                field_name=k,
            )
            required.append(k)

        return {
            "type": "object",
            "properties": props,
            "required": sorted(required),
            "additionalProperties": additional_properties,
        }

    if t == "array":
        if not value:
            # Empty array: we don't know item type
            schema: Dict[str, Any] = {"type": "array", "items": {}}
            if infer_bounds_here:
                schema["minItems"] = 0
                schema["maxItems"] = 0
            return schema

        # Infer schema for each element and merge
        item_schema = infer_schema(
            value[0],
            additional_properties=additional_properties,
            infer_bounds_fields=bounds_fields,
            infer_enum_fields=enum_fields,
            infer_all_bounds=infer_all_bounds,
            infer_all_enum=infer_all_enum,
            field_name=field_name,
        )
        for item in value[1:]:
            item_schema = merge_schemas(
                item_schema,
                infer_schema(
                    item,
                    additional_properties=additional_properties,
                    infer_bounds_fields=bounds_fields,
                    infer_enum_fields=enum_fields,
                    infer_all_bounds=infer_all_bounds,
                    infer_all_enum=infer_all_enum,
                    field_name=field_name,
                ),
            )

        schema = {"type": "array", "items": item_schema}
        if infer_bounds_here:
            schema["minItems"] = len(value)
            schema["maxItems"] = len(value)
        return schema

    # Scalars
    schema = {"type": t}
    if infer_bounds_here:
        if t in ("integer", "number"):
            schema["minimum"] = value
            schema["maximum"] = value
        elif t == "string":
            value_len = len(value)
            schema["minLength"] = value_len
            schema["maxLength"] = value_len
    if infer_enum_here and t in ("null", "boolean", "integer", "number", "string"):
        schema["enum"] = [value]
    return schema


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def _is_json_number(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)


def _json_path_for_key(path: str, key: str) -> str:
    if IDENTIFIER_RE.match(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key)}]"


def _describe_type(value: Any) -> str:
    value_type = json_type(value)
    if value_type == "number":
        return "number" if math.isfinite(value) else "non-finite number"
    return value_type


def validate_schema_definition(schema: Any, *, path: str = "$") -> List[str]:
    errors: List[str] = []

    if isinstance(schema, bool):
        return errors
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object or boolean"]

    schema_type = schema.get("type")
    if schema_type is not None:
        type_values = [schema_type] if isinstance(schema_type, str) else schema_type
        if not isinstance(type_values, list):
            errors.append(f"{path}.type: must be a string or array of strings")
        elif not type_values:
            errors.append(f"{path}.type: array must not be empty")
        else:
            seen = set()
            for idx, type_name in enumerate(type_values):
                if not isinstance(type_name, str):
                    errors.append(f"{path}.type[{idx}]: must be a string")
                    continue
                if type_name not in VALID_JSON_TYPES:
                    errors.append(f"{path}.type[{idx}]: unsupported type {type_name!r}")
                if type_name in seen:
                    errors.append(f"{path}.type[{idx}]: duplicate type {type_name!r}")
                seen.add(type_name)

    any_of = schema.get("anyOf")
    if any_of is not None:
        if not isinstance(any_of, list):
            errors.append(f"{path}.anyOf: must be an array")
        elif not any_of:
            errors.append(f"{path}.anyOf: must not be empty")
        else:
            for idx, candidate in enumerate(any_of):
                errors.extend(
                    validate_schema_definition(candidate, path=f"{path}.anyOf[{idx}]")
                )

    enum_values = schema.get("enum")
    if enum_values is not None:
        if not isinstance(enum_values, list):
            errors.append(f"{path}.enum: must be an array")
        elif not enum_values:
            errors.append(f"{path}.enum: must not be empty")

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            errors.append(f"{path}.properties: must be an object")
        else:
            for key, value in properties.items():
                key_path = _json_path_for_key(f"{path}.properties", key)
                errors.extend(validate_schema_definition(value, path=key_path))

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list):
            errors.append(f"{path}.required: must be an array")
        else:
            seen_required = set()
            for idx, field in enumerate(required):
                if not isinstance(field, str):
                    errors.append(f"{path}.required[{idx}]: must be a string")
                    continue
                if field in seen_required:
                    errors.append(f"{path}.required[{idx}]: duplicate field {field!r}")
                seen_required.add(field)

    additional_properties = schema.get("additionalProperties")
    if additional_properties is not None:
        if isinstance(additional_properties, bool):
            pass
        elif isinstance(additional_properties, dict):
            errors.extend(
                validate_schema_definition(
                    additional_properties,
                    path=f"{path}.additionalProperties",
                )
            )
        else:
            errors.append(f"{path}.additionalProperties: must be a boolean or object")

    items = schema.get("items")
    if items is not None:
        if isinstance(items, bool):
            pass
        elif isinstance(items, dict):
            errors.extend(validate_schema_definition(items, path=f"{path}.items"))
        else:
            errors.append(f"{path}.items: must be a boolean or object")

    for key in ("minimum", "maximum"):
        if key in schema and not _is_json_number(schema[key]):
            errors.append(f"{path}.{key}: must be a number")

    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema:
            if not isinstance(schema[key], int) or isinstance(schema[key], bool):
                errors.append(f"{path}.{key}: must be an integer")
            elif schema[key] < 0:
                errors.append(f"{path}.{key}: must be >= 0")

    if (
        "minimum" in schema
        and "maximum" in schema
        and _is_json_number(schema["minimum"])
        and _is_json_number(schema["maximum"])
        and schema["minimum"] > schema["maximum"]
    ):
        errors.append(f"{path}: minimum cannot be greater than maximum")

    if (
        "minLength" in schema
        and "maxLength" in schema
        and isinstance(schema["minLength"], int)
        and isinstance(schema["maxLength"], int)
        and not isinstance(schema["minLength"], bool)
        and not isinstance(schema["maxLength"], bool)
        and schema["minLength"] > schema["maxLength"]
    ):
        errors.append(f"{path}: minLength cannot be greater than maxLength")

    if (
        "minItems" in schema
        and "maxItems" in schema
        and isinstance(schema["minItems"], int)
        and isinstance(schema["maxItems"], int)
        and not isinstance(schema["minItems"], bool)
        and not isinstance(schema["maxItems"], bool)
        and schema["minItems"] > schema["maxItems"]
    ):
        errors.append(f"{path}: minItems cannot be greater than maxItems")

    return errors


def validate_against_schema(
    value: Any,
    schema: Any,
    *,
    path: str = "$",
) -> List[str]:
    if isinstance(schema, bool):
        return [] if schema else [f"{path}: disallowed by schema (false)"]

    if not isinstance(schema, dict):
        return [f"{path}: invalid schema encountered during validation"]

    errors: List[str] = []

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        branches = [validate_against_schema(value, candidate, path=path) for candidate in any_of]
        if all(branch_errors for branch_errors in branches):
            summary = "; ".join(branch[0] for branch in branches if branch) or "no anyOf branch matched"
            errors.append(f"{path}: does not match any allowed schema ({summary})")
            return errors

    schema_type = schema.get("type")
    if schema_type is not None:
        expected_types = [schema_type] if isinstance(schema_type, str) else list(schema_type)
        if expected_types and not any(_type_matches(value, expected) for expected in expected_types):
            expected_text = ", ".join(expected_types)
            errors.append(
                f"{path}: expected type {expected_text}, got {_describe_type(value)}"
            )
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} is not in enum {schema['enum']!r}")

    if isinstance(value, dict):
        required_fields = schema.get("required", [])
        for field in required_fields:
            if field not in value:
                errors.append(f"{path}: missing required property {field!r}")

        properties = schema.get("properties", {})
        for field_name, field_value in value.items():
            if field_name in properties:
                errors.extend(
                    validate_against_schema(
                        field_value,
                        properties[field_name],
                        path=_json_path_for_key(path, field_name),
                    )
                )

        additional_properties = schema.get("additionalProperties", True)
        extra_fields = sorted(field for field in value if field not in properties)
        if additional_properties is False:
            for field in extra_fields:
                errors.append(f"{path}: additional property {field!r} is not allowed")
        elif isinstance(additional_properties, dict):
            for field in extra_fields:
                errors.extend(
                    validate_against_schema(
                        value[field],
                        additional_properties,
                        path=_json_path_for_key(path, field),
                    )
                )

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(
                f"{path}: expected at least {schema['minItems']} items, got {len(value)}"
            )
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(
                f"{path}: expected at most {schema['maxItems']} items, got {len(value)}"
            )

        items_schema = schema.get("items")
        if isinstance(items_schema, bool) and not items_schema and value:
            errors.append(f"{path}: items are not allowed by schema")
        elif isinstance(items_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    validate_against_schema(item, items_schema, path=f"{path}[{index}]")
                )

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(
                f"{path}: expected length >= {schema['minLength']}, got {len(value)}"
            )
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(
                f"{path}: expected length <= {schema['maxLength']}, got {len(value)}"
            )

    if _is_json_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: expected value >= {schema['minimum']}, got {value}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: expected value <= {schema['maximum']}, got {value}")

    return errors


def load_input_json(parser: argparse.ArgumentParser, input_path: str) -> Any:
    try:
        if input_path == "file.json" and not sys.stdin.isatty():
            return json.load(sys.stdin)
        with open(input_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        parser.error(f"Input file not found: {input_path}")
    except json.JSONDecodeError as exc:
        source = "stdin" if input_path == "file.json" and not sys.stdin.isatty() else input_path
        parser.error(
            f"Invalid JSON in {source}: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        )


def load_schema_json(parser: argparse.ArgumentParser, schema_path: str) -> Any:
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        parser.error(f"Schema file not found: {schema_path}")
    except json.JSONDecodeError as exc:
        parser.error(
            f"Invalid JSON in schema file {schema_path}: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Infer JSON Schema from file.json")
    parser.add_argument("-i", "--input", default="file.json", help="Input JSON file (default: file.json)")
    parser.add_argument("-o", "--output", default=None, help="Output schema file (default: stdout)")
    parser.add_argument(
        "--validate",
        default=None,
        metavar="SCHEMA_FILE",
        help="Validate input JSON (-i or stdin) against a schema file",
    )
    parser.add_argument(
        "--minify",
        action="store_true",
        help="Print compact/minified JSON output",
    )
    parser.add_argument(
        "--additional-properties",
        default=False,
        type=parse_bool_flag,
        metavar="[false|true]",
        help="Set object additionalProperties (default: false)",
    )
    parser.add_argument(
        "--infer-bounds",
        nargs="+",
        default=[],
        metavar="FIELD",
        help="Infer min/max constraints only for the listed field names",
    )
    parser.add_argument(
        "--infer-enum",
        nargs="+",
        default=[],
        metavar="FIELD",
        help="Infer enum constraints only for the listed field names",
    )
    parser.add_argument(
        "--infer-all-bounds",
        action="store_true",
        help="Infer min/max constraints for all applicable fields",
    )
    parser.add_argument(
        "--infer-all-enum",
        action="store_true",
        help="Infer enum constraints for all applicable fields",
    )
    args = parser.parse_args()

    if args.validate:
        if args.output:
            parser.error("--output cannot be used with --validate")
        if args.minify:
            parser.error("--minify cannot be used with --validate")
        if args.additional_properties is True:
            parser.error("--additional-properties cannot be used with --validate")
        if args.infer_bounds:
            parser.error("--infer-bounds cannot be used with --validate")
        if args.infer_enum:
            parser.error("--infer-enum cannot be used with --validate")
        if args.infer_all_bounds:
            parser.error("--infer-all-bounds cannot be used with --validate")
        if args.infer_all_enum:
            parser.error("--infer-all-enum cannot be used with --validate")

        schema = load_schema_json(parser, args.validate)
        schema_errors = validate_schema_definition(schema)
        if schema_errors:
            print(
                f"Invalid schema in {args.validate}:",
                file=sys.stderr,
            )
            for error in schema_errors:
                print(f"- {error}", file=sys.stderr)
            raise SystemExit(1)

        payload = load_input_json(parser, args.input)
        validation_errors = validate_against_schema(payload, schema)
        if validation_errors:
            print(
                f"Validation failed: input data does not match schema {args.validate}.",
                file=sys.stderr,
            )
            for error in validation_errors:
                print(f"- {error}", file=sys.stderr)
            raise SystemExit(1)

        source = "stdin" if args.input == "file.json" and not sys.stdin.isatty() else args.input
        print(f"Validation passed: {source} matches schema {args.validate}.")
        return

    data = load_input_json(parser, args.input)

    infer_bounds_fields = parse_field_list(args.infer_bounds)
    infer_enum_fields = parse_field_list(args.infer_enum)

    schema = {
        "$schema": SCHEMA_DRAFT,
        **infer_schema(
            data,
            additional_properties=args.additional_properties,
            infer_bounds_fields=infer_bounds_fields,
            infer_enum_fields=infer_enum_fields,
            infer_all_bounds=args.infer_all_bounds,
            infer_all_enum=args.infer_all_enum,
        ),
    }

    if args.minify:
        text = json.dumps(schema, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
    else:
        text = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")

    if not args.output:
        print(text)


if __name__ == "__main__":
    main()
