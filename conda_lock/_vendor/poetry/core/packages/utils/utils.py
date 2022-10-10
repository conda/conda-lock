import os
import posixpath
import re
import sys

from typing import TYPE_CHECKING
from typing import Dict
from typing import List
from typing import Tuple
from typing import Union

from six.moves.urllib.parse import unquote  # noqa
from six.moves.urllib.parse import urlsplit  # noqa
from six.moves.urllib.request import url2pathname  # noqa

from conda_lock._vendor.poetry.core.packages.constraints.constraint import Constraint
from conda_lock._vendor.poetry.core.packages.constraints.multi_constraint import MultiConstraint
from conda_lock._vendor.poetry.core.packages.constraints.union_constraint import UnionConstraint
from conda_lock._vendor.poetry.core.semver import EmptyConstraint
from conda_lock._vendor.poetry.core.semver import Version
from conda_lock._vendor.poetry.core.semver import VersionConstraint
from conda_lock._vendor.poetry.core.semver import VersionRange
from conda_lock._vendor.poetry.core.semver import VersionUnion
from conda_lock._vendor.poetry.core.semver import parse_constraint
from conda_lock._vendor.poetry.core.utils._compat import Path
from conda_lock._vendor.poetry.core.version.markers import BaseMarker
from conda_lock._vendor.poetry.core.version.markers import MarkerUnion
from conda_lock._vendor.poetry.core.version.markers import MultiMarker
from conda_lock._vendor.poetry.core.version.markers import SingleMarker


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.packages.constraints import BaseConstraint  # noqa
    from conda_lock._vendor.poetry.core.semver import VersionTypes  # noqa

BZ2_EXTENSIONS = (".tar.bz2", ".tbz")
XZ_EXTENSIONS = (".tar.xz", ".txz", ".tlz", ".tar.lz", ".tar.lzma")
ZIP_EXTENSIONS = (".zip", ".whl")
TAR_EXTENSIONS = (".tar.gz", ".tgz", ".tar")
ARCHIVE_EXTENSIONS = ZIP_EXTENSIONS + BZ2_EXTENSIONS + TAR_EXTENSIONS + XZ_EXTENSIONS
SUPPORTED_EXTENSIONS = ZIP_EXTENSIONS + TAR_EXTENSIONS

try:
    import bz2  # noqa

    SUPPORTED_EXTENSIONS += BZ2_EXTENSIONS
except ImportError:
    pass

try:
    # Only for Python 3.3+
    import lzma  # noqa

    SUPPORTED_EXTENSIONS += XZ_EXTENSIONS
except ImportError:
    pass


def path_to_url(path):  # type: (Union[str, Path]) -> str
    """
    Convert a path to a file: URL.  The path will be made absolute unless otherwise
    specified and have quoted path parts.
    """
    return Path(path).absolute().as_uri()


def url_to_path(url):  # type: (str) -> Path
    """
    Convert an RFC8089 file URI to path.

    The logic used here is borrowed from pip
    https://github.com/pypa/pip/blob/4d1932fcdd1974c820ea60b3286984ebb0c3beaa/src/pip/_internal/utils/urls.py#L31
    """
    if not url.startswith("file:"):
        raise ValueError("{} is not a valid file URI".format(url))

    _, netloc, path, _, _ = urlsplit(url)

    if not netloc or netloc == "localhost":
        # According to RFC 8089, same as empty authority.
        netloc = ""
    elif netloc not in {".", ".."} and sys.platform == "win32":
        # If we have a UNC path, prepend UNC share notation.
        netloc = "\\\\" + netloc
    else:
        raise ValueError(
            "non-local file URIs are not supported on this platform: {}".format(url)
        )

    return Path(url2pathname(netloc + unquote(path)))


def is_url(name):  # type: (str) -> bool
    if ":" not in name:
        return False
    scheme = name.split(":", 1)[0].lower()

    return scheme in [
        "http",
        "https",
        "file",
        "ftp",
        "ssh",
        "git",
        "hg",
        "bzr",
        "sftp",
        "svn",
        "ssh",
    ]


def strip_extras(path):  # type: (str) -> Tuple[str, str]
    m = re.match(r"^(.+)(\[[^\]]+\])$", path)
    extras = None
    if m:
        path_no_extras = m.group(1)
        extras = m.group(2)
    else:
        path_no_extras = path

    return path_no_extras, extras


def is_installable_dir(path):  # type: (str) -> bool
    """Return True if `path` is a directory containing a setup.py file."""
    if not os.path.isdir(path):
        return False
    setup_py = os.path.join(path, "setup.py")
    if os.path.isfile(setup_py):
        return True
    return False


def is_archive_file(name):  # type: (str) -> bool
    """Return True if `name` is a considered as an archive file."""
    ext = splitext(name)[1].lower()
    if ext in ARCHIVE_EXTENSIONS:
        return True
    return False


def splitext(path):  # type: (str) -> Tuple[str, str]
    """Like os.path.splitext, but take off .tar too"""
    base, ext = posixpath.splitext(path)
    if base.lower().endswith(".tar"):
        ext = base[-4:] + ext
        base = base[:-4]
    return base, ext


