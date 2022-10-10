from __future__ import absolute_import
from __future__ import unicode_literals

import logging

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union
from warnings import warn

from .json import validate_object
from .packages.dependency import Dependency
from .packages.project_package import ProjectPackage
from .poetry import Poetry
from .pyproject import PyProjectTOML
from .spdx import license_by_id
from .utils._compat import Path


logger = logging.getLogger(__name__)


class Factory(object):
    """
    Factory class to create various elements needed by Poetry.
    """

    def create_poetry(
        self, cwd=None, with_dev=True
    ):  # type: (Optional[Path], bool) -> Poetry
        poetry_file = self.locate(cwd)
        local_config = PyProjectTOML(path=poetry_file).poetry_config

        # Checking validity
        check_result = self.validate(local_config)
        if check_result["errors"]:
            message = ""
            for error in check_result["errors"]:
                message += "  - {}\n".format(error)

            raise RuntimeError("The Poetry configuration is invalid:\n" + message)

        # Load package
        name = local_config["name"]
        version = local_config["version"]
        package = ProjectPackage(name, version, version)
        package.root_dir = poetry_file.parent

        for author in local_config["authors"]:
            package.authors.append(author)

        for maintainer in local_config.get("maintainers", []):
            package.maintainers.append(maintainer)

        package.description = local_config.get("description", "")
        package.homepage = local_config.get("homepage")
        package.repository_url = local_config.get("repository")
        package.documentation_url = local_config.get("documentation")
        try:
            license_ = license_by_id(local_config.get("license", ""))
        except ValueError:
            license_ = None

        package.license = license_
        package.keywords = local_config.get("keywords", [])
        package.classifiers = local_config.get("classifiers", [])

        if "readme" in local_config:
            package.readme = Path(poetry_file.parent) / local_config["readme"]

        if "platform" in local_config:
            package.platform = local_config["platform"]

        if "dependencies" in local_config:
            for name, constraint in local_config["dependencies"].items():
                if name.lower() == "python":
                    package.python_versions = constraint
                    continue

                if isinstance(constraint, list):
                    for _constraint in constraint:
                        package.add_dependency(
                            self.create_dependency(
                                name, _constraint, root_dir=package.root_dir
                            )
                        )

                    continue

                package.add_dependency(
                    self.create_dependency(name, constraint, root_dir=package.root_dir)
                )

        if with_dev and "dev-dependencies" in local_config:
            for name, constraint in local_config["dev-dependencies"].items():
                if isinstance(constraint, list):
                    for _constraint in constraint:
                        package.add_dependency(
                            self.create_dependency(
                                name,
                                _constraint,
                                category="dev",
                                root_dir=package.root_dir,
                            )
                        )

                    continue

                package.add_dependency(
                    self.create_dependency(
                        name, constraint, category="dev", root_dir=package.root_dir
                    )
                )

        extras = local_config.get("extras", {})
        for extra_name, requirements in extras.items():
            package.extras[extra_name] = []

            # Checking for dependency
            for req in requirements:
                req = Dependency(req, "*")

                for dep in package.requires:
                    if dep.name == req.name:
                        dep.in_extras.append(extra_name)
                        package.extras[extra_name].append(dep)

                        break

        if "build" in local_config:
            build = local_config["build"]
            if not isinstance(build, dict):
                build = {"script": build}
            package.build_config = build or {}

        if "include" in local_config:
            package.include = []

            for include in local_config["include"]:
                if not isinstance(include, dict):
                    include = {"path": include}

                formats = include.get("format", [])
                if formats and not isinstance(formats, list):
                    formats = [formats]
                include["format"] = formats

                package.include.append(include)

        if "exclude" in local_config:
            package.exclude = local_config["exclude"]

        if "packages" in local_config:
            package.packages = local_config["packages"]

        # Custom urls
        if "urls" in local_config:
            package.custom_urls = local_config["urls"]

        return Poetry(poetry_file, local_config, package)

    @classmethod
    def create_dependency(
        cls,
        name,  # type: str
        constraint,  # type: Union[str, Dict[str, Any]]
        category="main",  # type: str
        root_dir=None,  # type: Optional[Path]
    ):  # type: (...) -> Dependency
        from .packages.constraints import parse_constraint as parse_generic_constraint
        from .packages.directory_dependency import DirectoryDependency
        from .packages.file_dependency import FileDependency
        from .packages.url_dependency import URLDependency
        from .packages.utils.utils import create_nested_marker
        from .packages.vcs_dependency import VCSDependency
        from .version.markers import AnyMarker
        from .version.markers import parse_marker

        if constraint is None:
            constraint = "*"

        if isinstance(constraint, dict):
            optional = constraint.get("optional", False)
            python_versions = constraint.get("python")
            platform = constraint.get("platform")
            markers = constraint.get("markers")
            if "allows-prereleases" in constraint:
                message = (
                    'The "{}" dependency specifies '
                    'the "allows-prereleases" property, which is deprecated. '
                    'Use "allow-prereleases" instead.'.format(name)
                )
                warn(message, DeprecationWarning)
                logger.warning(message)

            allows_prereleases = constraint.get(
                "allow-prereleases", constraint.get("allows-prereleases", False)
            )

            if "git" in constraint:
                # VCS dependency
                dependency = VCSDependency(
                    name,
                    "git",
                    constraint["git"],
                    branch=constraint.get("branch", None),
                    tag=constraint.get("tag", None),
                    rev=constraint.get("rev", None),
                    category=category,
                    optional=optional,
                    develop=constraint.get("develop", False),
                    extras=constraint.get("extras", []),
                )
            elif "file" in constraint:
                file_path = Path(constraint["file"])

                dependency = FileDependency(
                    name,
                    file_path,
                    category=category,
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
                        category=category,
                        optional=optional,
                        base=root_dir,
                        extras=constraint.get("extras", []),
                    )
                else:
                    dependency = DirectoryDependency(
                        name,
                        path,
                        category=category,
                        optional=optional,
                        base=root_dir,
                        develop=constraint.get("develop", False),
                        extras=constraint.get("extras", []),
                    )
            elif "url" in constraint:
                dependency = URLDependency(
                    name,
                    constraint["url"],
                    category=category,
                    optional=optional,
                    extras=constraint.get("extras", []),
                )
            else:
                version = constraint["version"]

                dependency = Dependency(
                    name,
                    version,
                    optional=optional,
                    category=category,
                    allows_prereleases=allows_prereleases,
                    extras=constraint.get("extras", []),
                )

            if not markers:
                marker = AnyMarker()
                if python_versions:
                    dependency.python_versions = python_versions
                    marker = marker.intersect(
                        parse_marker(
                            create_nested_marker(
                                "python_version", dependency.python_constraint
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
            else:
                marker = parse_marker(markers)

            if not marker.is_any():
                dependency.marker = marker

            dependency.source_name = constraint.get("source")
        else:
            dependency = Dependency(name, constraint, category=category)

        return dependency

    @classmethod
    def validate(
        cls, config, strict=False
    ):  # type: (dict, bool) -> Dict[str, List[str]]
        """
        Checks the validity of a configuration
        """
        result = {"errors": [], "warnings": []}
        # Schema validation errors
        validation_errors = validate_object(config, "poetry-schema")

        result["errors"] += validation_errors

        if strict:
            # If strict, check the file more thoroughly
            if "dependencies" in config:
                python_versions = config["dependencies"]["python"]
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
                            'The "{}" dependency specifies '
                            'the "allows-prereleases" property, which is deprecated. '
                            'Use "allow-prereleases" instead.'.format(name)
                        )

            # Checking for scripts with extras
            if "scripts" in config:
                scripts = config["scripts"]
                for name, script in scripts.items():
                    if not isinstance(script, dict):
                        continue

                    extras = script["extras"]
                    for extra in extras:
                        if extra not in config["extras"]:
                            result["errors"].append(
                                'Script "{}" requires extra "{}" which is not defined.'.format(
                                    name, extra
                                )
                            )

        return result

    @classmethod
    def locate(cls, cwd):  # type: (Path) -> Path
        candidates = [Path(cwd)]
        candidates.extend(Path(cwd).parents)

        for path in candidates:
            poetry_file = path / "pyproject.toml"

            if poetry_file.exists():
                return poetry_file

        else:
            raise RuntimeError(
                "Poetry could not find a pyproject.toml file in {} or its parents".format(
                    cwd
                )
            )
