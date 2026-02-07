# json-to-schema

[![PyPI version](https://img.shields.io/pypi/v/json-to-schema.svg)](https://pypi.org/project/json-to-schema/)
[![Python versions](https://img.shields.io/pypi/pyversions/json-to-schema.svg)](https://pypi.org/project/json-to-schema/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

`json-to-schema` is a Python tool that infers a JSON Schema from JSON data.
Use it to generate JSON Schema definitions quickly from real payloads.
It outputs schemas compatible with [JSON Schema draft 2020-12](https://json-schema.org/draft/2020-12).

## Features

- Infers types for objects, arrays, and scalars.
- Merges array item types across elements.
- Marks object properties as required based on the input instance.
- Emits schemas compliant with JSON Schema draft 2020-12.

## Requirements

- Python 3.8+

## Install

```bash
pip install json-to-schema
```

## Generate JSON Schema from a JSON file (CLI)

Provide an input JSON file (defaults to `file.json`) and print the inferred schema to stdout:

```bash
json-to-schema
```

Specify a custom input file and write output to a schema file:

```bash
json-to-schema -i input.json -o schema.json
```

You can also pipe JSON directly into stdin:

```bash
echo '{"name":"Widget","price":12.5}' | json-to-schema
```

## Infer JSON Schema in Python code (library usage)

You can also import and use this package directly in Python applications:

```python
import json
from json_to_schema import infer_schema, SCHEMA_DRAFT

data = {
  "name": "Widget",
  "price": 12.5,
  "tags": ["sale", "featured"],
  "in_stock": True
}

schema = {
  "$schema": SCHEMA_DRAFT,
  **infer_schema(data)
}

print(json.dumps(schema, indent=2))
```

## Example: convert sample JSON to schema

Input (`input.json`):

```json
{
  "name": "Widget",
  "price": 12.5,
  "tags": ["sale", "featured"],
  "in_stock": true
}
```

Run:

```bash
json-to-schema -i input.json -o schema.json
```

Output (`schema.json`):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "name": { "type": "string" },
    "price": { "type": "number" },
    "tags": {
      "type": "array",
      "items": { "type": "string" }
    },
    "in_stock": { "type": "boolean" }
  },
  "required": ["in_stock", "name", "price", "tags"],
  "additionalProperties": false
}
```

## Testing

```bash
python -m unittest discover -s tests
```
