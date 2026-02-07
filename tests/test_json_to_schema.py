import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import json_to_schema

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeStdin(io.StringIO):
    def __init__(self, text: str, *, is_tty: bool):
        super().__init__(text)
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


class TestJsonType(unittest.TestCase):
    def test_json_type_scalars(self):
        self.assertEqual(json_to_schema.json_type(None), "null")
        self.assertEqual(json_to_schema.json_type(True), "boolean")
        self.assertEqual(json_to_schema.json_type(False), "boolean")
        self.assertEqual(json_to_schema.json_type(1), "integer")
        self.assertEqual(json_to_schema.json_type(1.5), "number")
        self.assertEqual(json_to_schema.json_type("hi"), "string")

    def test_json_type_containers_and_fallback(self):
        self.assertEqual(json_to_schema.json_type([]), "array")
        self.assertEqual(json_to_schema.json_type({}), "object")

        class Custom:
            pass

        self.assertEqual(json_to_schema.json_type(Custom()), "string")


class TestMergeTypes(unittest.TestCase):
    def test_merge_types(self):
        self.assertIsNone(json_to_schema.merge_types(None, None))
        self.assertEqual(json_to_schema.merge_types("string", None), "string")
        self.assertEqual(
            json_to_schema.merge_types("string", ["number", "string"]),
            ["number", "string"],
        )
        self.assertEqual(
            json_to_schema.merge_types(["number"], ["integer", "string"]),
            ["integer", "number", "string"],
        )


class TestMergeRequired(unittest.TestCase):
    def test_merge_required(self):
        self.assertIsNone(json_to_schema.merge_required(None, None))
        self.assertIsNone(json_to_schema.merge_required([], []))
        self.assertEqual(json_to_schema.merge_required(["a"], ["a", "b"]), ["a"])
        self.assertIsNone(json_to_schema.merge_required(["a"], ["b"]))


class TestMergeSchemas(unittest.TestCase):
    def test_merge_schemas_object_and_required(self):
        a = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        }
        b = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "c": {"type": "boolean"}},
            "required": ["a", "c"],
            "additionalProperties": False,
        }
        merged = json_to_schema.merge_schemas(a, b)
        self.assertEqual(merged["type"], "object")
        self.assertEqual(sorted(merged["properties"].keys()), ["a", "b", "c"])
        self.assertEqual(merged["required"], ["a"])
        self.assertFalse(merged["additionalProperties"])

    def test_merge_schemas_anyof_dedup(self):
        a = {"anyOf": [{"type": "string"}]}
        b = {"type": "integer"}
        merged = json_to_schema.merge_schemas(a, b)
        self.assertIn("anyOf", merged)
        self.assertEqual(len(merged["anyOf"]), 2)
        types = sorted(item.get("type") for item in merged["anyOf"])
        self.assertEqual(types, ["integer", "string"])

    def test_merge_schemas_arrays(self):
        a = {"type": "array", "items": {"type": "string"}}
        b = {"type": "array", "items": {"type": "integer"}}
        merged = json_to_schema.merge_schemas(a, b)
        self.assertEqual(merged["type"], "array")
        self.assertEqual(merged["items"]["type"], ["integer", "string"])

    def test_merge_schemas_numeric_constraints(self):
        a = {"type": "number", "minimum": 2, "maximum": 10}
        b = {"type": "number", "minimum": 5, "maximum": 8}
        merged = json_to_schema.merge_schemas(a, b)
        self.assertEqual(merged["minimum"], 2)
        self.assertEqual(merged["maximum"], 10)

    def test_merge_schemas_enum_union(self):
        a = {"type": "string", "enum": ["a", "b"]}
        b = {"type": "string", "enum": ["b", "c"]}
        merged = json_to_schema.merge_schemas(a, b)
        self.assertEqual(merged["enum"], ["a", "b", "c"])