def group_markers(
    markers, or_=False
):  # type: (List[BaseMarker], bool) -> List[Union[Tuple[str, str, str], List[Tuple[str, str, str]]]]
    groups = [[]]

    for marker in markers:
        if or_:
            groups.append([])

        if isinstance(marker, (MultiMarker, MarkerUnion)):
            groups[-1].append(
                group_markers(marker.markers, isinstance(marker, MarkerUnion))
            )
        elif isinstance(marker, SingleMarker):
            lhs, op, rhs = marker.name, marker.operator, marker.value

            groups[-1].append((lhs, op, rhs))

    return groups


def convert_markers(marker):  # type: (BaseMarker) -> Dict[str, List[Tuple[str, str]]]
    groups = group_markers([marker])

    requirements = {}

    def _group(
        _groups, or_=False
    ):  # type: (List[Union[Tuple[str, str, str], List[Tuple[str, str, str]]]], bool) -> None
        ors = {}
        for group in _groups:
            if isinstance(group, list):
                _group(group, or_=True)
            else:
                variable, op, value = group
                group_name = str(variable)

                # python_full_version is equivalent to python_version
                # for Poetry so we merge them
                if group_name == "python_full_version":
                    group_name = "python_version"

                if group_name not in requirements:
                    requirements[group_name] = []

                if group_name not in ors:
                    ors[group_name] = or_

                if ors[group_name] or not requirements[group_name]:
                    requirements[group_name].append([])

                requirements[group_name][-1].append((str(op), str(value)))

                ors[group_name] = False

    _group(groups, or_=True)

    return requirements


def create_nested_marker(
    name, constraint
):  # type: (str, Union["BaseConstraint", VersionUnion, Version, VersionConstraint]) -> str
    if constraint.is_any():
        return ""

    if isinstance(constraint, (MultiConstraint, UnionConstraint)):
        parts = []
        for c in constraint.constraints:
            multi = False
            if isinstance(c, (MultiConstraint, UnionConstraint)):
                multi = True

            parts.append((multi, create_nested_marker(name, c)))

        glue = " and "
        if isinstance(constraint, UnionConstraint):
            parts = ["({})".format(part[1]) if part[0] else part[1] for part in parts]
            glue = " or "
        else:
            parts = [part[1] for part in parts]

        marker = glue.join(parts)
    elif isinstance(constraint, Constraint):
        marker = '{} {} "{}"'.format(name, constraint.operator, constraint.version)
    elif isinstance(constraint, VersionUnion):
        parts = []
        for c in constraint.ranges:
            parts.append(create_nested_marker(name, c))

        glue = " or "
        parts = ["({})".format(part) for part in parts]

        marker = glue.join(parts)
    elif isinstance(constraint, Version):
        if name == "python_version" and constraint.precision >= 3:
            name = "python_full_version"

        marker = '{} == "{}"'.format(name, constraint.text)
    else:
        if constraint.min is not None:
            op = ">="
            if not constraint.include_min:
                op = ">"

            version = constraint.min
            if constraint.max is not None:
                min_name = max_name = name
                if min_name == "python_version" and constraint.min.precision >= 3:
                    min_name = "python_full_version"

                if max_name == "python_version" and constraint.max.precision >= 3:
                    max_name = "python_full_version"

                text = '{} {} "{}"'.format(min_name, op, version)

                op = "<="
                if not constraint.include_max:
                    op = "<"

                version = constraint.max

                text += ' and {} {} "{}"'.format(max_name, op, version)

                return text
        elif constraint.max is not None:
            op = "<="
            if not constraint.include_max:
                op = "<"

            version = constraint.max
        else:
            return ""

        if name == "python_version" and version.precision >= 3:
            name = "python_full_version"

        marker = '{} {} "{}"'.format(name, op, version)

    return marker


def get_python_constraint_from_marker(marker,):  # type: (BaseMarker) -> "VersionTypes"
    python_marker = marker.only("python_version", "python_full_version")
    if python_marker.is_any():
        return VersionRange()

    if python_marker.is_empty():
        return EmptyConstraint()

    markers = convert_markers(marker)

    ors = []
    for or_ in markers["python_version"]:
        ands = []
        for op, version in or_:
            # Expand python version
            if op == "==":
                version = "~" + version
                op = ""
            elif op == "!=":
                version += ".*"
            elif op in ("<=", ">"):
                parsed_version = Version.parse(version)
                if parsed_version.precision == 1:
                    if op == "<=":
                        op = "<"
                        version = parsed_version.next_major.text
                    elif op == ">":
                        op = ">="
                        version = parsed_version.next_major.text
                elif parsed_version.precision == 2:
                    if op == "<=":
                        op = "<"
                        version = parsed_version.next_minor.text
                    elif op == ">":
                        op = ">="
                        version = parsed_version.next_minor.text
            elif op in ("in", "not in"):
                versions = []
                for v in re.split("[ ,]+", version):
                    split = v.split(".")
                    if len(split) in [1, 2]:
                        split.append("*")
                        op_ = "" if op == "in" else "!="
                    else:
                        op_ = "==" if op == "in" else "!="

                    versions.append(op_ + ".".join(split))

                glue = " || " if op == "in" else ", "
                if versions:
                    ands.append(glue.join(versions))

                continue

            ands.append("{}{}".format(op, version))

        ors.append(" ".join(ands))

    return parse_constraint(" || ".join(ors))
