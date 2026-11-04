from __future__ import annotations

import json

from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING
from typing import Any

import fastjsonschema

from fastjsonschema.exceptions import JsonSchemaException


if TYPE_CHECKING:
    from collections.abc import Callable


class ValidationError(ValueError):
    pass


@cache
def _get_validator(
    schema_name: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    schema_file = files(__package__) / "schemas" / f"{schema_name}.json"

    if not schema_file.is_file():
        raise ValueError(f"Schema {schema_name} does not exist.")

    with schema_file.open(encoding="utf-8") as f:
        schema = json.load(f)

    validator: Callable[[dict[str, Any]], dict[str, Any]] = fastjsonschema.compile(
        schema
    )
    return validator


def validate_object(obj: dict[str, Any], schema_name: str) -> list[str]:
    validate = _get_validator(schema_name)

    errors = []
    try:
        validate(obj)
    except JsonSchemaException as e:
        errors = [e.message]

    return errors
