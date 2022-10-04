import collections
import collections.abc
import pathlib

from functools import partial
from typing import AbstractSet, Any, List, Mapping, Optional, Sequence, Union
from urllib.parse import urldefrag


try:
    from tomllib import load as toml_load
except ImportError:
    from tomli import load as toml_load

from typing_extensions import Literal

from conda_lock.common import get_in
from conda_lock.lookup import get_forward_lookup as get_lookup
from conda_lock.src_parser import (
    Dependency,
    LockSpecification,
    URLDependency,
    VersionedDependency,
)


def join_version_components(pieces: Sequence[Union[str, int]]) -> str:
    return ".".join(str(p) for p in pieces)


def normalize_pypi_name(name: str) -> str:
    if name in get_lookup():
        lookup = get_lookup()[name]
        return lookup.get("conda_name") or lookup.get("conda_forge")
    else:
        return name


def poetry_version_to_conda_version(version_string: Optional[str]) -> Optional[str]:
    if version_string is None:
        return None
    components = [c.replace(" ", "").strip() for c in version_string.split(",")]
    output_components = []

    for c in components:
        if len(c) == 0:
            continue
        version_pieces = c.lstrip("<>=^~!").split(".")
        if c[0] == "^":
            upper_version = [int(version_pieces[0]) + 1]
            for i in range(1, len(version_pieces)):
                upper_version.append(0)

            output_components.append(f">={join_version_components(version_pieces)}")
            output_components.append(f"<{join_version_components(upper_version)}")
        elif c[0] == "~":
            upper_version = [int(version_pieces[0]), int(version_pieces[1]) + 1]
            for i in range(2, len(version_pieces)):
                upper_version.append(0)

            output_components.append(f">={join_version_components(version_pieces)}")
            output_components.append(f"<{join_version_components(upper_version)}")
        else:
            output_components.append(c.replace("===", "=").replace("==", "="))
    return ",".join(output_components)


def parse_poetry_pyproject_toml(
    path: pathlib.Path,
    contents: Mapping[str, Any],
) -> LockSpecification:
    """
    Parse dependencies from a poetry pyproject.toml file

    Each dependency is assigned a category depending on which section it appears in:
    * dependencies in [tool.poetry.dependencies] have category main
    * dependencies in [tool.poetry.dev-dependencies] have category dev
    * dependencies in each `key` of [tool.poetry.extras] have category `key`

    * By default, dependency names are translated to the conda equivalent, with two exceptions:
        - If a dependency has `source = "pypi"`, it is treated as a pip dependency (by name)
        - If a dependency has a url, it is treated as a direct pip dependency (by url)

    * markers are not supported

    """
    dependencies: List[Dependency] = []

    categories = {"dependencies": "main", "dev-dependencies": "dev"}

    dep_to_extra = {}
    for category, deps in get_in(["tool", "poetry", "extras"], contents, {}).items():
        for dep in deps:
            dep_to_extra[dep] = category

    for section, default_category in categories.items():
        for depname, depattrs in get_in(
            ["tool", "poetry", section], contents, {}
        ).items():
            category = dep_to_extra.get(depname) or default_category
            optional = category != "main"
            manager: Literal["conda", "pip"] = "conda"
            url = None
            extras = []
            if isinstance(depattrs, collections.abc.Mapping):
                poetry_version_spec = depattrs.get("version", None)
                url = depattrs.get("url", None)
                optional = depattrs.get("optional", False)
                extras = depattrs.get("extras", [])
                # If a dependency is explicitly marked as sourced from pypi,
                # or is a URL dependency, delegate to the pip section
                if (
                    depattrs.get("source", None) == "pypi"
                    or poetry_version_spec is None
                ):
                    manager = "pip"
                # TODO: support additional features such as markers for things like sys_platform, platform_system
            elif isinstance(depattrs, str):
                poetry_version_spec = depattrs
            else:
                raise TypeError(
                    f"Unsupported type for dependency: {depname}: {depattrs}"
                )
            if manager == "conda":
                name = normalize_pypi_name(depname)
                version = poetry_version_to_conda_version(poetry_version_spec)
            else:
                name = depname
                version = poetry_version_spec
            if version is None:
                if url is None:
                    raise ValueError(
                        f"dependency {depname} has neither version nor url"
                    )
                url, hashes = urldefrag(url)
                dependencies.append(
                    URLDependency(
                        name=name,
                        url=url,
                        hashes=[hashes],
                        manager=manager,
                        optional=optional,
                        category=category,
                        extras=extras,
                    )
                )
            else:
                dependencies.append(
                    VersionedDependency(
                        name=name,
                        version=version,
                        manager=manager,
                        optional=optional,
                        category=category,
                        extras=extras,
                    )
                )

    return specification_with_dependencies(path, contents, dependencies)


