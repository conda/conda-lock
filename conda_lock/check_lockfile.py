import logging
import pathlib

from collections import defaultdict
import re
from typing import AbstractSet, List, Optional, Sequence

import yaml

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from conda_lock._vendor.poetry.core.constraints.version import (
    parse_constraint as _parse_constraint,
)

# from poetry.core.semver import parse_constraint  # wrong way to use vendored poetry
from conda_lock.conda_lock import _compute_filtered_categories, _detect_lockfile_kind
from conda_lock.lockfile import parse_conda_lock_file
from conda_lock.lockfile.v2prelim.models import LockedDependency, Lockfile
from conda_lock.models.lock_spec import (
    Dependency,
    LockSpecification,
    PathDependency,
    URLDependency,
    VCSDependency,
    VersionedDependency,
)
from conda_lock.src_parser import make_lock_spec
from conda_lock.virtual_package import default_virtual_package_repodata


logger = logging.getLogger(__name__)


def _create_lock_spec_for_check(
    files: List[pathlib.Path],
    mapping_url: str,
    channel_overrides: Optional[Sequence[str]],
    platform_overrides: Optional[Sequence[str]],
    include_dev_dependencies: bool,
    extras: Optional[AbstractSet[str]],
    filter_categories: bool,
    with_cuda: Optional[str],
) -> Optional[LockSpecification]:
    """
    Create a lock specification for checking against a lockfile.

    Args:
        files: List of source files (e.g., pyproject.toml, environment.yml).
        mapping_url: URL to the mapping file for converting package names.
        channel_overrides: Sequence of channels to override those in source files.
        platform_overrides: Sequence of platforms to override those in source files.
        include_dev_dependencies: Whether to include development dependencies.
        extras: Optional set of extras to include.
        filter_categories: Whether to filter dependencies by categories.
        with_cuda: CUDA version to assume for virtual packages.

    Returns:
        A LockSpecification object, or None if an error occurs.
    """
    try:
        filtered_categories: Optional[AbstractSet[str]] = None
        if filter_categories:
            filtered_categories = _compute_filtered_categories(
                include_dev_dependencies=include_dev_dependencies, extras=extras
            )
        if with_cuda is None:
            with_cuda = "default"

        virtual_package_repo = default_virtual_package_repodata(cuda_version=with_cuda)
        with virtual_package_repo:
            return make_lock_spec(
                src_files=files,
                mapping_url=mapping_url,
                channel_overrides=channel_overrides,
                platform_overrides=platform_overrides,
                filtered_categories=filtered_categories,
            )
    except (FileNotFoundError, OSError) as e:
        logger.exception(f"Error creating lock spec from {files}: {e}")
        return None


def _check_packages(
    lockfile_path: pathlib.Path,
    files: List[pathlib.Path],
    platform: str,
    lockfile_packages: List[LockedDependency],
    spec_packages: list[Dependency],
) -> bool:
    """
    Compare packages for a given platform between the lockfile and the lock spec.

    Packages are assumed to have already been filtered by platform and category.

    This ensures that:
    1. No spec dependencies are missing from the lockfile.
    2. No extra packages in the lockfile that are not in the spec.
    3. The versions of the packages in the lockfile are valid against the spec.

    Args:
        lockfile_path: Path to the lockfile, used for error messages.
        files: List of source files, used for error messages.
        platform: The platform being checked.
        lockfile_packages: List of locked dependencies from the lockfile for the platform.
        spec_packages: List of dependency from the lock specification for the platform.

    Returns:
        True if packages are consistent, False otherwise.
    """
    all_lockfile_pkgs = {p.name for p in lockfile_packages}

    all_dependency_names = set()
    for pkg in lockfile_packages:
        if pkg.dependencies:
            all_dependency_names.update(pkg.dependencies.keys())

    lockfile_root_pkg_names = all_lockfile_pkgs - all_dependency_names
    spec_pkgs_dict = {p.name: p for p in spec_packages}

    logger.debug(f"Root packages for {platform}: {lockfile_root_pkg_names}")

    # typically this will be a lockfile root pkg within the spec constraints
    def pkg_within_constraints(pkg: LockedDependency, constraints: Dependency) -> bool:
        """
        # Dependency
        # VCSDependency
        """
        if isinstance(constraints, VersionedDependency):
            if constraints.manager == "pip":
                constraint = SpecifierSet(
                    convert_poetry_to_pep440(constraints.version))
            else:
                constraint = constraints.version
            return Version(pkg.version) in SpecifierSet(constraint)
        elif isinstance(constraints, URLDependency):
            # TODO: check somehow
            logger.warning(
                f"URLDependency {constraints.name} is not checked for version compatibility.")
            return True
        elif isinstance(constraints, VCSDependency):
            # TODO: check somehow
            logging.warning(
                f"VCSDependency {constraints.name} is not checked for version compatibility.")
            # check if vcs matches?
            return True
        elif isinstance(constraints, PathDependency):
            # TODO: check somehow
            logger.warning(
                f"PathDependency {constraints.name} is not checked for version compatibility.")
            return True
        else:
            raise Exception(f"Unhandled dependency type: {type(constraints)}")

    for lockfile_pkg in lockfile_packages:
        # filter out non-root packages
        if lockfile_pkg.name not in lockfile_root_pkg_names:
            continue

        # check that root package is within the env spec constraints
        if (lockfile_pkg.name not in spec_pkgs_dict) or (
            not pkg_within_constraints(
                pkg=lockfile_pkg,
                constraints=spec_pkgs_dict[lockfile_pkg.name],
            )
        ):
            logger.error(
                f"For platform {platform}, {lockfile_path.name} has root package {lockfile_pkg.name} "
                f"which is not in the lock specification or does not match the constraints: {spec_pkgs_dict.get(lockfile_pkg.name)}. "
                "Run `conda-lock lock` to update the lockfile."
            )
            return False

    # ensure no extra packages in the lockfile
    if not lockfile_root_pkg_names.issubset(spec_pkgs_dict.keys()):
        extra_packages = lockfile_root_pkg_names - spec_pkgs_dict.keys()
        logger.error(
            f"For platform {platform}, {lockfile_path.name} contains packages not required by the lockspec: {extra_packages} "
            "Run `conda-lock lock` to update the lockfile."
        )
        return False
    return True


