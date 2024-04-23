import collections
import collections.abc
import logging
import pathlib
import sys
import warnings

from functools import partial
from typing import (
    AbstractSet,
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)
from urllib.parse import urldefrag


if sys.version_info >= (3, 11):
    from tomllib import load as toml_load
else:
    from tomli import load as toml_load

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name as canonicalize_pypi_name
from typing_extensions import Literal

from conda_lock.common import get_in
from conda_lock.lookup import get_forward_lookup as get_lookup
from conda_lock.models.lock_spec import (
    Dependency,
    LockSpecification,
    PoetryMappedDependencySpec,
    URLDependency,
    VCSDependency,
    VersionedDependency,
)
from conda_lock.src_parser.conda_common import conda_spec_to_versioned_dep


POETRY_INVALID_EXTRA_LOC = (
    "`{depname}` in file {filename} is part of the `{category}` extra "
    "but is not defined in [tool.poetry.dependencies]. "
    "Conda-Lock will treat it as part of the extra. "
    "Note that Poetry may have different behavior."
)

POETRY_EXTRA_NOT_OPTIONAL = (
    "`{depname}` in file {filename} is part of the `{category}` extra "
    "but is not specified as optional. "
    "Conda-Lock will treat it as part of the extra. "
    "Note that Poetry may have different behavior."
)

POETRY_OPTIONAL_NO_EXTRA = (
    "`{depname}` in file {filename} is specified as optional but is not in any extra. "
    "Conda-Lock will treat it as part of the `main` category. "
    "Note that Poetry may have different behavior."
)

POETRY_OPTIONAL_NOT_MAIN = (
    "`{depname}` in file {filename} is specified with the `optional` flag. "
    "Conda-Lock will follows Poetry behavior and ignore the flag. "
    "It will be treated as part of the `{category}` category."
)


def join_version_components(pieces: Sequence[Union[str, int]]) -> str:
    return ".".join(str(p) for p in pieces)


def normalize_pypi_name(name: str) -> str:
    cname = canonicalize_pypi_name(name)
    if cname in get_lookup():
        lookup = get_lookup()[cname]
        res = lookup.get("conda_name") or lookup.get("conda_forge")
        if res is not None:
            return res
        else:
            logging.warning(
                f"Could not find conda name for {cname}. Assuming identity."
            )
            return cname
    else:
        return cname


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


def handle_mapping(
    depattrs: collections.abc.Mapping,
    depname: str,
    path: pathlib.Path,
    category: str,
    in_extra: bool,
    default_category: str,
    manager: Literal["conda", "pip"],
    poetry_version_spec: Optional[str],
) -> PoetryMappedDependencySpec:
    """Handle a dependency in mapping form from a pyproject.toml file"""
    if "git" in depattrs:
        url: Optional[str] = depattrs.get("git", None)
        manager = "pip"
    else:
        poetry_version_spec = depattrs.get("version", None)
        url = depattrs.get("url", None)
    extras = depattrs.get("extras", [])
    optional_flag: Optional[bool] = depattrs.get("optional")

    # `optional = true` must be set if dependency is
    # inside main and part of an extra
    if optional_flag is not True and in_extra:
        warnings.warn(
            POETRY_EXTRA_NOT_OPTIONAL.format(
                depname=depname, filename=path.name, category=category
            )
        )

    # Will ignore `optional = true` if in `tool.poetry.dependencies`
    # but not in an extra
    if optional_flag is True and not in_extra and category == "main":
        warnings.warn(
            POETRY_OPTIONAL_NO_EXTRA.format(depname=depname, filename=path.name)
        )

    # Will always ignore optional flag if not in `tool.poetry.dependencies`
    if optional_flag is not None and default_category != "main":
        warnings.warn(
            POETRY_OPTIONAL_NOT_MAIN.format(
                depname=depname, filename=path.name, category=category
            )
        )

    # If a dependency is explicitly marked as sourced from pypi,
    # or is a URL dependency, delegate to the pip section
    if depattrs.get("source", None) == "pypi" or poetry_version_spec is None:
        manager = "pip"
    # TODO: support additional features such as markers for things like sys_platform, platform_system
    return PoetryMappedDependencySpec(
        url=url, manager=manager, extras=extras, poetry_version_spec=poetry_version_spec
    )


