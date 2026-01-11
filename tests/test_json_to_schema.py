import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import json_to_schema


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


class TestMain(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
