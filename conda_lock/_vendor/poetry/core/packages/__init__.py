import os
import re

from typing import List
from typing import Optional
from typing import Union

from conda_lock._vendor.poetry.core.semver import parse_constraint
from conda_lock._vendor.poetry.core.utils._compat import Path
from conda_lock._vendor.poetry.core.utils.patterns import wheel_file_re
from conda_lock._vendor.poetry.core.version.requirements import Requirement

from .dependency import Dependency
from .directory_dependency import DirectoryDependency
from .file_dependency import FileDependency
from .package import Package
from .project_package import ProjectPackage
from .url_dependency import URLDependency
from .utils.link import Link
from .utils.utils import convert_markers
from .utils.utils import group_markers
from .utils.utils import is_archive_file
from .utils.utils import is_installable_dir
from .utils.utils import is_url
from .utils.utils import path_to_url
from .utils.utils import strip_extras
from .utils.utils import url_to_path
from .vcs_dependency import VCSDependency


def _make_file_or_dir_dep(
    name,  # type: str
    path,  # type: Path
    base=None,  # type: Optional[Path]
    extras=None,  # type: Optional[List[str]]
):  # type: (...) -> Optional[Union[FileDependency, DirectoryDependency]]
    """
    Helper function to create a file or directoru dependency with the given arguments. If
    path is not a file or directory that exists, `None` is returned.
    """
    _path = path
    if not path.is_absolute() and base:
        # a base path was specified, so we should respect that
        _path = Path(base) / path

    if _path.is_file():
        return FileDependency(name, path, base=base, extras=extras)
    elif _path.is_dir():
        return DirectoryDependency(name, path, base=base, extras=extras)

    return None


def dependency_from_pep_508(
    name, relative_to=None
):  # type: (str, Optional[Path]) -> Dependency
    """
    Resolve a PEP-508 requirement string to a `Dependency` instance. If a `relative_to`
    path is specified, this is used as the base directory if the identified dependency is
    of file or directory type.
    """
    from conda_lock._vendor.poetry.core.vcs.git import ParsedUrl

    # Removing comments
    parts = name.split("#", 1)
    name = parts[0].strip()
    if len(parts) > 1:
        rest = parts[1]
        if " ;" in rest:
            name += " ;" + rest.split(" ;", 1)[1]

    req = Requirement(name)

    if req.marker:
        markers = convert_markers(req.marker)
    else:
        markers = {}

    name = req.name
    path = os.path.normpath(os.path.abspath(name))
    link = None

    if is_url(name):
        link = Link(name)
    elif req.url:
        link = Link(req.url)
    else:
        p, extras = strip_extras(path)
        if os.path.isdir(p) and (os.path.sep in name or name.startswith(".")):

            if not is_installable_dir(p):
                raise ValueError(
                    "Directory {!r} is not installable. File 'setup.py' "
                    "not found.".format(name)
                )
            link = Link(path_to_url(p))
        elif is_archive_file(p):
            link = Link(path_to_url(p))

    # it's a local file, dir, or url
    if link:
        is_file_uri = link.scheme == "file"
        is_relative_uri = is_file_uri and re.search(r"\.\./", link.url)

        # Handle relative file URLs
        if is_file_uri and is_relative_uri:
            path = Path(link.path)
            if relative_to:
                path = relative_to / path
            link = Link(path_to_url(path))

        # wheel file
        version = None
        if link.is_wheel:
            m = wheel_file_re.match(link.filename)
            if not m:
                raise ValueError("Invalid wheel name: {}".format(link.filename))
            name = m.group("name")
            version = m.group("ver")

        name = req.name or link.egg_fragment
        dep = None

        if link.scheme.startswith("git+"):
            url = ParsedUrl.parse(link.url)
            dep = VCSDependency(name, "git", url.url, rev=url.rev, extras=req.extras)
        elif link.scheme == "git":
            dep = VCSDependency(
                name, "git", link.url_without_fragment, extras=req.extras
            )
        elif link.scheme in ["http", "https"]:
            dep = URLDependency(name, link.url)
        elif is_file_uri:
            # handle RFC 8089 references
            path = url_to_path(req.url)
            dep = _make_file_or_dir_dep(
                name=name, path=path, base=relative_to, extras=req.extras
            )
        else:
            try:
                # this is a local path not using the file URI scheme
                dep = _make_file_or_dir_dep(
                    name=name, path=Path(req.url), base=relative_to, extras=req.extras,
                )
            except ValueError:
                pass

        if dep is None:
            dep = Dependency(name, version or "*", extras=req.extras)

        if version:
            dep._constraint = parse_constraint(version)
    else:
        if req.pretty_constraint:
            constraint = req.constraint
        else:
            constraint = "*"

        dep = Dependency(name, constraint, extras=req.extras)

    if "extra" in markers:
        # If we have extras, the dependency is optional
        dep.deactivate()

        for or_ in markers["extra"]:
            for _, extra in or_:
                dep.in_extras.append(extra)

    if "python_version" in markers:
        ors = []
        for or_ in markers["python_version"]:
            ands = []
            for op, version in or_:
                # Expand python version
                if op == "==" and "*" not in version:
                    version = "~" + version
                    op = ""
                elif op == "!=":
                    version += ".*"
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

        dep.python_versions = " || ".join(ors)

    if req.marker:
        dep.marker = req.marker

    return dep