def specification_with_dependencies(
    path: pathlib.Path, toml_contents: Mapping[str, Any], dependencies: List[Dependency]
) -> LockSpecification:
    for depname, depattrs in get_in(
        ["tool", "conda-lock", "dependencies"], toml_contents, {}
    ).items():
        if isinstance(depattrs, str):
            conda_version = depattrs
        else:
            raise TypeError(f"Unsupported type for dependency: {depname}: {depattrs:r}")
        dependencies.append(
            VersionedDependency(
                name=depname,
                version=conda_version,
                manager="conda",
                optional=False,
                category="main",
                extras=[],
            )
        )

    return LockSpecification(
        dependencies=dependencies,
        channels=get_in(["tool", "conda-lock", "channels"], toml_contents, []),
        platforms=get_in(["tool", "conda-lock", "platforms"], toml_contents, []),
        sources=[path],
    )


def to_match_spec(conda_dep_name: str, conda_version: Optional[str]) -> str:
    if conda_version:
        spec = f"{conda_dep_name} {conda_version}"
    else:
        spec = f"{conda_dep_name}"
    return spec


def parse_pyproject_toml(
    pyproject_toml: pathlib.Path,
) -> LockSpecification:
    with pyproject_toml.open("rb") as fp:
        contents = toml_load(fp)
    build_system = get_in(["build-system", "build-backend"], contents)
    pep_621_probe = get_in(["project", "dependencies"], contents)
    pdm_probe = get_in(["tool", "pdm"], contents)
    parse = parse_poetry_pyproject_toml
    if pep_621_probe is not None:
        if pdm_probe is None:
            parse = partial(
                parse_requirements_pyproject_toml,
                prefix=("project",),
                main_tag="dependencies",
                optional_tag="optional-dependencies",
            )
        else:
            parse = parse_pdm_pyproject_toml
    elif build_system.startswith("poetry"):
        parse = parse_poetry_pyproject_toml
    elif build_system.startswith("flit"):
        parse = partial(
            parse_requirements_pyproject_toml,
            prefix=("tool", "flit", "metadata"),
            main_tag="requires",
            optional_tag="requires-extra",
        )
    else:
        import warnings

        warnings.warn(
            "Could not detect build-system in pyproject.toml.  Assuming poetry"
        )

    return parse(pyproject_toml, contents)


def parse_python_requirement(
    requirement: str,
    manager: Literal["conda", "pip"] = "conda",
    optional: bool = False,
    category: str = "main",
    normalize_name: bool = True,
) -> Dependency:
    """Parse a requirements.txt like requirement to a conda spec"""
    requirement_specifier = requirement.split(";")[0].strip()
    from pkg_resources import Requirement

    parsed_req = Requirement.parse(requirement_specifier)
    name = parsed_req.unsafe_name
    collapsed_version = ",".join("".join(spec) for spec in parsed_req.specs)
    conda_version = poetry_version_to_conda_version(collapsed_version)

    if normalize_name:
        conda_dep_name = normalize_pypi_name(name)
    else:
        conda_dep_name = name
    extras = list(parsed_req.extras)

    if parsed_req.url:  # type: ignore[attr-defined]
        assert conda_version in {"", "*", None}
        url, frag = urldefrag(parsed_req.url)  # type: ignore[attr-defined]
        return URLDependency(
            name=conda_dep_name,
            manager=manager,
            optional=optional,
            category=category,
            extras=extras,
            url=url,
            hashes=[frag.replace("=", ":")],
        )
    else:
        return VersionedDependency(
            name=conda_dep_name,
            version=conda_version or "*",
            manager=manager,
            optional=optional,
            category=category,
            extras=extras,
        )


def parse_requirements_pyproject_toml(
    pyproject_toml_path: pathlib.Path,
    contents: Mapping[str, Any],
    prefix: Sequence[str],
    main_tag: str,
    optional_tag: str,
    dev_tags: AbstractSet[str] = {"dev", "test"},
) -> LockSpecification:
    """
    PEP621 and flit
    """
    dependencies: List[Dependency] = []

    sections = {(*prefix, main_tag): "main"}
    for extra in dev_tags:
        sections[(*prefix, optional_tag, extra)] = "dev"
    for extra in set(get_in([*prefix, optional_tag], contents, {}).keys()).difference(
        dev_tags
    ):
        sections[(*prefix, optional_tag, extra)] = extra

    for path, category in sections.items():
        for dep in get_in(list(path), contents, []):
            dependencies.append(
                parse_python_requirement(
                    dep, manager="conda", category=category, optional=category != "main"
                )
            )

    return specification_with_dependencies(pyproject_toml_path, contents, dependencies)


def parse_pdm_pyproject_toml(
    path: pathlib.Path,
    contents: Mapping[str, Any],
) -> LockSpecification:
    """
    PDM support. First, a regular PEP621 pass; then, add all dependencies listed
    in the 'tool.pdm.dev-dependencies' table with the 'dev' category.
    """
    res = parse_requirements_pyproject_toml(
        path,
        contents,
        prefix=("project",),
        main_tag="dependencies",
        optional_tag="optional-dependencies",
    )

    dev_reqs = []

    for section, deps in get_in(["tool", "pdm", "dev-dependencies"], contents).items():
        dev_reqs.extend(
            [
                parse_python_requirement(
                    dep, manager="conda", category="dev", optional=True
                )
                for dep in deps
            ]
        )

    res.dependencies.extend(dev_reqs)

    return res
