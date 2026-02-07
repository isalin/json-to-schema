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