class TestInferSchema(unittest.TestCase):
    def test_infer_schema_object(self):
        data = {"b": 1, "a": "x"}
        schema = json_to_schema.infer_schema(data)
        self.assertEqual(schema["type"], "object")
        self.assertEqual(sorted(schema["required"]), ["a", "b"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["a"]["type"], "string")
        self.assertEqual(schema["properties"]["b"]["type"], "integer")

    def test_infer_schema_empty_array(self):
        schema = json_to_schema.infer_schema([])
        self.assertEqual(schema, {"type": "array", "items": {}})

    def test_infer_schema_array_merge(self):
        schema = json_to_schema.infer_schema([1, "a", 2])
        self.assertEqual(schema["type"], "array")
        self.assertEqual(schema["items"]["type"], ["integer", "string"])

    def test_infer_schema_null(self):
        schema = json_to_schema.infer_schema(None)
        self.assertEqual(schema, {"type": "null"})

    def test_infer_schema_object_additional_properties_true(self):
        data = {"a": {"b": 1}}
        schema = json_to_schema.infer_schema(data, additional_properties=True)
        self.assertTrue(schema["additionalProperties"])
        self.assertTrue(schema["properties"]["a"]["additionalProperties"])

    def test_infer_schema_object_additional_properties_false(self):
        data = {"a": {"b": 1}}
        schema = json_to_schema.infer_schema(data, additional_properties=False)
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["a"]["additionalProperties"])

    def test_infer_schema_bounds_for_string_and_array(self):
        schema = json_to_schema.infer_schema(
            {"name": "abc", "tags": ["x", "yz"]},
            infer_all_bounds=True,
        )
        self.assertEqual(schema["properties"]["name"]["minLength"], 3)
        self.assertEqual(schema["properties"]["name"]["maxLength"], 3)
        self.assertEqual(schema["properties"]["tags"]["minItems"], 2)
        self.assertEqual(schema["properties"]["tags"]["maxItems"], 2)
        self.assertEqual(schema["properties"]["tags"]["items"]["minLength"], 1)
        self.assertEqual(schema["properties"]["tags"]["items"]["maxLength"], 2)

    def test_infer_schema_bounds_for_empty_array(self):
        schema = json_to_schema.infer_schema([], infer_all_bounds=True)
        self.assertEqual(schema["minItems"], 0)
        self.assertEqual(schema["maxItems"], 0)

    def test_infer_schema_enum_for_scalar_values(self):
        schema = json_to_schema.infer_schema([1, 2, 1], infer_all_enum=True)
        self.assertEqual(schema["items"]["enum"], [1, 2])

    def test_infer_schema_enum_and_bounds_together(self):
        schema = json_to_schema.infer_schema(
            ["aa", "b"],
            infer_all_enum=True,
            infer_all_bounds=True,
        )
        self.assertEqual(schema["items"]["enum"], ["aa", "b"])
        self.assertEqual(schema["items"]["minLength"], 1)
        self.assertEqual(schema["items"]["maxLength"], 2)

    def test_infer_schema_bounds_for_selected_fields_only(self):
        schema = json_to_schema.infer_schema(
            {"name": "abc", "status": "ok"},
            infer_bounds_fields={"name"},
        )
        self.assertEqual(schema["properties"]["name"]["minLength"], 3)
        self.assertEqual(schema["properties"]["name"]["maxLength"], 3)
        self.assertNotIn("minLength", schema["properties"]["status"])

    def test_infer_schema_enum_for_selected_fields_only(self):
        schema = json_to_schema.infer_schema(
            {"status": ["ok", "fail", "ok"], "kind": ["a", "b"]},
            infer_enum_fields={"status"},
        )
        self.assertEqual(schema["properties"]["status"]["items"]["enum"], ["ok", "fail"])
        self.assertNotIn("enum", schema["properties"]["kind"]["items"])


