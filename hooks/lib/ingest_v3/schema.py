"""A stdlib validator for the JSON-Schema subset ingest v3's schema files use (the repo has no deps)."""
import json
import os

SCHEMAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas")


def load_schema(name):
    with open(os.path.join(SCHEMAS_DIR, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def _is_type(value, t):
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    if t == "string":
        return isinstance(value, str)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    raise ValueError("unsupported schema type {!r}".format(t))


def validate(instance, schema, _root=None, _where="$"):
    root = schema if _root is None else _root
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError("unsupported $ref {!r}".format(ref))
        return validate(instance, root["$defs"][ref[len("#/$defs/"):]], root, _where)
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(instance, x) for x in types):
            return ["{}: expected {}, got {}".format(_where, "/".join(types), type(instance).__name__)]
    errors = []
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("{}: {!r} is not one of {}".format(_where, instance, schema["enum"]))
    if isinstance(instance, str) and len(instance) < schema.get("minLength", 0):
        errors.append("{}: shorter than {} characters".format(_where, schema["minLength"]))
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("{}: missing required {!r}".format(_where, key))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, value in instance.items():
            where = "{}.{}".format(_where, key)
            if key in props:
                errors += validate(value, props[key], root, where)
            elif extra is False:
                errors.append("{}: unexpected property {!r}".format(_where, key))
            elif isinstance(extra, dict):
                errors += validate(value, extra, root, where)
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append("{}: fewer than {} items".format(_where, schema["minItems"]))
        if "items" in schema:
            for i, item in enumerate(instance):
                errors += validate(item, schema["items"], root, "{}[{}]".format(_where, i))
    return errors
