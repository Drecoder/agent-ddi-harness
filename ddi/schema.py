"""A small JSON Schema validator: the subset the harness's own schemas use, standard library only.

Supported: boolean schemas, type, enum, const, required, properties, additionalProperties,
patternProperties, items, minItems, minLength, pattern, minimum, maximum. Unknown keywords are
an error in the schema, not silently ignored, so a schema cannot claim more than is checked.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"
KNOWN = {"$schema", "$id", "title", "description", "type", "enum", "const", "required",
         "properties", "additionalProperties", "patternProperties", "items", "minItems",
         "minLength", "pattern", "minimum", "maximum"}
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


class SchemaError(ValueError):
    pass


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, TYPES[t])


def errors(value, schema, path="$") -> list:
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: not allowed"]
    bad = set(schema) - KNOWN
    if bad:
        raise SchemaError(f"unsupported schema keyword(s) {sorted(bad)} at {path}")
    if "type" in schema:
        ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(value, t) for t in ts):
            return [f"{path}: expected {'/'.join(ts)}, got {type(value).__name__}"]
    return (_value(value, schema, path) + _string(value, schema, path) + _number(value, schema, path)
            + _array(value, schema, path) + _object(value, schema, path))


def _value(value, schema, path):
    out = []
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: must be {json.dumps(schema['const'])}, got {json.dumps(value)}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {json.dumps(value)} not in {json.dumps(schema['enum'])}")
    return out


def _string(value, schema, path):
    if not isinstance(value, str):
        return []
    out = []
    if len(value) < schema.get("minLength", 0):
        out.append(f"{path}: shorter than {schema['minLength']}")
    if "pattern" in schema and not re.search(schema["pattern"], value):
        out.append(f"{path}: does not match {schema['pattern']}")
    return out


def _number(value, schema, path):
    if not _is(value, "number"):
        return []
    out = []
    if "minimum" in schema and value < schema["minimum"]:
        out.append(f"{path}: below {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        out.append(f"{path}: above {schema['maximum']}")
    return out


def _array(value, schema, path):
    if not isinstance(value, list):
        return []
    out = [f"{path}: fewer than {schema['minItems']} items"] if len(value) < schema.get("minItems", 0) else []
    if "items" in schema:
        for i, v in enumerate(value):
            out += errors(v, schema["items"], f"{path}[{i}]")
    return out


def _object(value, schema, path):
    if not isinstance(value, dict):
        return []
    out = [f"{path}: missing {k}" for k in schema.get("required", []) if k not in value]
    props = schema.get("properties", {})
    for k, v in value.items():
        out += _property(k, v, schema, props, f"{path}.{k}")
    return out


def _property(key, value, schema, props, path):
    out, matched = [], False
    for pat, sub in schema.get("patternProperties", {}).items():
        if re.search(pat, key):
            matched = True
            out += errors(value, sub, path)
    if key in props:
        out += errors(value, props[key], path)
    elif not matched and "additionalProperties" in schema:
        out += errors(value, schema["additionalProperties"], path)
    return out


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


def check(value, name: str, what: str = "") -> None:
    errs = errors(value, load(name))
    if errs:
        raise SchemaError(f"{what or name}: " + "; ".join(errs[:8]))