def parse_poetry_pyproject_toml(
    path: pathlib.Path,
    platforms: List[str],
    contents: Mapping[str, Any],
) -> LockSpecification:
    """
    Parse dependencies from a poetry pyproject.toml file

    Each dependency is assigned a category depending on which section it appears in:
    * dependencies in [tool.poetry.dependencies] have category main
    * dependencies in [tool.poetry.dev-dependencies] have category dev
    * dependencies in each `key` of [tool.poetry.extras] have category `key`
    * dependencies in [tool.poetry.{group}.dependencies] have category `group`

    * By default, dependency names are translated to the conda equivalent, with three exceptions:
        - If a dependency has `source = "pypi"`, it is treated as a pip dependency (by name)
        - If a dependency has a url, it is treated as a direct pip dependency (by url)
        - If all dependencies are default-sourced to pip, `default-non-conda-source = "pip"`

    * markers are not supported

    """
    dependencies: List[Dependency] = []

    categories: Dict[Tuple[str, ...], str] = {
        ("dependencies",): "main",
        ("dev-dependencies",): "dev",
    }

    dep_to_extra = {}
    for cat, deps in get_in(["tool", "poetry", "extras"], contents, {}).items():
        for dep in deps:
            dep_to_extra[dep] = cat

    # Support for poetry dependency groups as specified in
    # https://python-poetry.org/docs/managing-dependencies/#optional-groups
    for group_name, _ in get_in(["tool", "poetry", "group"], contents, {}).items():
        group_key = tuple(["group", group_name, "dependencies"])
        categories[group_key] = group_name

    default_non_conda_source = get_in(
        ["tool", "conda-lock", "default-non-conda-source"],
        contents,
        "conda",
    )
    for section, default_category in categories.items():
        for depname, depattrs in get_in(
            ["tool", "poetry", *section], contents, {}
        ).items():
            category: str = dep_to_extra.get(depname) or default_category
            manager: Literal["conda", "pip"] = default_non_conda_source
            url = None
            extras: List[Any] = []
            in_extra: bool = False

            # Poetry spec includes Python version in "tool.poetry.dependencies"
            # Cannot be managed by pip
            if depname == "python":
                manager = "conda"

            # Extras can only be defined in `tool.poetry.dependencies`
            if default_category == "main":
                in_extra = category != "main"
            elif category != default_category:
                warnings.warn(
                    POETRY_INVALID_EXTRA_LOC.format(
                        depname=depname, filename=path.name, category=category
                    )
                )
            poetry_version_spec: Optional[str] = None
            if isinstance(depattrs, collections.abc.Mapping):
                pvs = handle_mapping(
                    depattrs,
                    depname,
                    path,
                    category,
                    in_extra,
                    default_category,
                    manager,
                    poetry_version_spec,
                )
                url, manager, extras, poetry_version_spec = (
                    pvs.url,
                    pvs.manager,
                    pvs.extras,
                    pvs.poetry_version_spec,
                )

            elif isinstance(depattrs, str):
                poetry_version_spec = depattrs
                if in_extra:
                    warnings.warn(
                        POETRY_EXTRA_NOT_OPTIONAL.format(
                            depname=depname, filename=path.name, category=category
                        )
                    )

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

            if "git" in depattrs and url is not None:
                url, rev = unpack_git_url(url)
                dependencies.append(
                    VCSDependency(
                        name=name,
                        source=url,
                        manager=manager,
                        vcs="git",
                        rev=rev,
                    )
                )
            elif version is None:
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
                        category=category,
                        extras=extras,
                    )
                )

    return specification_with_dependencies(path, platforms, contents, dependencies)


