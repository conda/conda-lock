from __future__ import annotations

import contextlib
import logging
import re

from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from conda_lock._vendor.cleo.io.null_io import NullIO
from packaging.utils import canonicalize_name
from conda_lock._vendor.poetry.core.factory import Factory as BaseFactory
from conda_lock._vendor.poetry.core.packages.dependency_group import MAIN_GROUP
from conda_lock._vendor.poetry.core.packages.project_package import ProjectPackage

from conda_lock._vendor.poetry.config.config import Config
from conda_lock._vendor.poetry.exceptions import PoetryException
from conda_lock._vendor.poetry.json import validate_object
from conda_lock._vendor.poetry.packages.locker import Locker
from conda_lock._vendor.poetry.plugins.plugin import Plugin
from conda_lock._vendor.poetry.plugins.plugin_manager import PluginManager
from conda_lock._vendor.poetry.poetry import Poetry
from conda_lock._vendor.poetry.toml.file import TOMLFile


if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from conda_lock._vendor.cleo.io.io import IO
    from conda_lock._vendor.poetry.core.packages.package import Package
    from tomlkit.toml_document import TOMLDocument

    from conda_lock._vendor.poetry.repositories import RepositoryPool
    from conda_lock._vendor.poetry.repositories.http_repository import HTTPRepository
    from conda_lock._vendor.poetry.utils.dependency_specification import DependencySpec

logger = logging.getLogger(__name__)


