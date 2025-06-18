import logging
import pathlib

from typing import AbstractSet, List, Optional, Sequence

import yaml

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from conda_lock.conda_lock import _compute_filtered_categories, _detect_lockfile_kind
from conda_lock.lockfile import parse_conda_lock_file
from conda_lock.lockfile.v2prelim.models import LockedDependency, Lockfile
from conda_lock.models.lock_spec import Dependency, LockSpecification
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


def _compare_packages(
    lockfile_path: pathlib.Path,
    files: List[pathlib.Path],
    platform: str,
    lockfile_packages: List[LockedDependency],
    spec_packages: List[Dependency],
) -> bool:
    """
    Compare packages for a given platform between the lockfile and the lock spec.

    This ensures that:
    1. Every dependency in the spec is present in the lockfile for the platform.
    2. There are no extra root packages in the lockfile that are not in the spec.

    Args:
        lockfile_path: Path to the lockfile, used for error messages.
        files: List of source files, used for error messages.
        platform: The platform being checked.
        lockfile_packages: List of locked dependencies from the lockfile for the platform.
        spec_packages: List of dependency from the lock specification for the platform.

    Returns:
        True if packages are consistent, False otherwise.
    """
    all_lockfile_packages_for_platform = {p.name for p in lockfile_packages}

    all_dependency_names = set()
    for pkg in lockfile_packages:
        if pkg.dependencies:
            all_dependency_names.update(pkg.dependencies.keys())

    lockfile_root_packages_for_platform = (
        all_lockfile_packages_for_platform - all_dependency_names
    )

    logger.debug(f"Root packages for {platform}: {lockfile_root_packages_for_platform}")

    # LEFT OFF
    spec_packages_dict = {p.name: p.version for p in spec_packages}
    for lockfile_pkg in lockfile_packages:
        assert lockfile_pkg.name in spec_packages_dict
        assert SpecifierSet(spec_packages_dict[lockfile_pkg.name]).contains(
            Version(lockfile_pkg.version)
        )
    
    # ensure every dependency in the spec is in the lockfile and makes sure lockfile version is valid against lockfile spec
    if not spec_packages <= all_lockfile_packages_for_platform:
        missing_packages = spec_packages - all_lockfile_packages_for_platform
        logger.error(
            f"For platform {platform}, {lockfile_path.name} is missing packages required "
            f"by {', '.join(str(f) for f in files)}: {missing_packages}. "
            "Run `conda-lock lock` to update the lockfile."
        )
        return False

    # ensure no extra packages in the lockfile
    if not lockfile_root_packages_for_platform.issubset(spec_packages):
        extra_packages = lockfile_root_packages_for_platform - spec_packages
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
    lock_spec: LockSpecification,
    platform: str,
    categories_to_check: AbstractSet[str],
    filter_categories: bool,
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
        filter_categories: Whether to filter categories.
        kind: The kind of lockfile.

    Returns:
        True if dependencies are consistent, False otherwise.
    """
    logger.info(f"Checking platform {platform}...")

    if kind != "lock":
        lockfile_pkgs_to_check = [
            p
            for p in lockfile_obj.package
            if p.platform == platform
            and (
                not filter_categories or p.categories.intersection(categories_to_check)
            )
        ]
        return _compare_packages(
            lockfile_path=lockfile_path,
            files=files,
            platform=platform,
            lockfile_packages=lockfile_pkgs_to_check,
            spec_packages=lock_spec.dependencies.get(platform, []),
        )
    else:
        for category in categories_to_check:
            lockfile_pkgs_to_check = [
                p
                for p in lockfile_obj.package
                if p.platform == platform and category in p.categories
            ]
            spec_packages_for_platform = [
                d
                for d in lock_spec.dependencies.get(platform, [])
                if d.category == category
            ]
            if not _compare_packages(
                lockfile_path=lockfile_path,
                files=files,
                platform=platform,
                lockfile_packages=lockfile_pkgs_to_check,
                spec_packages=spec_packages_for_platform,
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
        if not _check_platform_dependencies(
            lockfile_path=lockfile_path,
            files=files,
            lockfile_obj=lockfile_obj,
            lock_spec=lock_spec,
            platform=platform,
            categories_to_check=categories_to_check,
            filter_categories=filter_categories,
            kind=kind,
        ):
            return False

    logger.info(
        f"{lockfile_path.name} successfully validated for platforms: {', '.join(platforms_to_check)}"
    )
    return True
