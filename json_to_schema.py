#!/usr/bin/env python3
"""
Infer a JSON Schema from a JSON instance in file.json.

Usage:
  python json_to_schema.py            # reads file.json, prints schema to stdout
  echo '{"a": 1}' | python json_to_schema.py
  python json_to_schema.py -o schema.json
  python json_to_schema.py --minify
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Infer JSON Schema from file.json")
    parser.add_argument("-i", "--input", default="file.json", help="Input JSON file (default: file.json)")
    parser.add_argument("-o", "--output", default=None, help="Output schema file (default: stdout)")
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

    # If input is omitted and stdin is piped, read JSON from stdin.
    try:
        if args.input == "file.json" and not sys.stdin.isatty():
            data = json.load(sys.stdin)
        else:
            with open(args.input, "r", encoding="utf-8") as f:
                data = json.load(f)
    except FileNotFoundError:
        parser.error(f"Input file not found: {args.input}")
    except json.JSONDecodeError as exc:
        source = "stdin" if args.input == "file.json" and not sys.stdin.isatty() else args.input
        parser.error(
            f"Invalid JSON in {source}: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        )

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
    else:
        print(text)


if __name__ == "__main__":
    main()
