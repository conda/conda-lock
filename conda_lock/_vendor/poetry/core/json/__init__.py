from __future__ import annotations

import json

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import fastjsonschema

from fastjsonschema.exceptions import JsonSchemaException


if TYPE_CHECKING:
    from importlib.abc import Traversable


SCHEMA_DIR = Path(__file__).parent / "schemas"


def _get_schema_file(schema_name: str) -> Traversable:
    return files(__package__) / "schemas" / f"{schema_name}.json"


class ValidationError(ValueError):
    pass


def validate_object(obj: dict[str, Any], schema_name: str) -> list[str]:
    schema_file = _get_schema_file(schema_name)

    if not schema_file.is_file():
        raise ValueError(f"Schema {schema_name} does not exist.")

    with schema_file.open(encoding="utf-8") as f:
        schema = json.load(f)

    validate = fastjsonschema.compile(schema)

    errors = []
    try:
        validate(obj)
    except JsonSchemaException as e:
        errors = [e.message]

    return errors
