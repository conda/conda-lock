from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import Optional
from typing import Union

from conda_lock._vendor.poetry.core.semver import VersionRange
from conda_lock._vendor.poetry.core.semver import parse_constraint
from conda_lock._vendor.poetry.core.version.markers import parse_marker


if TYPE_CHECKING:
    from . import (
        DirectoryDependency,
        FileDependency,
        URLDependency,
        VCSDependency,
        Dependency,
    )

from .package import Package
from .utils.utils import create_nested_marker


class ProjectPackage(Package):
    def __init__(
        self, name, version, pretty_version=None
    ):  # type: (str, Union[str, VersionRange], Optional[str]) -> None
        super(ProjectPackage, self).__init__(name, version, pretty_version)

        self.build_config = dict()
        self.packages = []
        self.include = []
        self.exclude = []
        self.custom_urls = {}

        if self._python_versions == "*":
            self._python_constraint = parse_constraint("~2.7 || >=3.4")

    @property
    def build_script(self):  # type: () -> Optional[str]
        return self.build_config.get("script")

    def is_root(self):  # type: () -> bool
        return True

    def to_dependency(
        self,
    ):  # type: () -> Union["DirectoryDependency", "FileDependency", "URLDependency", "VCSDependency", "Dependency"]
        dependency = super(ProjectPackage, self).to_dependency()

        dependency.is_root = True

        return dependency

    @property
    def python_versions(self):  # type: () -> Union[str, VersionRange]
        return self._python_versions

    @python_versions.setter
    def python_versions(self, value):  # type: (Union[str, VersionRange]) -> None
        self._python_versions = value

        if value == "*" or value == VersionRange():
            value = "~2.7 || >=3.4"

        self._python_constraint = parse_constraint(value)
        self._python_marker = parse_marker(
            create_nested_marker("python_version", self._python_constraint)
        )

    @property
    def urls(self):  # type: () -> Dict[str, Any]
        urls = super(ProjectPackage, self).urls

        urls.update(self.custom_urls)

        return urls

    def build_should_generate_setup(self):  # type: () -> bool
        return self.build_config.get("generate-setup-file", True)
