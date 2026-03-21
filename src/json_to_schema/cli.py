from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Sequence, Set

from .core import (
    SCHEMA_DRAFT,
    apply_field_metadata,
    infer_schema,
    validate_against_schema,
    validate_schema_definition,
)


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


def parse_field_metadata_assignments(
    parser: argparse.ArgumentParser,
    values: Sequence[str],
    *,
    option_name: str,
) -> Dict[str, str]:
    assignments: Dict[str, str] = {}
    for raw in values:
        field_path, separator, metadata_value = raw.partition("=")
        if not separator:
            parser.error(f"{option_name} value {raw!r} must be in FIELD=VALUE format")
        field_path = field_path.strip()
        if not field_path:
            parser.error(f"{option_name} value {raw!r} is missing the field path before '='")
        assignments[field_path] = metadata_value.strip()
    return assignments


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
    parser.add_argument(
        "--schema-id",
        default=None,
        metavar="URI",
        help="Set root schema $id metadata",
    )
    parser.add_argument(
        "--schema-title",
        default=None,
        metavar="TITLE",
        help="Set root schema title metadata",
    )
    parser.add_argument(
        "--schema-description",
        default=None,
        metavar="TEXT",
        help="Set root schema description metadata",
    )
    parser.add_argument(
        "--field-title",
        action="append",
        default=[],
        metavar="FIELD=TITLE",
        help="Set field-level title metadata with dot paths (supports [] for array items)",
    )
    parser.add_argument(
        "--field-description",
        action="append",
        default=[],
        metavar="FIELD=TEXT",
        help="Set field-level description metadata with dot paths (supports [] for array items)",
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
        if args.schema_id is not None:
            parser.error("--schema-id cannot be used with --validate")
        if args.schema_title is not None:
            parser.error("--schema-title cannot be used with --validate")
        if args.schema_description is not None:
            parser.error("--schema-description cannot be used with --validate")
        if args.field_title:
            parser.error("--field-title cannot be used with --validate")
        if args.field_description:
            parser.error("--field-description cannot be used with --validate")

        existing_schema = load_schema_json(parser, args.validate)
        schema_errors = validate_schema_definition(existing_schema)
        if schema_errors:
            print(
                f"Invalid schema in {args.validate}:",
                file=sys.stderr,
            )
            for error in schema_errors:
                print(f"- {error}", file=sys.stderr)
            raise SystemExit(1)

        payload = load_input_json(parser, args.input)
        validation_errors = validate_against_schema(payload, existing_schema)
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
    field_titles = parse_field_metadata_assignments(
        parser,
        args.field_title,
        option_name="--field-title",
    )
    field_descriptions = parse_field_metadata_assignments(
        parser,
        args.field_description,
        option_name="--field-description",
    )

    schema: Dict[str, Any] = {"$schema": SCHEMA_DRAFT}
    if args.schema_id is not None:
        schema["$id"] = args.schema_id
    if args.schema_title is not None:
        schema["title"] = args.schema_title
    if args.schema_description is not None:
        schema["description"] = args.schema_description

    schema.update(
        infer_schema(
            data,
            additional_properties=args.additional_properties,
            infer_bounds_fields=infer_bounds_fields,
            infer_enum_fields=infer_enum_fields,
            infer_all_bounds=args.infer_all_bounds,
            infer_all_enum=args.infer_all_enum,
        )
    )

    try:
        apply_field_metadata(schema, field_titles, metadata_key="title")
        apply_field_metadata(schema, field_descriptions, metadata_key="description")
    except ValueError as exc:
        parser.error(str(exc))

    if args.minify:
        text = json.dumps(schema, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
    else:
        text = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")

    if not args.output:
        print(text)
