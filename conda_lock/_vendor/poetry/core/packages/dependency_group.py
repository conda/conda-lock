from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from packaging.utils import NormalizedName
from packaging.utils import canonicalize_name

from conda_lock._vendor.poetry.core.version.markers import parse_marker


if TYPE_CHECKING:
    from conda_lock._vendor.poetry.core.packages.dependency import Dependency
    from conda_lock._vendor.poetry.core.version.markers import BaseMarker

MAIN_GROUP: NormalizedName = canonicalize_name("main")


class DependencyGroup:
    def __init__(
        self, name: str, *, optional: bool = False, mixed_dynamic: bool = False
    ) -> None:
        self._name: NormalizedName = canonicalize_name(name)
        self._pretty_name: str = name
        self._optional: bool = optional
        self._mixed_dynamic = mixed_dynamic
        self._dependencies: list[Dependency] = []
        self._poetry_dependencies: list[Dependency] = []
        self._included_dependency_groups: dict[NormalizedName, DependencyGroup] = {}

    @property
    def name(self) -> NormalizedName:
        return self._name

    @property
    def pretty_name(self) -> str:
        return self._pretty_name

    @property
    def dependencies(self) -> list[Dependency]:
        dependencies = self._dependencies
        included_group_dependencies = self._resolve_included_dependency_groups(
            dependencies_for_locking=False
        )

        if not dependencies:
            # legacy mode
            dependencies = self._poetry_dependencies
        elif self._mixed_dynamic and self._poetry_dependencies:
            if all(dep.is_optional() for dep in self._dependencies):
                dependencies = [
                    *self._dependencies,
                    *(d for d in self._poetry_dependencies if not d.is_optional()),
                ]
            elif all(not dep.is_optional() for dep in self._dependencies):
                dependencies = [
                    *self._dependencies,
                    *(d for d in self._poetry_dependencies if d.is_optional()),
                ]

        return dependencies + included_group_dependencies

    @property
    def dependencies_for_locking(self) -> list[Dependency]:
        included_group_dependencies = self._resolve_included_dependency_groups(
            dependencies_for_locking=True
        )

        if not self._poetry_dependencies:
            dependencies = self._dependencies
        elif not self._dependencies:
            dependencies = self._poetry_dependencies
        else:
            poetry_dependencies_by_name = defaultdict(list)
            for dep in self._poetry_dependencies:
                poetry_dependencies_by_name[dep.name].append(dep)

            dependencies = []
            for dep in self.dependencies:
                if dep.name in poetry_dependencies_by_name:
                    enriched = False
                    dep_marker = dep.marker
                    if dep.in_extras:
                        dep_marker = dep.marker.intersect(
                            parse_marker(
                                " or ".join(
                                    f"extra == '{extra}'" for extra in dep.in_extras
                                )
                            )
                        )
                    for poetry_dep in poetry_dependencies_by_name[dep.name]:
                        marker = dep_marker.intersect(poetry_dep.marker)
                        if not marker.is_empty():
                            if marker == dep_marker:
                                marker = dep.marker
                            enriched = True
                            dependencies.append(
                                _enrich_dependency(dep, poetry_dep, marker)
                            )
                    if not enriched:
                        dependencies.append(dep)
                else:
                    dependencies.append(dep)

        return dependencies + included_group_dependencies

    def _resolve_included_dependency_groups(
        self, *, dependencies_for_locking: bool = False
    ) -> list[Dependency]:
        """Resolves and returns the dependencies from included dependency groups.

        This method iterates over all included dependency groups and collects
        their dependencies, associating them with the current group.
        """
        return [
            dependency.with_groups([self.name])
            for dependency_group in self._included_dependency_groups.values()
            for dependency in (
                dependency_group.dependencies_for_locking
                if dependencies_for_locking
                else dependency_group.dependencies
            )
        ]

    def is_optional(self) -> bool:
        return self._optional

    def add_dependency(self, dependency: Dependency) -> None:
        if not self._dependencies and self._poetry_dependencies:
            self._poetry_dependencies.append(dependency)
        else:
            self._dependencies.append(dependency)

    def add_poetry_dependency(self, dependency: Dependency) -> None:
        self._poetry_dependencies.append(dependency)

    def remove_dependency(self, name: str) -> None:
        from packaging.utils import canonicalize_name

        name = canonicalize_name(name)

        dependencies = []
        for dependency in self._dependencies:
            if dependency.name == name:
                continue
            dependencies.append(dependency)
        self._dependencies = dependencies

        dependencies = []
        for dependency in self._poetry_dependencies:
            if dependency.name == name:
                continue
            dependencies.append(dependency)
        self._poetry_dependencies = dependencies

    def include_dependency_group(self, dependency_group: DependencyGroup) -> None:
        if dependency_group.name == self.name:
            raise ValueError("Cannot include the dependency group to itself.")
        if dependency_group.name in self._included_dependency_groups:
            raise ValueError(
                f"Dependency group {dependency_group.pretty_name} is already included"
            )

        self._included_dependency_groups[dependency_group.name] = dependency_group

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DependencyGroup):
            return NotImplemented

        return (
            self._name == other.name
            and set(self._dependencies) == set(other._dependencies)
            and set(self._poetry_dependencies) == set(other._poetry_dependencies)
            and self._included_dependency_groups == other._included_dependency_groups
        )

    def __repr__(self) -> str:
        cls = self.__class__.__name__
        return (
            f"{cls}({self._pretty_name!r},"
            f" optional={self._optional},"
            f" mixed_dynamic={self._mixed_dynamic})"
        )


def _enrich_dependency(
    project_dependency: Dependency, poetry_dependency: Dependency, marker: BaseMarker
) -> Dependency:
    if (
        project_dependency.source_type is not None
        and poetry_dependency.source_type is not None
        and not poetry_dependency.is_same_source_as(project_dependency)
    ):
        raise ValueError(
            "Cannot enrich dependency with different sources: "
            f"{project_dependency} and {poetry_dependency}"
        )

    constraint = project_dependency.constraint.intersect(poetry_dependency.constraint)
    if constraint.is_empty():
        raise ValueError(
            "Cannot enrich dependency with incompatible constraints: "
            f"{project_dependency} and {poetry_dependency}"
        )

    if project_dependency.source_type is not None:
        from conda_lock._vendor.poetry.core.packages.directory_dependency import DirectoryDependency
        from conda_lock._vendor.poetry.core.packages.vcs_dependency import VCSDependency

        dependency = project_dependency.clone()
        if isinstance(project_dependency, (DirectoryDependency, VCSDependency)):
            dependency._develop = poetry_dependency._develop
    else:
        dependency = poetry_dependency.with_features(project_dependency.features)
        dependency._optional = project_dependency.is_optional()
        dependency._in_extras = project_dependency.in_extras

    dependency.constraint = constraint
    dependency.marker = marker

    return dependency
