from __future__ import annotations

import logging

from collections import defaultdict
from collections.abc import Mapping
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from packaging.licenses import InvalidLicenseExpression
from packaging.licenses import canonicalize_license_expression
from packaging.utils import canonicalize_name

from conda_lock._vendor.poetry.core.packages.dependency import Dependency
from conda_lock._vendor.poetry.core.packages.dependency_group import DependencyGroup
from conda_lock._vendor.poetry.core.utils.helpers import combine_unicode
from conda_lock._vendor.poetry.core.utils.helpers import readme_content_type


if TYPE_CHECKING:
    from packaging.utils import NormalizedName

    from conda_lock._vendor.poetry.core.packages.project_package import ProjectPackage
    from conda_lock._vendor.poetry.core.poetry import Poetry
    from conda_lock._vendor.poetry.core.pyproject.toml import PyProjectTOML

    DependencyConstraint = str | Mapping[str, Any]
    DependencyConfig = Mapping[str, list[DependencyConstraint] | DependencyConstraint]


logger = logging.getLogger(__name__)


class Factory:
    """
    Factory class to create various elements needed by Poetry.
    """

    def create_poetry(
        self, cwd: Path | None = None, with_groups: bool = True
    ) -> Poetry:
        from conda_lock._vendor.poetry.core.poetry import Poetry
        from conda_lock._vendor.poetry.core.pyproject.toml import PyProjectTOML

        poetry_file = self.locate(cwd)
        pyproject = PyProjectTOML(path=poetry_file)

        # Checking validity
        check_result = self.validate(pyproject.data)
        if check_result["errors"]:
            message = ""
            for error in check_result["errors"]:
                message += f"  - {error}\n"

            raise RuntimeError("The Poetry configuration is invalid:\n" + message)

        for warning in check_result["warnings"]:
            logger.warning(warning)

        # Load package
        # If name or version were missing in package mode, we would have already
        # raised an error, so we can safely assume they might only be missing
        # in non-package mode and use some dummy values in this case.
        project = pyproject.data.get("project", {})
        name = project.get("name") or pyproject.poetry_config.get(
            "name", "non-package-mode"
        )
        assert isinstance(name, str)
        version = project.get("version") or pyproject.poetry_config.get("version", "0")
        assert isinstance(version, str)
        package = self.get_package(name, version)
        self.configure_package(
            package, pyproject, poetry_file.parent, with_groups=with_groups
        )

        return Poetry(poetry_file, pyproject.poetry_config, package)

    @classmethod
    def get_package(cls, name: str, version: str) -> ProjectPackage:
        from conda_lock._vendor.poetry.core.packages.project_package import ProjectPackage

        return ProjectPackage(name, version)

    @classmethod
    def _add_package_pep735_group_dependencies(
        cls,
        package: ProjectPackage,
        group: DependencyGroup,
        dependencies: list[str | dict[str, str]],
    ) -> list[str]:
        group_includes = []
        for constraint in dependencies:
            if isinstance(constraint, str):
                dep = Dependency.create_from_pep_508(
                    constraint,
                    relative_to=package.root_dir,
                    groups=[group.pretty_name],
                )
                group.add_dependency(dep)
            elif include := constraint.get("include-group"):
                group_includes.append(include)
        return group_includes

    @classmethod
    def _add_package_poetry_group_dependencies(
        cls,
        package: ProjectPackage,
        group: str | DependencyGroup,
        dependencies: DependencyConfig,
    ) -> None:
        from conda_lock._vendor.poetry.core.packages.dependency_group import MAIN_GROUP

        if isinstance(group, str):
            if package.has_dependency_group(group):
                group = package.dependency_group(group)
            else:
                from conda_lock._vendor.poetry.core.packages.dependency_group import DependencyGroup

                group = DependencyGroup(group)

        for name, constraints in dependencies.items():
            _constraints = (
                constraints if isinstance(constraints, list) else [constraints]
            )
            for _constraint in _constraints:
                if name.lower() == "python":
                    if group.name == MAIN_GROUP and isinstance(_constraint, str):
                        package.python_versions = _constraint
                    continue

                group.add_poetry_dependency(
                    cls.create_dependency(
                        name,
                        _constraint,
                        groups=[group.name],
                        root_dir=package.root_dir,
                    )
                )

        package.add_dependency_group(group)

    @classmethod
    def configure_package(
        cls,
        package: ProjectPackage,
        pyproject: PyProjectTOML,
        root: Path,
        with_groups: bool = True,
    ) -> None:
        project = pyproject.data.get("project", {})
        tool_poetry = pyproject.poetry_config
        dependency_groups = pyproject.data.get("dependency-groups", {})

        package.root_dir = root

        cls._configure_package_metadata(package, project, tool_poetry, root)
        cls._configure_entry_points(package, project, tool_poetry)
        cls._configure_package_dependencies(
            package=package,
            project=project,
            tool_poetry=tool_poetry,
            dependency_groups=dependency_groups,
            with_groups=with_groups,
        )
        cls._configure_package_poetry_specifics(package, tool_poetry)

    @classmethod
    def _configure_package_metadata(
        cls,
        package: ProjectPackage,
        project: dict[str, Any],
        tool_poetry: dict[str, Any],
        root: Path,
    ) -> None:
        from conda_lock._vendor.poetry.core.spdx.helpers import license_by_id

        for key in ("authors", "maintainers"):
            if entries := project.get(key):
                participants = []
                for entry in entries:
                    name, email = entry.get("name"), entry.get("email")
                    if name and email:
                        participants.append(combine_unicode(f"{name} <{email}>"))
                    elif name:
                        participants.append(combine_unicode(name))
                    else:
                        participants.append(combine_unicode(email))
            else:
                participants = [
                    combine_unicode(author) for author in tool_poetry.get(key, [])
                ]
            if key == "authors":
                package.authors = participants
            else:
                package.maintainers = participants

        package.description = project.get("description") or tool_poetry.get(
            "description", ""
        )
        raw_license: str | None = None
        if project_license := project.get("license"):
            if isinstance(project_license, str):
                try:
                    package.license_expression = canonicalize_license_expression(
                        project_license
                    )
                except InvalidLicenseExpression:
                    # This is handled in validate().
                    raw_license = project_license
            else:
                # Table values for the license key in the [project] table,
                # including the text and file table subkeys, are now deprecated.
                # If the new license-files key is present, build tools MUST raise an
                # error if the license key is defined and has a value other
                # than a single top-level string.
                # https://peps.python.org/pep-0639/#deprecate-license-key-table-subkeys
                if "license-files" in project:
                    raise ValueError(
                        "[project.license] must be of type string"
                        " if [project.license-files] is defined."
                    )

                # Tools MUST NOT use the contents of the license.text [project] key
                # (or equivalent tool-specific format), [...] to fill [...] the Core
                # Metadata License-Expression field without informing the user and
                # requiring unambiguous, affirmative user action to select and confirm
                # the desired license expression value before proceeding.
                # https://peps.python.org/pep-0639/#converting-legacy-metadata
                # -> We just set the old license field in this case
                #    (and give a warning in validate).
                raw_license = project_license.get("text")
                if not raw_license and (license_file := project_license.get("file")):
                    # If the specified license file is present in the source tree,
                    # build tools SHOULD use it to fill the License-File field
                    # in the core metadata, and MUST include the specified file
                    # as if it were specified in a license-file field.
                    # If the file does not exist at the specified path,
                    # tools MUST raise an informative error as previously specified.
                    # https://peps.python.org/pep-0639/#deprecate-license-key-table-subkeys
                    license_path = (root / license_file).absolute()
                    try:
                        raw_license = Path(license_path).read_text(encoding="utf-8")
                    except FileNotFoundError as e:
                        raise FileNotFoundError(
                            f"Poetry: license file '{license_path}' not found"
                        ) from e
                    else:
                        # explicitly not a tuple to allow default handling
                        # to find additional license files later
                        package.license_files = Path(license_file)
        else:
            raw_license = tool_poetry.get("license")
        if raw_license:
            package.license = license_by_id(raw_license)

        # important: distinction between empty array and None:
        # - empty array: explicitly no license files
        # - None (not set): default handling allowed
        if (license_files := project.get("license-files")) is not None:
            # Build tools MUST treat each value as a glob pattern,
            # and MUST raise an error if the pattern contains invalid glob syntax.
            # https://peps.python.org/pep-0639/#add-license-files-key
            for entry in license_files:
                if "\\" in entry:
                    # Path delimiters MUST be the forward slash character (/).
                    raise ValueError(
                        f"Invalid entry in [project.license-files]: '{entry}'"
                        " (Path delimiters must be forward slashes.)"
                    )
                if ".." in Path(entry).parts:
                    # Parent directory indicators (..) MUST NOT be used.
                    raise ValueError(
                        f"Invalid entry in [project.license-files]: '{entry}'"
                        " ('..' must not be used.)"
                    )
            package.license_files = tuple(license_files)

        package.requires_python = project.get("requires-python", "*")
        package.keywords = project.get("keywords") or tool_poetry.get("keywords", [])
        package.classifiers = (
            static_classifiers := project.get("classifiers")
        ) or tool_poetry.get("classifiers", [])
        package.dynamic_classifiers = not static_classifiers

        if urls := project.get("urls"):
            custom_urls = {}
            for name, url in urls.items():
                lower_name = name.lower()
                if lower_name == "homepage":
                    package.homepage = url
                elif lower_name == "repository":
                    package.repository_url = url
                elif lower_name == "documentation":
                    package.documentation_url = url
                else:
                    custom_urls[name] = url
            package.custom_urls = custom_urls
        else:
            package.homepage = tool_poetry.get("homepage")
            package.repository_url = tool_poetry.get("repository")
            package.documentation_url = tool_poetry.get("documentation")
            if "urls" in tool_poetry:
                package.custom_urls = tool_poetry["urls"]

        if readme := project.get("readme"):
            if isinstance(readme, str):
                package.readmes = (root / readme,)
            elif "file" in readme:
                package.readmes = (root / readme["file"],)
                package.readme_content_type = readme["content-type"]
            elif "text" in readme:
                package.readme_content = readme["text"]
                package.readme_content_type = readme["content-type"]
        elif custom_readme := tool_poetry.get("readme"):
            custom_readmes = (
                (custom_readme,) if isinstance(custom_readme, str) else custom_readme
            )
            package.readmes = tuple(root / r for r in custom_readmes if r)

    @classmethod
    def _configure_entry_points(
        cls,
        package: ProjectPackage,
        project: dict[str, Any],
        tool_poetry: dict[str, Any],
    ) -> None:
        entry_points: defaultdict[str, dict[str, str]] = defaultdict(dict)

        if scripts := project.get("scripts"):
            entry_points["console-scripts"] = scripts
        elif scripts := tool_poetry.get("scripts"):
            for name, specification in scripts.items():
                if isinstance(specification, str):
                    specification = {"reference": specification, "type": "console"}

                if specification.get("type") != "console":
                    continue

                reference = specification.get("reference")

                if reference:
                    entry_points["console-scripts"][name] = reference

        if scripts := project.get("gui-scripts"):
            entry_points["gui-scripts"] = scripts

        if other_scripts := project.get("entry-points"):
            for group_name, scripts in sorted(other_scripts.items()):
                if group_name in {"console-scripts", "gui-scripts"}:
                    raise ValueError(
                        f"Group '{group_name}' is reserved and cannot be used"
                        " as a custom entry-point group."
                    )
                entry_points[group_name] = scripts
        elif other_scripts := tool_poetry.get("plugins"):
            for group_name, scripts in sorted(other_scripts.items()):
                entry_points[group_name] = scripts

        package.entry_points = dict(entry_points)

    @classmethod
    def _configure_package_dependencies(
        cls,
        package: ProjectPackage,
        project: dict[str, Any],
        tool_poetry: dict[str, Any],
        dependency_groups: dict[str, list[str | dict[str, str]]],
        with_groups: bool = True,
    ) -> None:
        from conda_lock._vendor.poetry.core.packages.dependency import Dependency
        from conda_lock._vendor.poetry.core.packages.dependency_group import MAIN_GROUP
        from conda_lock._vendor.poetry.core.packages.dependency_group import DependencyGroup

        dependencies = project.get("dependencies")
        optional_dependencies = project.get("optional-dependencies", {})
        dynamic = project.get("dynamic", [])

        package_extras: dict[NormalizedName, list[Dependency]]
        if dependencies is not None or optional_dependencies:
            dependencies = dependencies or {}

            group = DependencyGroup(
                MAIN_GROUP,
                mixed_dynamic=(
                    "dependencies" in dynamic or "optional-dependencies" in dynamic
                ),
            )
            package.add_dependency_group(group)

            for constraint in dependencies:
                group.add_dependency(
                    Dependency.create_from_pep_508(
                        constraint, relative_to=package.root_dir
                    )
                )
            package_extras = {}
            for extra_name, dependencies in optional_dependencies.items():
                extra_name = canonicalize_name(extra_name)
                package_extras[extra_name] = []

                for dependency_constraint in dependencies:
                    dependency = Dependency.create_from_pep_508(
                        dependency_constraint, relative_to=package.root_dir
                    )
                    dependency._optional = True
                    dependency._in_extras = [extra_name]

                    package_extras[extra_name].append(dependency)
                    group.add_dependency(dependency)

            package.extras = package_extras

        if "dependencies" in tool_poetry:
            cls._add_package_poetry_group_dependencies(
                package=package,
                group=MAIN_GROUP,
                dependencies=tool_poetry["dependencies"],
            )

        if with_groups:
            cls._configure_package_dependency_groups(
                package, tool_poetry, dependency_groups
            )

        if with_groups and "dev-dependencies" in tool_poetry:
            cls._add_package_poetry_group_dependencies(
                package=package,
                group="dev",
                dependencies=tool_poetry["dev-dependencies"],
            )

        # ignore extras in [tool.poetry] if dependencies or optional-dependencies
        # are declared in [project]
        if not dependencies and not optional_dependencies:
            package_extras = {}
            extras = tool_poetry.get("extras", {})
            for extra_name, requirements in extras.items():
                extra_name = canonicalize_name(extra_name)
                package_extras[extra_name] = []

                # Checking for dependency
                for req in requirements:
                    req = Dependency(req, "*")

                    for dep in package.requires:
                        if dep.name == req.name:
                            dep._in_extras = [*dep._in_extras, extra_name]
                            package_extras[extra_name].append(dep)

            package.extras = package_extras

    @classmethod
    def _configure_package_dependency_groups(
        cls,
        package: ProjectPackage,
        tool_poetry: dict[str, Any],
        dependency_groups: dict[str, list[str | dict[str, str]]],
    ) -> None:
        tool_poetry_groups = tool_poetry.get("group", {})
        tool_poetry_groups_normalized = {
            canonicalize_name(name): config
            for name, config in tool_poetry_groups.items()
        }
        # create groups from the dependency-groups section considering
        # additional information from the corresponding tool.poetry.group section
        pep739_include_groups = {}
        for group_name, dependencies in dependency_groups.items():
            poetry_group_config = tool_poetry_groups_normalized.get(
                canonicalize_name(group_name), {}
            )
            group = DependencyGroup(
                name=group_name,
                optional=poetry_group_config.get("optional", False),
            )
            package.add_dependency_group(group)
            included_groups = cls._add_package_pep735_group_dependencies(
                package=package,
                group=group,
                dependencies=dependencies,
            )
            pep739_include_groups[group_name] = included_groups
        # create groups from the tool.poetry.group section
        # with no corresponding entry in dependency-groups
        # and add dependency information for existing groups
        poetry_include_groups = {}
        for group_name, group_config in tool_poetry_groups.items():
            poetry_include_groups[group_name] = group_config.get("include-groups", [])
            if package.has_dependency_group(group_name):
                group = package.dependency_group(group_name)
            else:
                group = DependencyGroup(
                    name=group_name,
                    optional=group_config.get("optional", False),
                )
                package.add_dependency_group(group)
            cls._add_package_poetry_group_dependencies(
                package=package,
                group=group,
                dependencies=group_config.get("dependencies", {}),
            )

        for group_name, include_groups in chain(
            pep739_include_groups.items(), poetry_include_groups.items()
        ):
            if include_groups:
                current_group = package.dependency_group(group_name)
                for name in include_groups:
                    try:
                        # `name` isn't normalized,
                        # but `.dependency_group()` handles that.
                        group_to_include = package.dependency_group(name)
                    except ValueError as e:
                        raise ValueError(
                            f"Group '{group_name}' includes group '{name}'"
                            " which is not defined."
                        ) from e

                    current_group.include_dependency_group(group_to_include)

    @classmethod
    def _prepare_formats(
        cls,
        items: list[dict[str, Any]],
        default_formats: list[Literal["sdist", "wheel"]],
    ) -> list[dict[str, Any]]:
        result = []
        for item in items:
            formats = item.get("format", default_formats)
            if not isinstance(formats, list):
                formats = [formats]

            result.append({**item, "format": formats})

        return result

    @classmethod
    def _configure_package_poetry_specifics(
        cls, package: ProjectPackage, tool_poetry: dict[str, Any]
    ) -> None:
        if build := tool_poetry.get("build"):
            if not isinstance(build, dict):
                build = {"script": build}
            package.build_config = build or {}

        if includes := tool_poetry.get("include"):
            includes = [
                include if isinstance(include, dict) else {"path": include}
                for include in includes
            ]

            package.include = cls._prepare_formats(includes, default_formats=["sdist"])

        if exclude := tool_poetry.get("exclude"):
            package.exclude = exclude

        if packages := tool_poetry.get("packages"):
            package.packages = cls._prepare_formats(
                packages, default_formats=["sdist", "wheel"]
            )

    @classmethod
    def create_dependency(
        cls,
        name: str,
        constraint: DependencyConstraint,
        groups: list[str] | None = None,
        root_dir: Path | None = None,
    ) -> Dependency:
        from conda_lock._vendor.poetry.core.constraints.generic import (
            parse_constraint as parse_generic_constraint,
        )
        from conda_lock._vendor.poetry.core.constraints.version import (
            parse_constraint as parse_version_constraint,
        )
        from conda_lock._vendor.poetry.core.packages.dependency import Dependency
        from conda_lock._vendor.poetry.core.packages.dependency_group import MAIN_GROUP
        from conda_lock._vendor.poetry.core.packages.directory_dependency import DirectoryDependency
        from conda_lock._vendor.poetry.core.packages.file_dependency import FileDependency
        from conda_lock._vendor.poetry.core.packages.url_dependency import URLDependency
        from conda_lock._vendor.poetry.core.packages.utils.utils import create_nested_marker
        from conda_lock._vendor.poetry.core.packages.vcs_dependency import VCSDependency
        from conda_lock._vendor.poetry.core.version.markers import AnyMarker
        from conda_lock._vendor.poetry.core.version.markers import parse_marker

        if groups is None:
            groups = [MAIN_GROUP]

        if constraint is None:
            constraint = "*"

        if isinstance(constraint, Mapping):
            optional = constraint.get("optional", False)
            python_versions = constraint.get("python")
            platform = constraint.get("platform")
            markers = constraint.get("markers")
            allows_prereleases = constraint.get("allow-prereleases")

            dependency: Dependency
            if "git" in constraint:
                # VCS dependency
                dependency = VCSDependency(
                    name,
                    "git",
                    constraint["git"],
                    branch=constraint.get("branch", None),
                    tag=constraint.get("tag", None),
                    rev=constraint.get("rev", None),
                    directory=constraint.get("subdirectory", None),
                    groups=groups,
                    optional=optional,
                    develop=constraint.get("develop", False),
                    extras=constraint.get("extras", []),
                )
            elif "file" in constraint:
                file_path = Path(constraint["file"])

                dependency = FileDependency(
                    name,
                    file_path,
                    directory=constraint.get("subdirectory", None),
                    groups=groups,
                    base=root_dir,
                    extras=constraint.get("extras", []),
                )
            elif "path" in constraint:
                path = Path(constraint["path"])

                if root_dir:
                    is_file = root_dir.joinpath(path).is_file()
                else:
                    is_file = path.is_file()

                if is_file:
                    dependency = FileDependency(
                        name,
                        path,
                        directory=constraint.get("subdirectory", None),
                        groups=groups,
                        optional=optional,
                        base=root_dir,
                        extras=constraint.get("extras", []),
                    )
                else:
                    subdirectory = constraint.get("subdirectory", None)
                    if subdirectory:
                        path = path / subdirectory
                    dependency = DirectoryDependency(
                        name,
                        path,
                        groups=groups,
                        optional=optional,
                        base=root_dir,
                        develop=constraint.get("develop", False),
                        extras=constraint.get("extras", []),
                    )
            elif "url" in constraint:
                dependency = URLDependency(
                    name,
                    constraint["url"],
                    directory=constraint.get("subdirectory", None),
                    groups=groups,
                    optional=optional,
                    extras=constraint.get("extras", []),
                )
            else:
                version = constraint.get("version", "*")

                dependency = Dependency(
                    name,
                    version,
                    optional=optional,
                    groups=groups,
                    allows_prereleases=allows_prereleases,
                    extras=constraint.get("extras", []),
                )
                # Normally not valid, but required for enriching [project] dependencies
                dependency._develop = constraint.get("develop", False)

            marker = parse_marker(markers) if markers else AnyMarker()

            if python_versions:
                marker = marker.intersect(
                    parse_marker(
                        create_nested_marker(
                            "python_version", parse_version_constraint(python_versions)
                        )
                    )
                )

            if platform:
                marker = marker.intersect(
                    parse_marker(
                        create_nested_marker(
                            "sys_platform", parse_generic_constraint(platform)
                        )
                    )
                )

            if not marker.is_any():
                dependency.marker = marker

            dependency.source_name = constraint.get("source")
        else:
            dependency = Dependency(name, constraint, groups=groups)

        return dependency

    @classmethod
    def validate(
        cls, toml_data: dict[str, Any], strict: bool = False
    ) -> dict[str, list[str]]:
        """
        Checks the validity of a configuration
        """
        from conda_lock._vendor.poetry.core.json import validate_object

        result: dict[str, list[str]] = {"errors": [], "warnings": []}

        # Validate against schemas
        project = toml_data.get("project")
        if project is not None:
            project_validation_errors = [
                e.replace("data", "project")
                for e in validate_object(project, "project-schema")
            ]
            result["errors"] += project_validation_errors
        # With PEP 621 [tool.poetry] is not mandatory anymore. We still create and
        # validate it so that default values (e.g. for package-mode) are set.
        tool_poetry = toml_data.setdefault("tool", {}).setdefault("poetry", {})
        tool_poetry_validation_errors = [
            e.replace("data.", "tool.poetry.")
            for e in validate_object(tool_poetry, "poetry-schema")
        ]
        result["errors"] += tool_poetry_validation_errors

        dependency_groups = toml_data.get("dependency-groups")
        if dependency_groups is not None:
            dependency_groups_validation_errors = [
                e.replace("data", "dependency-groups")
                for e in validate_object(dependency_groups, "dependency-groups-schema")
            ]
            result["errors"] += dependency_groups_validation_errors

        # Check for required fields if package mode.
        # In non-package mode, there are no required fields.
        package_mode = tool_poetry.get("package-mode", True)
        if package_mode:
            for key in ("name", "version"):
                value = (project or {}).get(key) or tool_poetry.get(key)
                if not value:
                    result["errors"].append(
                        f"Either [project.{key}] or [tool.poetry.{key}]"
                        " is required in package mode."
                    )

        config = tool_poetry

        if "dev-dependencies" in config:
            result["warnings"].append(
                'The "poetry.dev-dependencies" section is deprecated'
                " and will be removed in a future version."
                ' Use "poetry.group.dev.dependencies" instead.'
            )

        cls._validate_dependency_groups(toml_data, result)

        if strict:
            # Validate [project] section
            if project:
                cls._validate_project(project, result)

            # Validate relation between [project] and [tool.poetry]
            cls._validate_legacy_vs_project(toml_data, result)

            cls._validate_strict(config, result)

        return result

    @classmethod
    def _validate_dependency_groups(
        cls, toml_data: dict[str, Any], result: dict[str, list[str]]
    ) -> None:
        """Ensure that there are no duplicated dependency groups
        and that they do not include themselves."""
        original_names = defaultdict(set)
        group_includes: dict[NormalizedName, list[NormalizedName]] = {}

        for group_name, dependencies in toml_data.get("dependency-groups", {}).items():
            normalized_group_name = canonicalize_name(group_name)
            original_names[normalized_group_name].add(group_name)
            for constraint in dependencies:
                if isinstance(constraint, dict) and (
                    include := constraint.get("include-group")
                ):
                    group_includes.setdefault(normalized_group_name, []).append(
                        canonicalize_name(include)
                    )

        poetry_config = toml_data.get("tool", {}).get("poetry", {})
        for group_name, group_config in poetry_config.get("group", {}).items():
            normalized_group_name = canonicalize_name(group_name)
            original_names[normalized_group_name].add(group_name)
            if include_groups := group_config.get("include-groups", []):
                group_includes.setdefault(normalized_group_name, []).extend(
                    canonicalize_name(name) for name in include_groups
                )

        for normed_name, names in original_names.items():
            if len(names) > 1:
                result["errors"].append(
                    "Duplicate dependency group name after normalization:"
                    f" {normed_name} ({', '.join(sorted(names))})"
                )

        for root in group_includes:
            # group, path to group, ancestors
            stack: list[
                tuple[NormalizedName, list[NormalizedName], set[NormalizedName]]
            ] = [(root, [], {root})]
            while stack:
                group, path, ancestors = stack.pop()
                for include in group_includes.get(group, []):
                    new_path = [*path, include]
                    if include in ancestors:
                        result["errors"].append(
                            f"Cyclic dependency group include in {root}:"
                            f" {' -> '.join(new_path)}"
                        )
                    else:
                        stack.append((include, new_path, ancestors | {include}))

    @classmethod
    def _validate_project(
        cls, project: dict[str, Any], result: dict[str, list[str]]
    ) -> None:
        if (project_license := project.get("license")) is not None:
            if isinstance(project_license, str):
                try:
                    canonicalize_license_expression(project_license)
                except InvalidLicenseExpression:
                    result["warnings"].append(
                        "[project.license] is not a valid SPDX expression."
                        " This is deprecated and will raise an error in the future."
                    )
            else:
                result["warnings"].append(
                    "Defining [project.license] as a table is deprecated."
                    " [project.license] should be a valid SPDX license expression."
                    " License files can be referenced in [project.license-files]."
                )

        for classifier in project.get("classifiers", []):
            if classifier.startswith("License :: "):
                result["warnings"].append(
                    "License classifiers are deprecated. Use [project.license] instead."
                )

    @classmethod
    def _validate_legacy_vs_project(
        cls, toml_data: dict[str, Any], result: dict[str, list[str]]
    ) -> None:
        project = toml_data.get("project", {})
        dynamic = project.get("dynamic", [])
        tool_poetry = toml_data["tool"]["poetry"]

        redundant_fields = [
            # name, deprecated (if not dynamic), new name (or None if same as old)
            ("name", True, None),
            # version can be dynamically set via `build --local-version` or plugins
            ("version", False, None),
            ("description", True, None),
            # multiple readmes are not supported in [project.readme]
            ("readme", False, None),
            ("license", True, None),
            ("authors", True, None),
            ("maintainers", True, None),
            ("keywords", True, None),
            # classifiers are enriched dynamically per default
            ("classifiers", False, None),
            ("homepage", True, "urls"),
            ("repository", True, "urls"),
            ("documentation", True, "urls"),
            ("urls", True, "urls"),
            ("plugins", True, "entry-points"),
            ("extras", True, "optional-dependencies"),
        ]
        dynamic_information = {
            "version": (
                "If you want to set the version dynamically via"
                " `poetry build --local-version` or you are using a plugin, which"
                " sets the version dynamically, you should define the version in"
                " [tool.poetry] and add 'version' to [project.dynamic]."
            ),
            "readme": (
                "If you want to define multiple readmes, you should define them in"
                " [tool.poetry] and add 'readme' to [project.dynamic]."
            ),
            "classifiers": (
                "ATTENTION: Per default Poetry determines classifiers for supported"
                " Python versions and license automatically. If you define classifiers"
                " in [project], you disable the automatic enrichment. In other words,"
                " you have to define all classifiers manually."
                " If you want to use Poetry's automatic enrichment of classifiers,"
                " you should define them in [tool.poetry] and add 'classifiers'"
                " to [project.dynamic]."
            ),
        }
        assert {f[0] for f in redundant_fields if not f[1]} == set(dynamic_information)

        for name, deprecated, new_name in redundant_fields:
            new_name = new_name or name
            if name in tool_poetry:
                warning = ""
                if new_name in project:
                    warning = (
                        f"[project.{new_name}] and [tool.poetry.{name}] are both set."
                        " The latter will be ignored."
                    )
                elif deprecated:
                    warning = (
                        f"[tool.poetry.{name}] is deprecated."
                        f" Use [project.{new_name}] instead."
                    )
                elif new_name not in dynamic:
                    warning = (
                        f"[tool.poetry.{name}] is set but '{new_name}' is not in"
                        f" [project.dynamic]. If it is static use [project.{new_name}]."
                        f" If it is dynamic, add '{new_name}' to [project.dynamic]."
                    )
                if warning:
                    if additional_info := dynamic_information.get(name):
                        warning += f"\n{additional_info}"
                    result["warnings"].append(warning)

        # scripts are special because entry-points are deprecated
        # but files are not because there is no equivalent in [project]
        if scripts := tool_poetry.get("scripts"):
            for script in scripts.values():
                if not isinstance(script, dict) or script.get("type") != "file":
                    if "scripts" in project:
                        warning = (
                            "[project.scripts] is set and there are console scripts in"
                            " [tool.poetry.scripts]. The latter will be ignored."
                        )
                    else:
                        warning = (
                            "Defining console scripts in [tool.poetry.scripts] is"
                            " deprecated. Use [project.scripts] instead."
                            " ([tool.poetry.scripts] should only be used for scripts"
                            " of type 'file')."
                        )
                    result["warnings"].append(warning)
                    break

        # dependencies are special because we consider
        # [project.dependencies] as abstract dependencies for building
        # and [tool.poetry.dependencies] as the concrete dependencies for locking
        if (
            "dependencies" in tool_poetry
            and "project" in toml_data
            and "dependencies" not in project
            and "dependencies" not in project.get("dynamic", [])
        ):
            result["warnings"].append(
                "[tool.poetry.dependencies] is set but [project.dependencies] is not"
                " and 'dependencies' is not in [project.dynamic]."
                " You should either migrate [tool.poetry.dependencies] to"
                " [project.dependencies] (if you do not need Poetry-specific features)"
                " or add [project.dependencies] in addition to"
                " [tool.poetry.dependencies] or add 'dependencies' to"
                " [project.dynamic]."
            )

        # requires-python in [project] and python in [tool.poetry.dependencies] are
        # special because we consider requires-python as abstract python version
        # for building and python as concrete python version for locking
        if (
            "python" in tool_poetry.get("dependencies", {})
            and "project" in toml_data
            and "requires-python" not in project
            and "requires-python" not in project.get("dynamic", [])
        ):
            result["warnings"].append(
                "[tool.poetry.dependencies.python] is set but [project.requires-python]"
                " is not set and 'requires-python' is not in [project.dynamic]."
            )

    @classmethod
    def _validate_strict(
        cls, config: dict[str, Any], result: dict[str, list[str]]
    ) -> None:
        for classifier in config.get("classifiers", []):
            if classifier.startswith("License :: "):
                result["warnings"].append(
                    "License classifiers are deprecated. Use [project.license] instead."
                )

        if "dependencies" in config:
            python_versions = config["dependencies"].get("python")
            if python_versions == "*":
                result["warnings"].append(
                    "A wildcard Python dependency is ambiguous. "
                    "Consider specifying a more explicit one."
                )

            for name, constraint in config["dependencies"].items():
                if not isinstance(constraint, dict):
                    continue

                if "allows-prereleases" in constraint:
                    result["warnings"].append(
                        f'The "{name}" dependency specifies '
                        'the "allows-prereleases" property, which is deprecated. '
                        'Use "allow-prereleases" instead.'
                    )

        if "extras" in config:
            for extra_name, requirements in config["extras"].items():
                extra_name = canonicalize_name(extra_name)

                for req in requirements:
                    req_name = canonicalize_name(req)
                    for dependency in config.get("dependencies", {}):
                        dep_name = canonicalize_name(dependency)
                        if req_name == dep_name:
                            break
                    else:
                        result["errors"].append(
                            f'Cannot find dependency "{req}" for extra '
                            f'"{extra_name}" in main dependencies.'
                        )

        # Checking for scripts with extras
        if "scripts" in config:
            scripts = config["scripts"]
            config_extras = config.get("extras", {})

            for name, script in scripts.items():
                if not isinstance(script, dict):
                    continue

                extras = script.get("extras", [])
                if extras:
                    result["warnings"].append(
                        f'The script "{name}" depends on an extra. Scripts'
                        " depending on extras are deprecated and support for them"
                        " will be removed in a future version of"
                        " poetry/poetry-core. See"
                        " https://packaging.python.org/en/latest/specifications/entry-points/#data-model"
                        " for details."
                    )
                for extra in extras:
                    if extra not in config_extras:
                        result["errors"].append(
                            f'The script "{name}" requires extra "{extra}"'
                            " which is not defined."
                        )

        # Checking types of all readme files (must match)
        if "readme" in config and not isinstance(config["readme"], str):
            readme_types = {readme_content_type(r) for r in config["readme"]}
            if len(readme_types) > 1:
                result["errors"].append(
                    "Declared README files must be of same type: found"
                    f" {', '.join(sorted(readme_types))}"
                )

    @classmethod
    def locate(cls, cwd: Path | None = None) -> Path:
        cwd = Path(cwd or Path.cwd())
        candidates = [cwd]
        candidates.extend(cwd.parents)

        for path in candidates:
            poetry_file = path / "pyproject.toml"

            if poetry_file.exists():
                return poetry_file

        raise RuntimeError(
            f"Poetry could not find a pyproject.toml file in {cwd} or its parents"
        )
