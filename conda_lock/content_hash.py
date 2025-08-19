"""Compute the content hash of a lock specification.

The content hash is used with `conda-lock --check-content-hash` to avoid unnecessary
relocking when the lock specification is unchanged.

Note that the content hash depends not only on the LockSpecification object but
also the virtual package specification.

WARNING: The fundamental concept of a content hash is seriously flawed:
<https://github.com/conda/conda-lock/issues/432#issuecomment-1637071282>

It is important to maintain the content hash for backwards compatibility,
but we should stop using it in the future.

Note that anything that modifies the content hash is a breaking change,
since it will invalidate all existing lockspecs.

The content hash is computed by JSON-serializing the lock specification and
the virtual package specification, and then hashing the serialized
representation.
"""

import hashlib
import json

from collections.abc import Sequence
from copy import deepcopy
from typing import Optional, Union, cast

from conda_lock.content_hash_types import (
    EmptyDict,
    HashableVirtualPackageRepresentation,
    PlatformSubdirStr,
    SerializedDependency,
    SerializedLockspec,
    SubdirMetadata,
)
from conda_lock.models.lock_spec import LockSpecification
from conda_lock.virtual_package import FakeRepoData


def compute_content_hashes(
    lock_spec: LockSpecification,
    virtual_package_repo: Optional[FakeRepoData],
    reinsert_spurious_build_number: bool = True,
    remove_new_nulls: bool = True,
) -> dict[PlatformSubdirStr, str]:
    """Compute the content hashes for the given lock specification.

    Args:
        lock_spec: The lock specification to compute the content hashes for.

        virtual_package_repo: The virtual package repository to use.
            If None, the content hash is computed without the VPR.

        reinsert_spurious_build_number: Whether to reinsert the spurious build
            number in the build string of the VPR. This prevents the content hash
            from changing when upgrading from v2 to v3.0.3.

        remove_new_nulls: Whether to remove newly added fields from the package
            specs when they are null. This prevents the content hash from changing
            when upgrading from v2 to v3.0.3.

    Returns:
        A dictionary of platform-specific content hashes.
    """
    result: dict[PlatformSubdirStr, str] = {}

    # This is done so that conda-lock >=3.0.3 will produce the same content
    # hashes as conda-lock v2.
    if reinsert_spurious_build_number and virtual_package_repo is not None:
        virtual_package_repo = _reinsert_spurious_build_number(virtual_package_repo)

    for platform in lock_spec.platforms:
        content = _content_for_platform(lock_spec, platform, virtual_package_repo)
        if remove_new_nulls:
            content = _remove_new_nulls(content)
        result[platform] = _dict_to_hash(content)
    return result


def _dict_to_json(
    d: Union[SerializedLockspec, HashableVirtualPackageRepresentation],
) -> str:
    """Produce a canonical JSON representation of the given dict."""
    return json.dumps(d, sort_keys=True)


