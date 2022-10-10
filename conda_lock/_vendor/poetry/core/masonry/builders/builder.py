# -*- coding: utf-8 -*-
import logging
import re
import shutil
import sys
import tempfile

from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Union

from conda_lock._vendor.poetry.core.utils._compat import Path
from conda_lock._vendor.poetry.core.utils._compat import to_str
from conda_lock._vendor.poetry.core.vcs import get_vcs

from ..metadata import Metadata
from ..utils.module import Module
from ..utils.package_include import PackageInclude


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.poetry import Poetry  # noqa


AUTHOR_REGEX = re.compile(r"(?u)^(?P<name>[- .,\w\d'’\"()]+) <(?P<email>.+?)>$")

METADATA_BASE = """\
Metadata-Version: 2.1
Name: {name}
Version: {version}
Summary: {summary}
"""

logger = logging.getLogger(__name__)


class Builder(object):
    format = None  # type: Optional[str]

    def __init__(
        self, poetry, ignore_packages_formats=False, executable=None
    ):  # type: ("Poetry", bool, Optional[Union[Path, str]]) -> None
        self._poetry = poetry
        self._package = poetry.package
        self._path = poetry.file.parent
        self._excluded_files = None  # type: Optional[Set[str]]
        self._executable = Path(executable or sys.executable)

        packages = []
        for p in self._package.packages:
            formats = p.get("format", [])
            if not isinstance(formats, list):
                formats = [formats]

            if (
                formats
                and self.format
                and self.format not in formats
                and not ignore_packages_formats
            ):
                continue

            packages.append(p)

        includes = []
        for include in self._package.include:
            formats = include.get("format", [])

            if (
                formats
                and self.format
                and self.format not in formats
                and not ignore_packages_formats
            ):
                continue

            includes.append(include)

        self._module = Module(
            self._package.name,
            self._path.as_posix(),
            packages=packages,
            includes=includes,
        )

        self._meta = Metadata.from_package(self._package)

    @property
    def executable(self):  # type: () -> Path
        return self._executable

    def build(self):  # type: () -> None
        raise NotImplementedError()

    def find_excluded_files(self):  # type: () -> Set[str]
        if self._excluded_files is None:
            # Checking VCS
            vcs = get_vcs(self._path)
            if not vcs:
                vcs_ignored_files = set()
            else:
                vcs_ignored_files = set(vcs.get_ignored_files())

            explicitely_excluded = set()
            for excluded_glob in self._package.exclude:
                for excluded in self._path.glob(str(excluded_glob)):
                    explicitely_excluded.add(
                        Path(excluded).relative_to(self._path).as_posix()
                    )

            explicitely_included = set()
            for inc in self._package.include:
                included_glob = inc["path"]
                for included in self._path.glob(str(included_glob)):
                    explicitely_included.add(
                        Path(included).relative_to(self._path).as_posix()
                    )

            ignored = (vcs_ignored_files | explicitely_excluded) - explicitely_included
            result = set()
            for file in ignored:
                result.add(file)

            # The list of excluded files might be big and we will do a lot
            # containment check (x in excluded).
            # Returning a set make those tests much much faster.
            self._excluded_files = result

        return self._excluded_files

    def is_excluded(self, filepath):  # type: (Union[str, Path]) -> bool
        exclude_path = Path(filepath)

        while True:
            if exclude_path.as_posix() in self.find_excluded_files():
                return True

            if len(exclude_path.parts) > 1:
                exclude_path = exclude_path.parent
            else:
                break

        return False

    def find_files_to_add(
        self, exclude_build=True
    ):  # type: (bool) -> Set[BuildIncludeFile]
        """
        Finds all files to add to the tarball
        """
        to_add = set()

        for include in self._module.includes:
            include.refresh()
            formats = include.formats or ["sdist"]

            for file in include.elements:
                if "__pycache__" in str(file):
                    continue

                if file.is_dir():
                    if self.format in formats:
                        for current_file in file.glob("**/*"):
                            include_file = BuildIncludeFile(
                                path=current_file,
                                project_root=self._path,
                                source_root=self._path,
                            )

                            if not current_file.is_dir() and not self.is_excluded(
                                include_file.relative_to_source_root()
                            ):
                                to_add.add(include_file)
                    continue

                if (
                    isinstance(include, PackageInclude)
                    and include.source
                    and self.format == "wheel"
                ):
                    source_root = include.base
                else:
                    source_root = self._path

                include_file = BuildIncludeFile(
                    path=file, project_root=self._path, source_root=source_root
                )

                if self.is_excluded(
                    include_file.relative_to_project_root()
                ) and isinstance(include, PackageInclude):
                    continue

                if file.suffix == ".pyc":
                    continue

                if file in to_add:
                    # Skip duplicates
                    continue

                logger.debug("Adding: {}".format(str(file)))
                to_add.add(include_file)

        # add build script if it is specified and explicitly required
        if self._package.build_script and not exclude_build:
            to_add.add(
                BuildIncludeFile(
                    path=self._package.build_script,
                    project_root=self._path,
                    source_root=self._path,
                )
            )

        return to_add

    def get_metadata_content(self):  # type: () -> str
        content = METADATA_BASE.format(
            name=self._meta.name,
            version=self._meta.version,
            summary=to_str(self._meta.summary),
        )

        # Optional fields
        if self._meta.home_page:
            content += "Home-page: {}\n".format(self._meta.home_page)

        if self._meta.license:
            content += "License: {}\n".format(self._meta.license)

        if self._meta.keywords:
            content += "Keywords: {}\n".format(self._meta.keywords)

        if self._meta.author:
            content += "Author: {}\n".format(to_str(self._meta.author))

        if self._meta.author_email:
            content += "Author-email: {}\n".format(to_str(self._meta.author_email))

        if self._meta.maintainer:
            content += "Maintainer: {}\n".format(to_str(self._meta.maintainer))

        if self._meta.maintainer_email:
            content += "Maintainer-email: {}\n".format(
                to_str(self._meta.maintainer_email)
            )

        if self._meta.requires_python:
            content += "Requires-Python: {}\n".format(self._meta.requires_python)

        for classifier in self._meta.classifiers:
            content += "Classifier: {}\n".format(classifier)

        for extra in sorted(self._meta.provides_extra):
            content += "Provides-Extra: {}\n".format(extra)

        for dep in sorted(self._meta.requires_dist):
            content += "Requires-Dist: {}\n".format(dep)

        for url in sorted(self._meta.project_urls, key=lambda u: u[0]):
            content += "Project-URL: {}\n".format(to_str(url))

        if self._meta.description_content_type:
            content += "Description-Content-Type: {}\n".format(
                self._meta.description_content_type
            )

        if self._meta.description is not None:
            content += "\n" + to_str(self._meta.description) + "\n"

        return content

    def convert_entry_points(self):  # type: () -> Dict[str, List[str]]
        result = defaultdict(list)

        # Scripts -> Entry points
        for name, ep in self._poetry.local_config.get("scripts", {}).items():
            extras = ""
            if isinstance(ep, dict):
                extras = "[{}]".format(", ".join(ep["extras"]))
                ep = ep["callable"]

            result["console_scripts"].append("{} = {}{}".format(name, ep, extras))

        # Plugins -> entry points
        plugins = self._poetry.local_config.get("plugins", {})
        for groupname, group in plugins.items():
            for name, ep in sorted(group.items()):
                result[groupname].append("{} = {}".format(name, ep))

        for groupname in result:
            result[groupname] = sorted(result[groupname])

        return dict(result)

    @classmethod
    def convert_author(cls, author):  # type: (str) -> Dict[str, str]
        m = AUTHOR_REGEX.match(author)

        name = m.group("name")
        email = m.group("email")

        return {"name": name, "email": email}

    @classmethod
    @contextmanager
    def temporary_directory(cls, *args, **kwargs):  # type: (*Any, **Any) -> None
        try:
            from tempfile import TemporaryDirectory

            with TemporaryDirectory(*args, **kwargs) as name:
                yield name
        except ImportError:
            name = tempfile.mkdtemp(*args, **kwargs)

            yield name

            shutil.rmtree(name)


