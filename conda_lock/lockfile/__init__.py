import pathlib

from collections import defaultdict
from textwrap import dedent
from typing import Collection, Dict, List, Mapping, Optional, Sequence, Set, Union

import yaml

from conda_lock.lockfile.v1.models import Lockfile as LockfileV1
from conda_lock.lockfile.v2prelim.models import (
    LockedDependency,
    Lockfile,
    MetadataOption,
    lockfile_v1_to_v2,
)
from conda_lock.lookup import conda_name_to_pypi_name
from conda_lock.models.lock_spec import Dependency


class MissingLockfileVersion(ValueError):
    pass


class UnknownLockfileVersion(ValueError):
    pass


def _seperator_munge_get(
    d: Mapping[str, Union[List[LockedDependency], LockedDependency]], key: str
) -> Union[List[LockedDependency], LockedDependency]:
    # since separators are not consistent across managers (or even within) we need to do some double attempts here
    try:
        return d[key]
    except KeyError:
        try:
            return d[key.replace("-", "_")]
        except KeyError:
            return d[key.replace("_", "-")]


def apply_categories(
    requested: Dict[str, Dependency],
    planned: Mapping[str, Union[List[LockedDependency], LockedDependency]],
    categories: Sequence[str] = ("main", "dev"),
    convert_to_pip_names: bool = False,
) -> None:
    """map each package onto the root request the with the highest-priority category"""

    # requested is a dictionary of packages that were requested (keys are the package
    # names requested). These can either be pip package names or conda package names
    # if pip package names, convert_to_pip_names will be True
    #
    # planned is the set of packages that are planned to be installed. The key is
    # again the name of the package. The names of the packages in planned and
    # requested are consistent (ie: all "pip" names or all "conda" names)
    #
    # convert_to_pip_names indicates that the names in requested and planned are
    # pip names and that, if a conda name is encountered, it should be converted to
    # a pip name

    # walk dependency tree to assemble all transitive dependencies by request
    dependents: Dict[str, Set[str]] = {}
    by_category = defaultdict(list)

    def extract_planned_items(
        planned_items: Union[List[LockedDependency], LockedDependency],
    ) -> List[LockedDependency]:
        if not isinstance(planned_items, list):
            return [planned_items]

        return [
            item
            for item in planned_items
            if dep_name(item.manager, item.name) not in deps
        ]

    def dep_name(manager: str, dep: str) -> str:
        # If we operate on lists of pip names and this is a conda dependency, we
        # convert the name to a pip name.
        if convert_to_pip_names and manager == "conda":
            return conda_name_to_pypi_name(dep)
        return dep

    for name, request in requested.items():
        todo: List[str] = list()
        deps: Set[str] = set()
        item = name

        # Loop around all the transitive dependencies of name
        while True:
            # Get all the LockedDependency that correspond to this requested item.
            # Note that there may be multiple of them because, if, for example,
            # the user requests `dask` as a pip package, it may map to `dask` and
            # `dask-core` as packages that are planned to be installed.
            planned_items = extract_planned_items(_seperator_munge_get(planned, item))

            for planned_item in planned_items:
                todo.extend(
                    dep_name(planned_item.manager, dep)
                    for dep in planned_item.dependencies
                    # exclude virtual packages
                    if not (dep in deps or dep.startswith("__"))
                )
            if todo:
                item = todo.pop(0)
                deps.add(item)
            else:
                break

        dependents[name] = deps

        by_category[request.category].append(request.name)

    # now, map each package to its root request preferring the ones earlier in the
    # list
    categories = [*categories, *(k for k in by_category if k not in categories)]
    root_requests = {}
    for category in categories:
        for root in by_category.get(category, []):
            for transitive_dep in dependents[root]:
                if transitive_dep not in root_requests:
                    root_requests[transitive_dep] = root
    # include root requests themselves
    for name in requested:
        root_requests[name] = name

    for dep, root in root_requests.items():
        source = requested[root]
        # try a conda target first
        targets = _seperator_munge_get(planned, dep)
        if not isinstance(targets, list):
            targets = [targets]
        for target in targets:
            target.category = source.category


def parse_conda_lock_file(path: pathlib.Path) -> Lockfile:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")

    with path.open() as f:
        content = yaml.safe_load(f)
    version = content.pop("version", None)
    if version == 1:
        lockfile = lockfile_v1_to_v2(LockfileV1.parse_obj(content))
    elif version == 2:
        lockfile = Lockfile.parse_obj(content)
    elif version is None:
        raise MissingLockfileVersion(f"{path} is missing a version")
    else:
        raise UnknownLockfileVersion(f"{path} has unknown version {version}")
    lockfile.toposort_inplace()
    return lockfile


def write_conda_lock_file(
    content: Lockfile,
    path: pathlib.Path,
    metadata_choices: Optional[Collection[MetadataOption]],
    include_help_text: bool = True,
) -> None:
    content.alphasort_inplace()
    content.filter_virtual_packages_inplace()
    with path.open("w") as f:
        if include_help_text:
            categories = set(p.category for p in content.package)

            def write_section(text: str) -> None:
                lines = dedent(text).split("\n")
                for idx, line in enumerate(lines):
                    if (idx == 0 or idx == len(lines) - 1) and len(line) == 0:
                        continue
                    print(("# " + line).rstrip(), file=f)

            metadata_flags: str = (
                " ".join([f"--md {md.value}" for md in metadata_choices])
                if metadata_choices is not None and len(metadata_choices) != 0
                else ""
            )

            write_section(
                f"""
                This lock file was generated by conda-lock (https://github.com/conda/conda-lock). DO NOT EDIT!

                A "lock file" contains a concrete list of package versions (with checksums) to be installed. Unlike
                e.g. `conda env create`, the resulting environment will not change as new package versions become
                available, unless you explicitly update the lock file.

                Install this environment as "YOURENV" with:
                    conda-lock install -n YOURENV {path.name}
                """
            )
            if "dev" in categories:
                write_section(
                    f"""
                    This lock contains optional development dependencies. Include them in the installed environment with:
                        conda-lock install --dev-dependencies -n YOURENV {path.name}
                    """
                )
            extras = sorted(categories.difference({"main", "dev"}))
            if extras:
                write_section(
                    f"""
                    This lock contains optional dependency categories {', '.join(extras)}. Include them in the installed environment with:
                        conda-lock install {' '.join('-e '+extra for extra in extras)} -n YOURENV {path.name}
                    """
                )
            write_section(
                f"""
                To update a single package to the latest version compatible with the version constraints in the source:
                    conda-lock lock {metadata_flags} --lockfile {path.name} --update PACKAGE
                To re-solve the entire environment, e.g. after changing a version constraint in the source file:
                    conda-lock {metadata_flags}{' '.join('-f '+path for path in content.metadata.sources)} --lockfile {path.name}
                """
            )
        output = content.to_v1().dict_for_output()
        yaml.dump(output, stream=f, sort_keys=False)