def _json_to_hash(s: str) -> str:
    """Hash the given JSON string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _dict_to_hash(
    d: Union[SerializedLockspec, HashableVirtualPackageRepresentation],
) -> str:
    """Hash the given dict."""
    return _json_to_hash(_dict_to_json(d))


def _content_for_platform(
    lock_spec: LockSpecification,
    platform: PlatformSubdirStr,
    virtual_package_repo: Optional[FakeRepoData],
) -> SerializedLockspec:
    serialized_lockspec: SerializedLockspec = {
        "channels": [c.model_dump_json() for c in lock_spec.channels],
        "specs": [
            cast(SerializedDependency, p.model_dump())
            for p in sorted(
                lock_spec.dependencies[platform], key=lambda p: (p.manager, p.name)
            )
        ],
    }
    if lock_spec.pip_repositories:
        serialized_lockspec["pip_repositories"] = [
            repo.model_dump_json() for repo in lock_spec.pip_repositories
        ]
    if virtual_package_repo is not None:
        serialized_lockspec["virtual_package_hash"] = (
            _virtual_package_content_for_platform(virtual_package_repo, platform)
        )
    return serialized_lockspec


def _virtual_package_content_for_platform(
    virtual_package_repo: FakeRepoData,
    platform: PlatformSubdirStr,
) -> HashableVirtualPackageRepresentation:
    """Serialize the virtual package content into a dict for hashing.

    This is used in the computation of the content hash, and goes
    into the "virtual_package_hash" field of the serialized lockspec.
    """
    vpr_data = virtual_package_repo.all_repodata

    # We don't actually use these values! I'm including them to indicate
    # what I would have expected from the schema. See the code block
    # immediately below for the actual values.
    fallback_noarch: Union[SubdirMetadata, EmptyDict] = {
        "info": {"subdir": "noarch"},
        "packages": {},
    }
    fallback_platform: Union[SubdirMetadata, EmptyDict] = {
        "info": {"subdir": platform},
        "packages": {},
    }

    # It seems a bit of a schema violation, but the original implementation
    # did this, so we have to keep it in order to preserve consistency of
    # the hashes.
    fallback_noarch = {}
    fallback_platform = {}

    result: HashableVirtualPackageRepresentation = {
        "noarch": vpr_data.get("noarch", fallback_noarch),
        platform: vpr_data.get(platform, fallback_platform),
    }
    return result


def backwards_compatible_content_hashes(
    lock_spec: LockSpecification,
    virtual_package_repo: Optional[FakeRepoData],
    platform: PlatformSubdirStr,
) -> set[str]:
    """Compute a set of content hashes for equivalent lock specifications.

    Computing multiple content hashes allows us to support previous versions of
    the content hash computation for backwards compatibility.

    We could have adopted a more targeted strategy for producing specific variants
    of the VPR, but the VPR can also be customized, so it's hard to know exactly
    how it's constructed. Therefore we just enumerate all possible variants to be safe.

    Note that VPR=None is only used for old tests, and it corresponds to the case where
    VPR is unspecified rather than default. (TODO: replace those tests and eliminate
    this special case?)
    """
    virtual_package_repo_variants: list[FakeRepoData] = []
    if virtual_package_repo is not None:
        # Allow for equivalent legacy versions of the VPR to support
        # backwards compatibility so that we don't unnecessarily reject a good hash.
        # This list will be combinatorially expanded in the following steps.
        # (If the VPR is None, we leave the list empty, effectively skipping the
        # enumeration.)
        virtual_package_repo_variants = [virtual_package_repo]

    # Support both with and without the redundant __osx=10.15 package.
    for vpr in virtual_package_repo_variants.copy():
        if platform == "osx-64" and _contains_osx_11_0_0_tar_bz2(vpr):
            virtual_package_repo_variants.append(add_or_remove_osx_10_15_0_tar_bz2(vpr))

    # Reinsert spurious build number in build string
    for vpr in virtual_package_repo_variants.copy():
        virtual_package_repo_variants.append(_reinsert_spurious_build_number(vpr))

    # Compute virtual_package_repo parameter values to iterate over.
    vprs: Sequence[Optional[FakeRepoData]]
    if virtual_package_repo is None:
        assert len(virtual_package_repo_variants) == 0
        vprs = [None]
    else:
        assert len(virtual_package_repo_variants) > 0
        vprs = virtual_package_repo_variants

    # Compute the content hashes for the given lock specification and VPR variants.
    allowed_hashes: set[str] = set()
    for vpr_or_none in vprs:
        # We don't need to check cases involving reinserting the spurious build number
        # in the VPR since that's already covered by the VPR variants.
        # We do need to include both possible values of remove_new_nulls, because
        # that affects the package specs, not the VPR.
        allowed_hashes.add(
            compute_content_hashes(
                lock_spec=lock_spec,
                virtual_package_repo=vpr_or_none,
                reinsert_spurious_build_number=False,
                remove_new_nulls=False,
            )[platform]
        )
        allowed_hashes.add(
            compute_content_hashes(
                lock_spec=lock_spec,
                virtual_package_repo=vpr_or_none,
                reinsert_spurious_build_number=False,
                remove_new_nulls=True,
            )[platform]
        )
    return allowed_hashes


def add_or_remove_osx_10_15_0_tar_bz2(
    virtual_package_repo: FakeRepoData,
) -> FakeRepoData:
    """Add or remove the __osx 10.15 virtual package.

    Adds __osx 10.15 if it is not present, and removes it if it is present.
    This way, whichever convention we start with, the opposite convention will be
    produced.

    Rationale:
    In 6f69901 we started generating the default repodata based on
    default-virtual-packages.yaml instead of programmatically. But there was a bug
    in which we added both __osx 10.15 and 11.0. The extra 10.15 is ignored by conda
    and mamba, and 11.0 takes precedence. We added an option to readd the 10.15 package
    in 777dfbf.
    """
    result = virtual_package_repo.model_copy(deep=True)

    # We know this isn't empty because we only call this when 11.0 is present.
    rd: SubdirMetadata = cast(SubdirMetadata, result.all_repodata["osx-64"])

    packages = rd["packages"]
    if "__osx-10.15-0.tar.bz2" in packages:
        del packages["__osx-10.15-0.tar.bz2"]
    else:
        packages["__osx-10.15-0.tar.bz2"] = {
            "name": "__osx",
            "version": "10.15",
            "build_string": "",
            "build_number": 0,
            "noarch": "",
            "depends": [],
            "timestamp": 1577854800000,
            "package_type": "virtual_system",
            "build": "0",
            "subdir": "osx-64",
        }
    return result


def _contains_osx_11_0_0_tar_bz2(virtual_package_repo: FakeRepoData) -> bool:
    rd = virtual_package_repo.all_repodata.get("osx-64", {})
    if "packages" not in rd:
        return False
    rd = cast(SubdirMetadata, rd)
    return rd["packages"].get("__osx-11.0-0.tar.bz2") == {
        "name": "__osx",
        "version": "11.0",
        "build_string": "",
        "build_number": 0,
        "noarch": "",
        "depends": [],
        "timestamp": 1577854800000,
        "package_type": "virtual_system",
        "build": "0",
        "subdir": "osx-64",
    }


def _reinsert_spurious_build_number(virtual_package_repo: FakeRepoData) -> FakeRepoData:
    """Reinsert the spurious build number in the build string.

    This was introduced in v3.0.3 to reproduce the content hash of the v2 lockfiles.
    <https://github.com/conda/conda-lock/pull/776>

    Without the spurious build number:

    ```json
    "__archspec-1-x86_64.tar.bz2": {
        "build": "x86_64",
        "build_number": 0,
        "build_string": "x86_64",
        "depends": [],
        "name": "__archspec",
        "noarch": "",
        "package_type": "virtual_system",
        "subdir": "linux-64",
        "timestamp": 1577854800000,
        "version": "1"
    }
    ```

    With the spurious build number:
    ```json
    "__archspec-1-x86_64_0.tar.bz2": {
        "build": "x86_64_0",
        "build_number": 0,
        "build_string": "x86_64",
        "depends": [],
        "name": "__archspec",
        "noarch": "",
        "package_type": "virtual_system",
        "subdir": "linux-64",
        "timestamp": 1577854800000,
        "version": "1"
    }
    ```
    """
    result = virtual_package_repo.model_copy(deep=True)
    for platform in result.all_repodata:
        rd = result.all_repodata[platform]
        if "packages" in rd:
            rd = cast(SubdirMetadata, rd)
            for package_name, package_data in rd["packages"].copy().items():
                name = package_data["name"]
                version = package_data["version"]
                build_string = package_data["build_string"]
                build_number = package_data["build_number"]
                if len(build_string) > 0:
                    package_data["build"] = f"{build_string}_{build_number}"
                    new_name = f"{name}-{version}-{build_string}_{build_number}.tar.bz2"
                    rd["packages"][new_name] = package_data
                    del rd["packages"][package_name]
    return result


def _remove_new_nulls(content: SerializedLockspec) -> SerializedLockspec:
    """Remove newly added fields from the VPR when they are null.

    New fields added in v3.0.0 that are usually None but were absent in v2
    would alter the content hash, so we remove them for backwards compatibility.
    """
    result = deepcopy(content)
    for spec in result["specs"]:
        if "markers" in spec and spec["markers"] is None:
            del spec["markers"]
        if "subdirectory" in spec and spec["subdirectory"] is None:
            del spec["subdirectory"]
    return result
