import json

from typing import Dict


def get_in(keys, nested_dict, default=None):
    """
    >>> foo = {'a': {'b': {'c': 1}}}
    >>> get_in(['a', 'b'], foo)
    {'c': 1}

    """
    import operator

    from functools import reduce

    try:
        return reduce(operator.getitem, keys, nested_dict)
    except (KeyError, IndexError, TypeError):
        return default


def read_file(filepath: str) -> str:
    with open(filepath, mode="r") as fp:
        return fp.read()


def write_file(obj: str, filepath: str) -> None:
    with open(filepath, mode="w") as fp:
        fp.write(obj)


def read_json(filepath: str) -> Dict:
    with open(filepath, mode="r") as fp:
        return json.load(fp)