def _check_platform_dependencies(
    lockfile_path: pathlib.Path,
    files: List[pathlib.Path],
    lockfile_obj: Lockfile,
    env_spec: LockSpecification,
    platform: str,
    categories_to_check: AbstractSet[str],
    kind: str,
) -> bool:
    """
    Check dependencies for a single platform.

    This function dispatches to the correct comparison logic based on the lockfile kind
    and categories.

    Args:
        lockfile_path: Path to the lockfile.
        files: List of source files.
        lockfile_obj: The parsed Lockfile object.
        lock_spec: The LockSpecification object.
        platform: The platform to check.
        categories_to_check: Set of categories to check.
        kind: The kind of lockfile.

    Returns:
        True if dependencies are consistent, False otherwise.
    """
    logger.info(f"Checking platform {platform}...")

    if kind != "lock":
        raise NotImplementedError(
            f"Lockfile kind {kind} is not supported for checking dependencies."
        )
    # implementation detail: we should split the spec and lockfile packages by category
    # and compare them separately

    # TODO: check conda and pip managed stuff separately or just assume pip takes precedence over conda?  # https://github.com/conda/conda-lock/issues/479#issuecomment-2992825287

    # env_spec := dict[str, List[Dependency]]
    # I want a data class which holds the packages easy to filter by platform, category, and manager

    class EnvSpecFilter:
        def __init__(self, env_spec: LockSpecification, platform: str):
            self._inner_data_structure = defaultdict(lambda: defaultdict(list))
            for dep in env_spec.dependencies.get(platform, []):
                if dep.category in categories_to_check:
                    dep.model_config["frozen"] = True
                    self._inner_data_structure[dep.category][dep.manager].append(dep)

        def filtered_env_spec(self, category, manager):
            return self._inner_data_structure.get(category, {}).get(manager, [])

    env_spec_filter = EnvSpecFilter(env_spec, platform)

    # in lock lockfiles we can compare categories, to ensure those are correct
    for category in categories_to_check:
        for manager in ("conda", "pip"):
            # filter packages by manager
            filtered_lockfile_pkgs = [
                p
                for p in lockfile_obj.package
                if (p.platform == platform)
                and (category in p.categories)
                and (p.manager == manager)
            ]

            if not _check_packages(
                lockfile_path=lockfile_path,
                files=files,
                platform=platform,
                lockfile_packages=filtered_lockfile_pkgs,
                spec_packages=env_spec_filter.filtered_env_spec(
                    category=category, manager=manager
                ),
            ):
                return False
    return True


