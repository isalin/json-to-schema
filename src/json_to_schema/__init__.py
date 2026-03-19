"""
Infer a JSON Schema from a JSON instance in file.json.

Usage:
  python -m json_to_schema            # reads file.json, prints schema to stdout
  echo '{"a": 1}' | python -m json_to_schema
  python -m json_to_schema -o schema.json
  python -m json_to_schema --minify
  python -m json_to_schema -i payload.json --validate schema.json
"""

from .cli import (
    load_input_json,
    load_schema_json,
    main,
    parse_bool_flag,
    parse_field_list,
    parse_field_metadata_assignments,
)
from .core import (
    IDENTIFIER_RE,
    SCHEMA_DRAFT,
    VALID_JSON_TYPES,
    apply_field_metadata,
    infer_schema,
    json_type,
    merge_required,
    merge_schemas,
    merge_types,
    resolve_field_schema_path,
    should_infer_for_field,
    validate_against_schema,
    validate_schema_definition,
)

__all__ = [
    "IDENTIFIER_RE",
    "SCHEMA_DRAFT",
    "VALID_JSON_TYPES",
    "apply_field_metadata",
    "infer_schema",
    "json_type",
    "load_input_json",
    "load_schema_json",
    "main",
    "merge_required",
    "merge_schemas",
    "merge_types",
    "parse_bool_flag",
    "parse_field_list",
    "parse_field_metadata_assignments",
    "resolve_field_schema_path",
    "should_infer_for_field",
    "validate_against_schema",
    "validate_schema_definition",
]