def specification_with_dependencies(
    path: pathlib.Path,
    platforms: List[str],
    toml_contents: Mapping[str, Any],
    dependencies: List[Dependency],
) -> LockSpecification:
    force_pypi = set()
    for depname, depattrs in get_in(
        ["tool", "conda-lock", "dependencies"], toml_contents, {}
    ).items():
        if isinstance(depattrs, str):
            dependencies.append(
                conda_spec_to_versioned_dep(f"{depname} {depattrs}", "main")
            )
        elif isinstance(depattrs, collections.abc.Mapping):
            if depattrs.get("source", None) == "pypi":
                force_pypi.add(depname)
        else:
            raise TypeError(f"Unsupported type for dependency: {depname}: {depattrs:r}")

    if force_pypi:
        for dep in dependencies:
            if dep.name in force_pypi:
                dep.manager = "pip"

    channels = get_in(["tool", "conda-lock", "channels"], toml_contents, [])
    try:
        # conda-lock will use `--override-channels` so nodefaults is redundant.
        channels.remove("nodefaults")
    except ValueError:
        pass

    pip_repositories = get_in(
        ["tool", "conda-lock", "pip-repositories"], toml_contents, []
    )

    return LockSpecification(
        dependencies={platform: dependencies for platform in platforms},
        channels=channels,
        pip_repositories=pip_repositories,
        sources=[path],
        allow_pypi_requests=get_in(
            ["tool", "conda-lock", "allow-pypi-requests"], toml_contents, True
        ),
    )


def to_match_spec(conda_dep_name: str, conda_version: Optional[str]) -> str:
    if conda_version:
        spec = f"{conda_dep_name} {conda_version}"
    else:
        spec = f"{conda_dep_name}"
    return spec


class RequirementWithHash(Requirement):
    """Requirement with support for pip hash checking.

    Pip offers hash checking where the requirement string is
    my_package == 1.23 --hash=sha256:1234...
    """

    def __init__(self, requirement_string: str) -> None:
        try:
            requirement_string, hash = requirement_string.split(" --hash=")
        except ValueError:
            hash = None
        self.hash: Optional[str] = hash
        super().__init__(requirement_string)


def parse_requirement_specifier(
    requirement: str,
) -> RequirementWithHash:
    """Parse a url requirement to a conda spec"""
    if (
        requirement.startswith("git+")
        or requirement.startswith("https://")
        or requirement.startswith("ssh://")
    ):
        # Handle the case where only the URL is specified without a package name
        repo_name_and_maybe_tag = requirement.split("/")[-1]
        repo_name = repo_name_and_maybe_tag.split("@")[0]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        # Use the repo name as a placeholder for the package name
        return RequirementWithHash(f"{repo_name} @ {requirement}")
    else:
        return RequirementWithHash(requirement)


def unpack_git_url(url: str) -> Tuple[str, Optional[str]]:
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("git+"):
        url = url[4:]
    rev = None
    if "@" in url:
        try:
            url, rev = url.split("@")
        except ValueError:
            # SSH URLs can have multiple @s
            url1, url2, rev = url.split("@")
            url = f"{url1}@{url2}"
    return url, rev