class TestFieldMetadataHelpers(unittest.TestCase):
    def test_resolve_field_schema_path_supports_nested_and_array_items(self):
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                }
            },
        }
        self.assertEqual(
            json_to_schema.resolve_field_schema_path(schema, "user.name"),
            {"type": "string"},
        )
        self.assertEqual(
            json_to_schema.resolve_field_schema_path(schema, "user.tags[]"),
            {"type": "string"},
        )

    def test_apply_field_metadata_updates_targeted_field(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        }
        json_to_schema.apply_field_metadata(
            schema,
            {"name": "Display name"},
            metadata_key="description",
        )
        self.assertEqual(schema["properties"]["name"]["description"], "Display name")


class TestValidateAgainstSchema(unittest.TestCase):
    def test_validate_against_schema_passes(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 2},
                "age": {"type": "integer", "minimum": 0},
            },
            "required": ["name", "age"],
            "additionalProperties": False,
        }
        payload = {"name": "Ada", "age": 22}
        self.assertEqual(json_to_schema.validate_against_schema(payload, schema), [])

    def test_validate_against_schema_reports_failures(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 3},
                "age": {"type": "integer", "minimum": 18},
            },
            "required": ["name", "age"],
            "additionalProperties": False,
        }
        payload = {"name": "Al", "age": 16, "extra": True}
        errors = json_to_schema.validate_against_schema(payload, schema)
        self.assertTrue(any("$.name: expected length >= 3" in error for error in errors))
        self.assertTrue(any("$.age: expected value >= 18" in error for error in errors))
        self.assertTrue(any("additional property 'extra' is not allowed" in error for error in errors))


class TestValidateSchemaDefinition(unittest.TestCase):
    def test_validate_schema_definition_accepts_boolean_schema(self):
        self.assertEqual(json_to_schema.validate_schema_definition(True), [])
        self.assertEqual(json_to_schema.validate_schema_definition(False), [])

    def test_validate_schema_definition_rejects_invalid_type(self):
        errors = json_to_schema.validate_schema_definition({"type": "wat"})
        self.assertTrue(any("unsupported type 'wat'" in error for error in errors))

    def test_validate_schema_definition_rejects_non_object_schema(self):
        errors = json_to_schema.validate_schema_definition("bad")
        self.assertEqual(errors, ["$: schema must be an object or boolean"])


