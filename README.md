# json-to-schema

Generate a JSON Schema (based on [draft 2020-12](https://json-schema.org/draft/2020-12)) from a sample JSON instance.

## Features

- Infers types for objects, arrays, and scalars.
- Merges array item types across elements.
- Marks object properties as required based on the input instance.
- Emits schemas compliant with JSON Schema draft 2020-12.

## Requirements

- Python 3.8+

## Usage

Provide an input JSON file (defaults to `file.json`) and print the inferred schema to stdout:

```bash
python json_to_schema.py
```

Specify a custom input file and write output to a schema file:

```bash
python json_to_schema.py -i input.json -o schema.json
```

## Example

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
python json_to_schema.py -i input.json -o schema.json
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
python -m unittest
```