class BuildIncludeFile:
    def __init__(
        self,
        path,  # type: Union[Path, str]
        project_root,  # type: Union[Path, str]
        source_root=None,  # type: Optional[Union[Path, str]]
    ):
        """
        :param project_root: the full path of the project's root
        :param path: a full path to the file to be included
        :param source_root: the root path to resolve to
        """
        self.path = Path(path)
        self.project_root = Path(project_root).resolve()
        self.source_root = None if not source_root else Path(source_root).resolve()
        if not self.path.is_absolute() and self.source_root:
            self.path = self.source_root / self.path
        else:
            self.path = self.path

        try:
            self.path = self.path.resolve()
        except FileNotFoundError:
            # this is an issue in in python 3.5, since resolve uses strict=True by
            # default, this workaround needs to be maintained till python 2.7 and
            # python 3.5 are dropped, until we can use resolve(strict=False).
            pass

    def __eq__(self, other):  # type: (Union[BuildIncludeFile, Path]) -> bool
        if hasattr(other, "path"):
            return self.path == other.path
        return self.path == other

    def __ne__(self, other):  # type: (Union[BuildIncludeFile, Path]) -> bool
        return not self.__eq__(other)

    def __hash__(self):  # type: () -> int
        return hash(self.path)

    def __repr__(self):  # type: () -> str
        return str(self.path)

    def relative_to_project_root(self):  # type: () -> Path
        return self.path.relative_to(self.project_root)

    def relative_to_source_root(self):  # type: () -> Path
        if self.source_root is not None:
            return self.path.relative_to(self.source_root)
        return self.path
