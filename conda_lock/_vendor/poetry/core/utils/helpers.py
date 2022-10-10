import os
import re
import shutil
import stat
import tempfile

from contextlib import contextmanager
from typing import Any
from typing import Iterator
from typing import List
from typing import Union

from conda_lock._vendor.poetry.core.utils._compat import Path
from conda_lock._vendor.poetry.core.version import Version


try:
    from collections.abc import Mapping
except ImportError:
    from collections import Mapping


_canonicalize_regex = re.compile(r"[-_]+")


def canonicalize_name(name):  # type: (str) -> str
    return _canonicalize_regex.sub("-", name).lower()


def module_name(name):  # type: (str) -> str
    return canonicalize_name(name).replace(".", "_").replace("-", "_")


def normalize_version(version):  # type: (str) -> str
    return str(Version(version))


@contextmanager
def temporary_directory(*args, **kwargs):  # type: (*Any, **Any) -> Iterator[str]
    name = tempfile.mkdtemp(*args, **kwargs)
    yield name
    safe_rmtree(name)


def parse_requires(requires):  # type: (str) -> List[str]
    lines = requires.split("\n")

    requires_dist = []
    in_section = False
    current_marker = None
    for line in lines:
        line = line.strip()
        if not line:
            if in_section:
                in_section = False

            continue

        if line.startswith("["):
            # extras or conditional dependencies
            marker = line.lstrip("[").rstrip("]")
            if ":" not in marker:
                extra, marker = marker, None
            else:
                extra, marker = marker.split(":")

            if extra:
                if marker:
                    marker = '{} and extra == "{}"'.format(marker, extra)
                else:
                    marker = 'extra == "{}"'.format(extra)

            if marker:
                current_marker = marker

            continue

        if current_marker:
            line = "{} ; {}".format(line, current_marker)

        requires_dist.append(line)

    return requires_dist


def _on_rm_error(func, path, exc_info):  # type: (Any, Union[str, Path], Any) -> None
    if not os.path.exists(path):
        return

    os.chmod(path, stat.S_IWRITE)
    func(path)


def safe_rmtree(path):  # type: (Union[str, Path]) -> None
    if Path(path).is_symlink():
        return os.unlink(str(path))

    shutil.rmtree(path, onerror=_on_rm_error)


def merge_dicts(d1, d2):  # type: (dict, dict) -> None
    for k, v in d2.items():
        if k in d1 and isinstance(d1[k], dict) and isinstance(d2[k], Mapping):
            merge_dicts(d1[k], d2[k])
        else:
            d1[k] = d2[k]