class Factory(BaseFactory):
    """
    Factory class to create various elements needed by Poetry.
    """

    def create_poetry(
        self,
        cwd: Path | None = None,
        with_groups: bool = True,
        io: IO | None = None,
        disable_plugins: bool = False,
        disable_cache: bool = False,
    ) -> Poetry:
        if io is None:
            io = NullIO()

        base_poetry = super().create_poetry(cwd=cwd, with_groups=with_groups)

        poetry_file = base_poetry.pyproject_path
        locker = Locker(poetry_file.parent / "poetry.lock", base_poetry.local_config)

        # Loading global configuration
        config = Config.create()

        # Loading local configuration
        local_config_file = TOMLFile(poetry_file.parent / "poetry.toml")
        if local_config_file.exists():
            if io.is_debug():
                io.write_line(f"Loading configuration file {local_config_file.path}")

            config.merge(local_config_file.read())

        # Load local sources
        repositories = {}
        existing_repositories = config.get("repositories", {})
        for source in base_poetry.pyproject.poetry_config.get("source", []):
            name = source.get("name")
            url = source.get("url")
            if name and url and name not in existing_repositories:
                repositories[name] = {"url": url}

        config.merge({"repositories": repositories})

        poetry = Poetry(
            poetry_file,
            base_poetry.local_config,
            base_poetry.package,
            locker,
            config,
            disable_cache,
        )

        poetry.set_pool(
            self.create_pool(
                config,
                poetry.local_config.get("source", []),
                io,
                disable_cache=disable_cache,
            )
        )

        plugin_manager = PluginManager(Plugin.group, disable_plugins=disable_plugins)
        plugin_manager.load_plugins()
        poetry.set_plugin_manager(plugin_manager)
        plugin_manager.activate(poetry, io)

        return poetry

    @classmethod
    def get_package(cls, name: str, version: str) -> ProjectPackage:
        return ProjectPackage(name, version)

    @classmethod
    def create_pool(
        cls,
        config: Config,
        sources: Iterable[dict[str, Any]] = (),
        io: IO | None = None,
        disable_cache: bool = False,
    ) -> RepositoryPool:
        from conda_lock._vendor.poetry.repositories import RepositoryPool
        from conda_lock._vendor.poetry.repositories.repository_pool import Priority

        if io is None:
            io = NullIO()

        if disable_cache:
            logger.debug("Disabling source caches")

        pool = RepositoryPool(config=config)

        explicit_pypi = False
        for source in sources:
            repository = cls.create_package_source(
                source, config, disable_cache=disable_cache
            )
            priority = Priority[source.get("priority", Priority.PRIMARY.name).upper()]
            if "default" in source or "secondary" in source:
                warning = (
                    "Found deprecated key 'default' or 'secondary' in"
                    " pyproject.toml configuration for source"
                    f" {source.get('name')}. Please provide the key 'priority'"
                    " instead. Accepted values are:"
                    f" {', '.join(repr(p.name.lower()) for p in Priority)}."
                )
                io.write_error_line(f"<warning>Warning: {warning}</warning>")
                if source.get("default"):
                    priority = Priority.DEFAULT
                elif source.get("secondary"):
                    priority = Priority.SECONDARY

            if priority is Priority.SECONDARY:
                allowed_prios = (p for p in Priority if p is not Priority.SECONDARY)
                warning = (
                    "Found deprecated priority 'secondary' for source"
                    f" '{source.get('name')}' in pyproject.toml. Consider changing the"
                    " priority to one of the non-deprecated values:"
                    f" {', '.join(repr(p.name.lower()) for p in allowed_prios)}."
                )
                io.write_error_line(f"<warning>Warning: {warning}</warning>")
            elif priority is Priority.DEFAULT:
                warning = (
                    "Found deprecated priority 'default' for source"
                    f" '{source.get('name')}' in pyproject.toml. You can achieve"
                    " the same effect by changing the priority to 'primary' and putting"
                    " the source first."
                )
                io.write_error_line(f"<warning>Warning: {warning}</warning>")

            if io.is_debug():
                message = f"Adding repository {repository.name} ({repository.url})"
                if priority is Priority.DEFAULT:
                    message += " and setting it as the default one"
                else:
                    message += f" and setting it as {priority.name.lower()}"

                io.write_line(message)

            pool.add_repository(repository, priority=priority)
            if repository.name.lower() == "pypi":
                explicit_pypi = True

        # Only add PyPI if no default repository is configured
        if not explicit_pypi:
            if pool.has_default() or pool.has_primary_repositories():
                if io.is_debug():
                    io.write_line("Deactivating the PyPI repository")
            else:
                from conda_lock._vendor.poetry.repositories.pypi_repository import PyPiRepository

                pool.add_repository(
                    PyPiRepository(disable_cache=disable_cache),
                    priority=Priority.PRIMARY,
                )

        if not pool.repositories:
            raise PoetryException(
                "At least one source must not be configured as 'explicit'."
            )

        return pool

    @classmethod
    def create_package_source(
        cls, source: dict[str, str], config: Config, disable_cache: bool = False
    ) -> HTTPRepository:
        from conda_lock._vendor.poetry.repositories.exceptions import InvalidSourceError
        from conda_lock._vendor.poetry.repositories.legacy_repository import LegacyRepository
        from conda_lock._vendor.poetry.repositories.pypi_repository import PyPiRepository
        from conda_lock._vendor.poetry.repositories.single_page_repository import SinglePageRepository

        try:
            name = source["name"]
        except KeyError:
            raise InvalidSourceError("Missing [name] in source.")

        pool_size = config.installer_max_workers

        if name.lower() == "pypi":
            if "url" in source:
                raise InvalidSourceError(
                    "The PyPI repository cannot be configured with a custom url."
                )
            return PyPiRepository(disable_cache=disable_cache, pool_size=pool_size)

        try:
            url = source["url"]
        except KeyError:
            raise InvalidSourceError(f"Missing [url] in source {name!r}.")

        repository_class = LegacyRepository

        if re.match(r".*\.(htm|html)$", url):
            repository_class = SinglePageRepository

        return repository_class(
            name,
            url,
            config=config,
            disable_cache=disable_cache,
            pool_size=pool_size,
        )

    @classmethod
    def create_pyproject_from_package(cls, package: Package) -> TOMLDocument:
        import tomlkit

        from conda_lock._vendor.poetry.utils.dependency_specification import dependency_to_specification

        pyproject: dict[str, Any] = tomlkit.document()

        pyproject["tool"] = tomlkit.table(is_super_table=True)

        content: dict[str, Any] = tomlkit.table()
        pyproject["tool"]["poetry"] = content

        content["name"] = package.name
        content["version"] = package.version.text
        content["description"] = package.description
        content["authors"] = package.authors
        content["license"] = package.license.id if package.license else ""

        if package.classifiers:
            content["classifiers"] = package.classifiers

        for key, attr in {
            ("documentation", "documentation_url"),
            ("repository", "repository_url"),
            ("homepage", "homepage"),
            ("maintainers", "maintainers"),
            ("keywords", "keywords"),
        }:
            value = getattr(package, attr, None)
            if value:
                content[key] = value

        readmes = []

        for readme in package.readmes:
            readme_posix_path = readme.as_posix()

            with contextlib.suppress(ValueError):
                if package.root_dir:
                    readme_posix_path = readme.relative_to(package.root_dir).as_posix()

            readmes.append(readme_posix_path)

        if readmes:
            content["readme"] = readmes

        optional_dependencies = set()
        extras_section = None

        if package.extras:
            extras_section = tomlkit.table()

            for extra in package.extras:
                _dependencies = []
                for dependency in package.extras[extra]:
                    _dependencies.append(dependency.name)
                    optional_dependencies.add(dependency.name)

                extras_section[extra] = _dependencies

        optional_dependencies = set(optional_dependencies)
        dependency_section = content["dependencies"] = tomlkit.table()
        dependency_section["python"] = package.python_versions

        for dep in package.all_requires:
            constraint: DependencySpec | str = dependency_to_specification(
                dep, tomlkit.inline_table()
            )

            if not isinstance(constraint, str):
                if dep.name in optional_dependencies:
                    constraint["optional"] = True

                if len(constraint) == 1 and "version" in constraint:
                    assert isinstance(constraint["version"], str)
                    constraint = constraint["version"]
                elif not constraint:
                    constraint = "*"

            for group in dep.groups:
                if group == MAIN_GROUP:
                    dependency_section[dep.name] = constraint
                else:
                    if "group" not in content:
                        content["group"] = tomlkit.table(is_super_table=True)

                    if group not in content["group"]:
                        content["group"][group] = tomlkit.table(is_super_table=True)

                    if "dependencies" not in content["group"][group]:
                        content["group"][group]["dependencies"] = tomlkit.table()

                    content["group"][group]["dependencies"][dep.name] = constraint

        if extras_section:
            content["extras"] = extras_section

        pyproject = cast("TOMLDocument", pyproject)

        return pyproject

    @classmethod
    def validate(
        cls, config: dict[str, Any], strict: bool = False
    ) -> dict[str, list[str]]:
        results = super().validate(config, strict)

        results["errors"].extend(validate_object(config))

        # A project should not depend on itself.
        dependencies = set(config.get("dependencies", {}).keys())
        dependencies.update(config.get("dev-dependencies", {}).keys())
        groups = config.get("group", {}).values()
        for group in groups:
            dependencies.update(group.get("dependencies", {}).keys())

        dependencies = {canonicalize_name(d) for d in dependencies}

        project_name = config.get("name")
        if project_name is not None and canonicalize_name(project_name) in dependencies:
            results["errors"].append(
                f"Project name ({project_name}) is same as one of its dependencies"
            )

        return results
