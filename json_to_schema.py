#!/usr/bin/env python3
"""
Infer a JSON Schema from a JSON instance in file.json.

Usage:
  python json_to_schema.py            # reads file.json, prints schema to stdout
  python json_to_schema.py -o schema.json
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from typing import Any, Dict, List, Optional, Set, Tuple


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

    return out


def infer_schema(value: Any) -> Dict[str, Any]:
    t = json_type(value)

    # Handle nullability by including "null" in type if needed in merges;
    # at leaf, just set type = "null"
    if t == "object":
        props: Dict[str, Any] = {}
        required: List[str] = []
        for k, v in value.items():
            props[k] = infer_schema(v)
            required.append(k)

        return {
            "type": "object",
            "properties": props,
            "required": sorted(required),
            # This is an opinionated default; change to True if you want permissive schemas.
            "additionalProperties": False,
        }

    if t == "array":
        if not value:
            # Empty array: we don't know item type
            return {"type": "array", "items": {}}

        # Infer schema for each element and merge
        item_schema = infer_schema(value[0])
        for item in value[1:]:
            item_schema = merge_schemas(item_schema, infer_schema(item))

        return {"type": "array", "items": item_schema}

    # Scalars
    return {"type": t}


def main() -> None:
    parser = argparse.ArgumentParser(description="Infer JSON Schema from file.json")
    parser.add_argument("-i", "--input", default="file.json", help="Input JSON file (default: file.json)")
    parser.add_argument("-o", "--output", default=None, help="Output schema file (default: stdout)")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    schema = {
        "$schema": SCHEMA_DRAFT,
        **infer_schema(data),
    }

    text = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()