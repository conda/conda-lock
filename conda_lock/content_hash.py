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

from typing import Dict, Optional, Set, Union, cast

from conda_lock.content_hash_types import (
    EmptyDict,
    HashableVirtualPackageRepresentation,
    PlatformSubdirStr,
    SerializedDependency,
    SerializedLockspec,
    SubdirMetadata,
)
from conda_lock.models.lock_spec import LockSpecification
from conda_lock.virtual_package import FakeRepoData, default_virtual_package_repodata


def compute_content_hashes(
    lock_spec: LockSpecification,
    virtual_package_repo: Optional[FakeRepoData],
) -> Dict[PlatformSubdirStr, str]:
    result: dict[PlatformSubdirStr, str] = {}
    for platform in lock_spec.platforms:
        content = _content_for_platform(lock_spec, platform, virtual_package_repo)
        result[platform] = _dict_to_hash(content)
    return result


def _dict_to_json(d: SerializedLockspec | HashableVirtualPackageRepresentation) -> str:
    """Produce a canonical JSON representation of the given dict."""
    return json.dumps(d, sort_keys=True)


def _json_to_hash(s: str) -> str:
    """Hash the given JSON string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _dict_to_hash(d: SerializedLockspec | HashableVirtualPackageRepresentation) -> str:
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
) -> Set[str]:
    """Verify that the content hash matches the given lock specification."""
    # This is the nominal content hash.
    allowed_hashes = {compute_content_hashes(lock_spec, virtual_package_repo)[platform]}

    # Also allow for equivalent legacy versions of the default VPR to support backwards
    # compatibility so that we don't unnecessarily reject a good hash.
    if _is_vpr_default(virtual_package_repo, platform):
        ...
    return allowed_hashes


def _is_vpr_default(
    virtual_package_repo: Optional[FakeRepoData], platform: PlatformSubdirStr
) -> bool:
    """Check if the virtual package repo for the given platform is the default one.

    (If so, we may need to allow equivalent legacy versions of the default VPR
    to support backwards compatibility so that we don't unnecessarily reject a
    good hash.)

    >>> _is_vpr_default(default_virtual_package_repodata(), "linux-64")
    True

    >>> from conda_lock.virtual_package import _init_fake_repodata
    >>> repodata = _init_fake_repodata()
    >>> _is_vpr_default(repodata, "linux-64")
    False
    """
    if virtual_package_repo is None:
        return True

    default_vpr = default_virtual_package_repodata()
    default_vpr_content = _virtual_package_content_for_platform(default_vpr, platform)
    default_vpr_content_json = _dict_to_json(default_vpr_content)

    vpr_content = _virtual_package_content_for_platform(virtual_package_repo, platform)
    vpr_content_json = _dict_to_json(vpr_content)

    return default_vpr_content_json == vpr_content_json