def check_lockfile(
    lockfile_path: pathlib.Path,
    files: List[pathlib.Path],
    mapping_url: str,
    channel_overrides: Optional[Sequence[str]] = None,
    platform_overrides: Optional[Sequence[str]] = None,
    include_dev_dependencies: bool = True,
    extras: Optional[AbstractSet[str]] = None,
    filter_categories: bool = False,
    with_cuda: Optional[str] = None,
) -> bool:
    """
    Check if a lockfile is in sync with the source files.

    Args:
        lockfile_path: Path to the conda-lock.yml file.
        files: List of source files to generate a lock specification from.
        mapping_url: URL to the mapping file.
        channel_overrides: A list of channels to override the channels in the lock specification.
        platform_overrides: A list of platforms to override the platforms in the lock specification.
        include_dev_dependencies: If true, include dev dependencies in the lock specification.
        extras: A set of extras to include in the lock specification.
        filter_categories: If true, filter the lock specification by categories.
        with_cuda: The version of cuda to use for virtual packages.

    Returns:
        True if validation passes, False if there are issues.
    """

    if not lockfile_path.exists():
        logger.error(f"Error: {lockfile_path} not found")
        return False

    try:
        lockfile_obj = parse_conda_lock_file(lockfile_path)
    except (yaml.error.YAMLError, FileNotFoundError):
        logger.exception(f"Error reading {lockfile_path}")
        return False

    lock_spec = _create_lock_spec_for_check(
        files=files,
        mapping_url=mapping_url,
        channel_overrides=channel_overrides,
        platform_overrides=platform_overrides,
        include_dev_dependencies=include_dev_dependencies,
        extras=extras,
        filter_categories=filter_categories,
        with_cuda=with_cuda,
    )
    if lock_spec is None:
        return False

    platforms_in_lockfile = set(lockfile_obj.metadata.platforms)
    platforms_in_spec = set(lock_spec.platforms)

    platforms_to_check = sorted(
        list(platforms_in_lockfile.intersection(platforms_in_spec))
    )

    if not platforms_to_check:
        logger.error("No common platforms found between lockfile and source files.")
        return False

    categories_to_check = _compute_filtered_categories(
        include_dev_dependencies=include_dev_dependencies, extras=extras
    )
    kind = _detect_lockfile_kind(lockfile_path)

    for platform in platforms_to_check:
        # main function
        if not _check_platform_dependencies(
            lockfile_path=lockfile_path,
            files=files,
            lockfile_obj=lockfile_obj,
            env_spec=lock_spec,
            platform=platform,
            categories_to_check=categories_to_check,
            kind=kind,
        ):
            return False

    logger.info(
        f"{lockfile_path.name} successfully validated for platforms: {', '.join(platforms_to_check)}"
    )
    return True

def convert_poetry_to_pep440(poetry_spec: str) -> str:
    """
    Converts a Poetry version specification to a PEP 440-compliant
    string for packaging.specifiers.SpecifierSet.

    Args:
        poetry_spec: A string containing a Poetry version specification.

    Returns:
        A PEP 440-compliant version specifier string.
    """
    if not isinstance(poetry_spec, str):
        raise TypeError("The poetry_spec must be a string.")

    spec_parts = []
    for part in poetry_spec.split(','):
        part = part.strip()
        if part.startswith(('>=', '<=', '==', '!=', '>', '<')):
            spec_parts.append(part)
        elif part.startswith('^'):
            version_str = part[1:]
            version = Version(version_str)
            parts = list(version.release)
            if version.major != 0:
                upper_bound = f"{version.major + 1}.0.0"
            elif version.minor != 0:
                upper_bound = f"0.{version.minor + 1}.0"
            else:
                upper_bound = f"0.0.{version.micro + 1}"
            spec_parts.append(f">={version_str},<{upper_bound}")
        elif part.startswith('~'):
            version_str = part[1:]
            version = Version(version_str)
            parts = list(version.release)
            if len(parts) == 3:
                upper_bound = f"{version.major}.{version.minor + 1}.0"
            elif len(parts) == 2:
                upper_bound = f"{version.major}.{version.minor + 1}.0"
            else:
                upper_bound = f"{version.major + 1}.0.0"
            spec_parts.append(f">={version_str},<{upper_bound}")
        elif '*' in part:
            if part == '*':
                spec_parts.append(">=0.0.0")
            else:
                base_version = part.replace('*', '0')
                version = Version(base_version)
                parts = list(version.release)
                if len(parts) == 2:
                    upper_bound = f"{version.major + 1}.0.0"
                else: # len(parts) == 3
                    upper_bound = f"{version.major}.{version.minor + 1}.0"
                spec_parts.append(f">={base_version},<{upper_bound}")
        else:
            # Assumes an exact version or already compliant specifier
            if re.match(r'^\d+(\.\d+)*$', part):
                spec_parts.append(f"=={part}")
            else:
                spec_parts.append(part)

    return ",".join(spec_parts)
