import json
import os
import pathlib
import tempfile
import typing

from contextlib import contextmanager
from itertools import chain
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Sequence,
    TypeVar,
    Union,
)


T = TypeVar("T")


def get_in(
    keys: Sequence[Any], nested_dict: Mapping[Any, Any], default: Any = None
) -> Any:
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


def read_file(filepath: Union[str, pathlib.Path]) -> str:
    with open(filepath) as fp:
        return fp.read()


def write_file(obj: str, filepath: Union[str, pathlib.Path]) -> None:
    with open(filepath, mode="w") as fp:
        fp.write(obj)


@contextmanager
def temporary_file_with_contents(content: str) -> Iterator[pathlib.Path]:
    """Generate a temporary file with the given content.  This file can be used by subprocesses

    On Windows, NamedTemporaryFiles can't be opened a second time, so we have to close it first (and delete it manually later)
    """
    tf = tempfile.NamedTemporaryFile(delete=False)
    try:
        tf.close()
        write_file(content, tf.name)
        yield pathlib.Path(tf.name)
    finally:
        os.unlink(tf.name)


def read_json(filepath: Union[str, pathlib.Path]) -> Dict:
    with open(filepath) as fp:
        return json.load(fp)


def ordered_union(collections: Iterable[Iterable[T]]) -> List[T]:
    return list({k: k for k in chain.from_iterable(collections)}.values())


def relative_path(source: pathlib.Path, target: pathlib.Path) -> str:
    """
    Get posix representation of the relative path from `source` to `target`.
    Both `source` and `target` must exist on the filesystem.
    """
    common = pathlib.PurePath(
        os.path.commonpath((source.resolve(strict=True), target.resolve(strict=True)))
    )
    up = [".."] * len(source.resolve().relative_to(common).parents)
    down = target.resolve().relative_to(common).parts
    return str(pathlib.PurePosixPath(*up) / pathlib.PurePosixPath(*down))
