from typing import TYPE_CHECKING
from typing import List
from typing import Optional

from conda_lock._vendor.poetry.core.utils._compat import Path
from conda_lock._vendor.poetry.core.utils.helpers import canonicalize_name


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.packages import Dependency  # noqa


# TODO: Convert to dataclass once python 2.7, 3.5 is dropped
class BuildSystem:
    def __init__(
        self, build_backend=None, requires=None
    ):  # type: (Optional[str], Optional[List[str]]) -> None
        self.build_backend = (
            build_backend
            if build_backend is not None
            else "setuptools.build_meta:__legacy__"
        )
        self.requires = requires if requires is not None else ["setuptools", "wheel"]
        self._dependencies = None

    @property
    def dependencies(self):  # type: () -> List["Dependency"]
        if self._dependencies is None:
            # avoid circular dependency when loading DirectoryDependency
            from conda_lock._vendor.poetry.core.packages import DirectoryDependency
            from conda_lock._vendor.poetry.core.packages import FileDependency
            from conda_lock._vendor.poetry.core.packages import dependency_from_pep_508

            self._dependencies = []
            for requirement in self.requires:
                dependency = None
                try:
                    dependency = dependency_from_pep_508(requirement)
                except ValueError:
                    # PEP 517 requires can be path if not PEP 508
                    path = Path(requirement)
                    try:
                        if path.is_file():
                            dependency = FileDependency(
                                name=canonicalize_name(path.name), path=path
                            )
                        elif path.is_dir():
                            dependency = DirectoryDependency(
                                name=canonicalize_name(path.name), path=path
                            )
                    except OSError:
                        # compatibility Python < 3.8
                        # https://docs.python.org/3/library/pathlib.html#methods
                        pass

                if dependency is None:
                    # skip since we could not determine requirement
                    continue

                self._dependencies.append(dependency)

        return self._dependencies