def parse_python_requirement(
    requirement: str,
    manager: Literal["conda", "pip"] = "conda",
    category: str = "main",
    normalize_name: bool = True,
) -> Dependency:
    """Parse a requirements.txt like requirement to a conda spec"""
    parsed_req = parse_requirement_specifier(requirement)
    name = canonicalize_pypi_name(parsed_req.name)
    collapsed_version = str(parsed_req.specifier)
    conda_version = poetry_version_to_conda_version(collapsed_version)
    if conda_version:
        conda_version = ",".join(sorted(conda_version.split(",")))

    if normalize_name:
        conda_dep_name = normalize_pypi_name(name)
    else:
        conda_dep_name = name
    extras = list(parsed_req.extras)

    if parsed_req.url and parsed_req.url.startswith("git+"):
        url, rev = unpack_git_url(parsed_req.url)
        return VCSDependency(
            name=conda_dep_name,
            source=url,
            manager=manager,
            vcs="git",
            rev=rev,
        )
    elif parsed_req.url:
        assert conda_version in {"", "*", None}
        url, frag = urldefrag(parsed_req.url)
        return URLDependency(
            name=conda_dep_name,
            manager=manager,
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
            category=category,
            extras=extras,
            hash=parsed_req.hash,
        )


def parse_requirements_pyproject_toml(
    pyproject_toml_path: pathlib.Path,
    platforms: List[str],
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

    default_non_conda_source = get_in(
        ["tool", "conda-lock", "default-non-conda-source"],
        contents,
        "conda",
    )
    for path, category in sections.items():
        for dep in get_in(list(path), contents, []):
            dependencies.append(
                parse_python_requirement(
                    dep, manager=default_non_conda_source, category=category
                )
            )

    return specification_with_dependencies(
        pyproject_toml_path, platforms, contents, dependencies
    )


def parse_pdm_pyproject_toml(
    path: pathlib.Path,
    platforms: List[str],
    contents: Mapping[str, Any],
) -> LockSpecification:
    """
    PDM support. First, a regular PEP621 pass; then, add all dependencies listed
    in the 'tool.pdm.dev-dependencies' table with the 'dev' category.
    """
    res = parse_requirements_pyproject_toml(
        path,
        platforms,
        contents,
        prefix=("project",),
        main_tag="dependencies",
        optional_tag="optional-dependencies",
    )

    dev_reqs = []
    default_non_conda_source = get_in(
        ["tool", "conda-lock", "default-non-conda-source"],
        contents,
        "conda",
    )
    for section, deps in get_in(["tool", "pdm", "dev-dependencies"], contents).items():
        dev_reqs.extend(
            [
                parse_python_requirement(
                    dep, manager=default_non_conda_source, category="dev"
                )
                for dep in deps
            ]
        )

    for dep_list in res.dependencies.values():
        dep_list.extend(dev_reqs)

    return res


def parse_platforms_from_pyproject_toml(
    pyproject_toml: pathlib.Path,
) -> List[str]:
    with pyproject_toml.open("rb") as fp:
        contents = toml_load(fp)
    return get_in(["tool", "conda-lock", "platforms"], contents, [])


def parse_pyproject_toml(
    pyproject_toml: pathlib.Path,
    platforms: List[str],
) -> LockSpecification:
    with pyproject_toml.open("rb") as fp:
        contents = toml_load(fp)
    build_system = get_in(["build-system", "build-backend"], contents)

    if get_in(
        ["tool", "conda-lock", "skip-non-conda-lock"],
        contents,
        False,
    ):
        dependencies: List[Dependency] = []
        return specification_with_dependencies(
            pyproject_toml, platforms, contents, dependencies
        )

    if "dependencies" in get_in(["project", "dynamic"], contents, []):
        # In this case, the dependencies are not declaratively defined in the
        # pyproject.toml, so we can't parse them. Instead they are provided dynamically
        # during hte build process. For example, see
        # <https://pypi.org/project/hatch-requirements-txt/>.
        # To properly handle this case, we would need to build the project and then
        # extract the metadata with something like
        # <https://pypa-build.readthedocs.io/en/latest/api.html#module-build.util>.
        # For more details, see <https://peps.python.org/pep-0621/#dynamic>.
        logging.warning(
            "conda-lock does not yet support reading dynamic dependencies "
            "from pyproject.toml. They will be ignored."
        )
        pep_621_probe = None
    else:
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

    return parse(pyproject_toml, platforms, contents)