class TestMain(unittest.TestCase):
    def test_main_reads_piped_stdin_when_input_not_specified(self):
        buf = io.StringIO()
        stdin = FakeStdin(json.dumps({"from_stdin": True}), is_tty=False)
        with patch.object(sys, "argv", ["json_to_schema.py"]):
            with patch.object(sys, "stdin", stdin):
                with redirect_stdout(buf):
                    json_to_schema.main()

        output = json.loads(buf.getvalue())
        self.assertEqual(output["$schema"], json_to_schema.SCHEMA_DRAFT)
        self.assertEqual(output["properties"]["from_stdin"]["type"], "boolean")

    def test_main_reads_stdin_even_if_default_file_exists(self):
        with TemporaryDirectory() as tmpdir:
            default_file = Path(tmpdir) / "file.json"
            default_file.write_text(json.dumps({"from_file": "value"}), encoding="utf-8")

            buf = io.StringIO()
            stdin = FakeStdin(json.dumps({"from_stdin": 123}), is_tty=False)
            with patch.object(sys, "argv", ["json_to_schema.py"]):
                with patch.object(sys, "stdin", stdin):
                    old_cwd = os.getcwd()
                    try:
                        os.chdir(tmpdir)
                        with redirect_stdout(buf):
                            json_to_schema.main()
                    finally:
                        os.chdir(old_cwd)

            output = json.loads(buf.getvalue())
            self.assertIn("from_stdin", output["properties"])
            self.assertNotIn("from_file", output["properties"])

    def test_main_prefers_explicit_input_file_over_stdin(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"from_file": 1}), encoding="utf-8")

            buf = io.StringIO()
            stdin = FakeStdin(json.dumps({"from_stdin": True}), is_tty=False)
            with patch.object(sys, "argv", ["json_to_schema.py", "-i", str(input_path)]):
                with patch.object(sys, "stdin", stdin):
                    with redirect_stdout(buf):
                        json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertIn("from_file", output["properties"])
            self.assertNotIn("from_stdin", output["properties"])

    def test_main_reads_default_file_when_stdin_is_tty(self):
        with TemporaryDirectory() as tmpdir:
            default_file = Path(tmpdir) / "file.json"
            default_file.write_text(json.dumps({"from_file": "value"}), encoding="utf-8")

            buf = io.StringIO()
            stdin = FakeStdin("", is_tty=True)
            with patch.object(sys, "argv", ["json_to_schema.py"]):
                with patch.object(sys, "stdin", stdin):
                    old_cwd = os.getcwd()
                    try:
                        os.chdir(tmpdir)
                        with redirect_stdout(buf):
                            json_to_schema.main()
                    finally:
                        os.chdir(old_cwd)

            output = json.loads(buf.getvalue())
            self.assertIn("from_file", output["properties"])

    def test_main_stdin_to_output_file(self):
        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "schema.json"
            stdin = FakeStdin(json.dumps({"from_stdin": True}), is_tty=False)
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-o", str(output_path)],
            ):
                with patch.object(sys, "stdin", stdin):
                    json_to_schema.main()

            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(output["$schema"], json_to_schema.SCHEMA_DRAFT)
            self.assertIn("from_stdin", output["properties"])

    def test_main_stdout(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"a": 1}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(sys, "argv", ["json_to_schema.py", "-i", str(input_path)]):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertEqual(output["$schema"], json_to_schema.SCHEMA_DRAFT)
            self.assertEqual(output["type"], "object")
            self.assertEqual(output["properties"]["a"]["type"], "integer")

    def test_main_output_file(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "schema.json"
            input_path.write_text(json.dumps(["x"]), encoding="utf-8")

            with patch.object(
                sys,
                "argv",
                [
                    "json_to_schema.py",
                    "-i",
                    str(input_path),
                    "-o",
                    str(output_path),
                ],
            ):
                json_to_schema.main()

            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(output["type"], "array")
            self.assertEqual(output["items"]["type"], "string")

    def test_main_stdout_minified(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"a": 1}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(sys, "argv", ["json_to_schema.py", "-i", str(input_path), "--minify"]):
                with redirect_stdout(buf):
                    json_to_schema.main()

            raw_output = buf.getvalue().strip()
            parsed = json.loads(raw_output)
            self.assertEqual(parsed["$schema"], json_to_schema.SCHEMA_DRAFT)
            self.assertEqual(parsed["properties"]["a"]["type"], "integer")
            self.assertNotIn("\n", raw_output)

    def test_main_output_file_minified(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "schema.json"
            input_path.write_text(json.dumps({"a": 1}), encoding="utf-8")

            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "-o", str(output_path), "--minify"],
            ):
                json_to_schema.main()

            raw_output = output_path.read_text(encoding="utf-8").strip()
            parsed = json.loads(raw_output)
            self.assertEqual(parsed["$schema"], json_to_schema.SCHEMA_DRAFT)
            self.assertNotIn("\n", raw_output)

    def test_main_missing_default_file_prints_friendly_error_when_stdin_is_tty(self):
        stdin = FakeStdin("", is_tty=True)
        with patch.object(sys, "argv", ["json_to_schema.py"]):
            with patch.object(sys, "stdin", stdin):
                with TemporaryDirectory() as tmpdir:
                    old_cwd = os.getcwd()
                    try:
                        os.chdir(tmpdir)
                        err = io.StringIO()
                        with self.assertRaises(SystemExit) as cm:
                            with redirect_stderr(err):
                                json_to_schema.main()
                        self.assertEqual(cm.exception.code, 2)
                        self.assertIn("Input file not found: file.json", err.getvalue())
                    finally:
                        os.chdir(old_cwd)

    def test_main_invalid_json_from_stdin_prints_friendly_error(self):
        stdin = FakeStdin("{invalid-json", is_tty=False)
        with patch.object(sys, "argv", ["json_to_schema.py"]):
            with patch.object(sys, "stdin", stdin):
                err = io.StringIO()
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stderr(err):
                        json_to_schema.main()
                self.assertEqual(cm.exception.code, 2)
                self.assertIn("Invalid JSON in stdin:", err.getvalue())
                self.assertIn("line 1, column 2", err.getvalue())

    def test_main_additional_properties_defaults_to_false(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"obj": {"x": 1}}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(sys, "argv", ["json_to_schema.py", "-i", str(input_path)]):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertFalse(output["additionalProperties"])
            self.assertFalse(output["properties"]["obj"]["additionalProperties"])

    def test_main_additional_properties_true(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"obj": {"x": 1}}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--additional-properties", "true"],
            ):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertTrue(output["additionalProperties"])
            self.assertTrue(output["properties"]["obj"]["additionalProperties"])

    def test_main_additional_properties_false(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"obj": {"x": 1}}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--additional-properties", "false"],
            ):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertFalse(output["additionalProperties"])
            self.assertFalse(output["properties"]["obj"]["additionalProperties"])

    def test_main_additional_properties_invalid_value_raises(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"a": 1}), encoding="utf-8")

            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--additional-properties", "maybe"],
            ):
                with self.assertRaises(SystemExit):
                    json_to_schema.main()

    def test_main_infer_bounds_for_selected_fields(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"name": "abc", "scores": [1, 3]}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--infer-bounds", "name"],
            ):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertEqual(output["properties"]["name"]["minLength"], 3)
            self.assertEqual(output["properties"]["name"]["maxLength"], 3)
            self.assertNotIn("minItems", output["properties"]["scores"])
            self.assertNotIn("minimum", output["properties"]["scores"]["items"])

    def test_main_infer_enum_for_selected_fields(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(
                json.dumps({"status": ["ok", "fail", "ok"], "kind": ["a", "b"]}),
                encoding="utf-8",
            )

            buf = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--infer-enum", "status"],
            ):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertEqual(output["properties"]["status"]["items"]["enum"], ["ok", "fail"])
            self.assertNotIn("enum", output["properties"]["kind"]["items"])

    def test_main_infer_all_bounds(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"name": "abc", "scores": [1, 3]}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--infer-all-bounds"],
            ):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertEqual(output["properties"]["name"]["minLength"], 3)
            self.assertEqual(output["properties"]["name"]["maxLength"], 3)
            self.assertEqual(output["properties"]["scores"]["minItems"], 2)
            self.assertEqual(output["properties"]["scores"]["maxItems"], 2)
            self.assertEqual(output["properties"]["scores"]["items"]["minimum"], 1)
            self.assertEqual(output["properties"]["scores"]["items"]["maximum"], 3)

    def test_main_infer_all_enum(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"status": ["ok", "fail", "ok"]}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--infer-all-enum"],
            ):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertEqual(output["properties"]["status"]["items"]["enum"], ["ok", "fail"])

    def test_main_applies_root_schema_metadata_flags(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"name": "Widget"}), encoding="utf-8")

            buf = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "json_to_schema.py",
                    "-i",
                    str(input_path),
                    "--schema-id",
                    "https://example.com/schemas/widget",
                    "--schema-title",
                    "Widget Schema",
                    "--schema-description",
                    "Schema for widget payloads",
                ],
            ):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertEqual(output["$id"], "https://example.com/schemas/widget")
            self.assertEqual(output["title"], "Widget Schema")
            self.assertEqual(output["description"], "Schema for widget payloads")

    def test_main_applies_field_metadata_flags(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(
                json.dumps(
                    {
                        "user": {"name": "Ada"},
                        "tags": ["one", "two"],
                    }
                ),
                encoding="utf-8",
            )

            buf = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "json_to_schema.py",
                    "-i",
                    str(input_path),
                    "--field-title",
                    "user.name=Full name",
                    "--field-description",
                    "tags[]=Tag value",
                ],
            ):
                with redirect_stdout(buf):
                    json_to_schema.main()

            output = json.loads(buf.getvalue())
            self.assertEqual(output["properties"]["user"]["properties"]["name"]["title"], "Full name")
            self.assertEqual(output["properties"]["tags"]["items"]["description"], "Tag value")

    def test_main_rejects_invalid_field_metadata_assignment(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"name": "Ada"}), encoding="utf-8")

            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--field-title", "name"],
            ):
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stderr(err):
                        json_to_schema.main()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("must be in FIELD=VALUE format", err.getvalue())

    def test_main_rejects_unknown_field_metadata_path(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            input_path.write_text(json.dumps({"name": "Ada"}), encoding="utf-8")

            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--field-description", "user.email=Email"],
            ):
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stderr(err):
                        json_to_schema.main()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("was not found in inferred schema", err.getvalue())

    def test_main_validate_success(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "payload.json"
            schema_path = Path(tmpdir) / "schema.json"
            input_path.write_text(json.dumps({"name": "Lin", "age": 5}), encoding="utf-8")
            schema_path.write_text(
                json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"},
                        },
                        "required": ["name", "age"],
                        "additionalProperties": False,
                    }
                ),
                encoding="utf-8",
            )

            out = io.StringIO()
            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--validate", str(schema_path)],
            ):
                with redirect_stdout(out), redirect_stderr(err):
                    json_to_schema.main()

            self.assertIn("Validation passed:", out.getvalue())
            self.assertIn(str(input_path), out.getvalue())
            self.assertIn(str(schema_path), out.getvalue())
            self.assertEqual(err.getvalue(), "")

    def test_main_validate_failure(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "payload.json"
            schema_path = Path(tmpdir) / "schema.json"
            input_path.write_text(json.dumps({"name": "Lin", "age": "bad"}), encoding="utf-8")
            schema_path.write_text(
                json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"},
                        },
                        "required": ["name", "age"],
                        "additionalProperties": False,
                    }
                ),
                encoding="utf-8",
            )

            out = io.StringIO()
            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--validate", str(schema_path)],
            ):
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stdout(out), redirect_stderr(err):
                        json_to_schema.main()

            self.assertEqual(cm.exception.code, 1)
            self.assertIn("Validation failed:", err.getvalue())
            self.assertIn("$.age: expected type integer, got string", err.getvalue())

    def test_main_validate_rejects_generation_output_flags(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "payload.json"
            schema_path = Path(tmpdir) / "schema.json"
            output_path = Path(tmpdir) / "out-schema.json"
            input_path.write_text(json.dumps({"name": "Lin", "age": 5}), encoding="utf-8")
            schema_path.write_text(
                json.dumps({"type": "object", "properties": {"name": {"type": "string"}}}),
                encoding="utf-8",
            )

            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "json_to_schema.py",
                    "-i",
                    str(input_path),
                    "-o",
                    str(output_path),
                    "--validate",
                    str(schema_path),
                ],
            ):
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stderr(err):
                        json_to_schema.main()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("--output cannot be used with --validate", err.getvalue())

    def test_main_validate_rejects_metadata_flags(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "payload.json"
            schema_path = Path(tmpdir) / "schema.json"
            input_path.write_text(json.dumps({"name": "Lin"}), encoding="utf-8")
            schema_path.write_text(
                json.dumps({"type": "object", "properties": {"name": {"type": "string"}}}),
                encoding="utf-8",
            )

            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                [
                    "json_to_schema.py",
                    "-i",
                    str(input_path),
                    "--validate",
                    str(schema_path),
                    "--field-title",
                    "name=Display name",
                ],
            ):
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stderr(err):
                        json_to_schema.main()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("--field-title cannot be used with --validate", err.getvalue())

    def test_main_validate_reads_payload_from_stdin(self):
        with TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "schema.json"
            schema_path.write_text(
                json.dumps({"type": "object", "properties": {"a": {"type": "integer"}}}),
                encoding="utf-8",
            )

            stdin = FakeStdin(json.dumps({"a": 1}), is_tty=False)
            out = io.StringIO()
            with patch.object(sys, "argv", ["json_to_schema.py", "--validate", str(schema_path)]):
                with patch.object(sys, "stdin", stdin):
                    with redirect_stdout(out):
                        json_to_schema.main()

            self.assertIn("Validation passed: stdin matches schema", out.getvalue())

    def test_main_validate_missing_schema_file_prints_friendly_error(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "payload.json"
            input_path.write_text(json.dumps({"a": 1}), encoding="utf-8")

            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--validate", str(Path(tmpdir) / "missing-schema.json")],
            ):
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stderr(err):
                        json_to_schema.main()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("Schema file not found:", err.getvalue())

    def test_main_validate_invalid_schema_json_prints_friendly_error(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "payload.json"
            schema_path = Path(tmpdir) / "schema.json"
            input_path.write_text(json.dumps({"a": 1}), encoding="utf-8")
            schema_path.write_text("{bad-json", encoding="utf-8")

            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--validate", str(schema_path)],
            ):
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stderr(err):
                        json_to_schema.main()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("Invalid JSON in schema file", err.getvalue())

    def test_main_validate_invalid_schema_definition(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "payload.json"
            schema_path = Path(tmpdir) / "schema.json"
            input_path.write_text(json.dumps({"a": 1}), encoding="utf-8")
            schema_path.write_text(json.dumps({"type": "wat"}), encoding="utf-8")

            err = io.StringIO()
            with patch.object(
                sys,
                "argv",
                ["json_to_schema.py", "-i", str(input_path), "--validate", str(schema_path)],
            ):
                with self.assertRaises(SystemExit) as cm:
                    with redirect_stderr(err):
                        json_to_schema.main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn(f"Invalid schema in {schema_path}:", err.getvalue())
            self.assertIn("unsupported type 'wat'", err.getvalue())


class TestFixtureDrivenSchemaInference(unittest.TestCase):
    CASES = [
        "user_profile",
        "events_mixed_array",
        "nullable_variants",
        "optional_keys_objects",
        "deep_nested_collections",
        "mixed_scalar_array",
    ]

    def _load_json(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_infer_schema_matches_expected_from_fixtures(self):
        for case in self.CASES:
            with self.subTest(case=case):
                input_path = FIXTURES_DIR / f"{case}.input.json"
                expected_path = FIXTURES_DIR / f"{case}.expected_schema.json"

                data = self._load_json(input_path)
                expected_schema = self._load_json(expected_path)
                actual_schema = json_to_schema.infer_schema(data)

                self.assertEqual(actual_schema, expected_schema)

    def test_main_generates_expected_schema_from_fixture_files(self):
        for case in self.CASES:
            with self.subTest(case=case):
                input_path = FIXTURES_DIR / f"{case}.input.json"
                expected_path = FIXTURES_DIR / f"{case}.expected_schema.json"

                expected_schema = {
                    "$schema": json_to_schema.SCHEMA_DRAFT,
                    **self._load_json(expected_path),
                }

                buf = io.StringIO()
                with patch.object(sys, "argv", ["json_to_schema.py", "-i", str(input_path)]):
                    with redirect_stdout(buf):
                        json_to_schema.main()

                actual_schema = json.loads(buf.getvalue())
                self.assertEqual(actual_schema, expected_schema)


if __name__ == "__main__":
    unittest.main()
