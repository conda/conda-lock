"""Solver dryrun normalization.

Conda's ``--dry-run --json`` output is the protocol that conda-lock
consumes from conda/mamba/micromamba. This module owns translating
that output into a uniform shape with one ``FETCH`` per planned
package, regardless of whether the underlying solver returned
rich-LINK actions (mamba 2.x / micromamba), sparse-LINK actions
(conda, Python mamba 1.x), or already-complete FETCH actions.
"""

from collections.abc import Mapping
from typing import Any, cast

from packaging.version import Version

from conda_lock.errors import MetadataConsistencyError
from conda_lock.invoke_conda import PathLike, mamba_binary_version
from conda_lock.models.dry_run_install import DryRunInstall, FetchAction
from conda_lock.solver.repodata_cache import (
    record_matches_link,
    record_validation_error,
)


_FETCH_KEYS_FROM_LINK: tuple[str, ...] = (
    "channel",
    "depends",
    "fn",
    "md5",
    "name",
    "subdir",
    "timestamp",
    "url",
    "version",
)


def link_action_as_fetch(link_action: Mapping[str, Any]) -> FetchAction | None:
    """Reuse a LINK action's metadata as a FETCH action when complete.

    Checked-in captures from Mamba 2.6.0 and micromamba 1.5.12 include
    the fields needed downstream. Conda and Python Mamba 1.x instead emit
    sparse LINKs. Reusing complete LINK metadata avoids reading a package
    cache whose layout varies by solver version. It does not establish that
    the metadata came from channel repodata: offline plans may use extracted
    records.

    Synthesis is rejected unless the LINK has every field that the
    downstream code (``solve_conda``) reads from a FETCH. Critically we
    require ``depends`` to be present *and* a list, otherwise an absent
    or null value would silently erase a package's runtime dependencies.
    """
    for key in _FETCH_KEYS_FROM_LINK:
        if key not in link_action or link_action[key] is None:
            return None
    if record_validation_error(link_action):
        return None
    fetch = cast(FetchAction, dict(link_action))
    # ``sha256`` and ``constrains`` are deliberately NOT in
    # ``_FETCH_KEYS_FROM_LINK``: both are optional in real repodata
    # (older .tar.bz2-era packages lack ``sha256``; most packages
    # declare no ``constrains``), so requiring them here would force
    # the disk fallback for packages whose cached record lacks them
    # just the same. ``sha256`` is copied through as-is because its
    # consumer (``HashModel.sha256`` via ``solve_conda``) is Optional;
    # ``constrains`` is normalized to ``[]`` for subsequent graph validation.
    fetch["sha256"] = link_action.get("sha256")
    constrains = link_action.get("constrains")
    fetch["constrains"] = constrains if isinstance(constrains, list) else []
    return fetch


def _validate_live_actions(
    conda: PathLike, dry_run_install: DryRunInstall | dict[str, Any]
) -> None:
    links = dry_run_install["actions"]["LINK"]
    fetches = dry_run_install["actions"]["FETCH"]
    # Older Mamba serializes empty live dependency arrays as null. This
    # normalization must never be applied to records read from disk.
    null_depends = [
        p for p in [*links, *fetches] if "depends" in p and p["depends"] is None
    ]
    if null_depends:
        version = mamba_binary_version(str(conda))
        # Fixed by https://github.com/mamba-org/mamba/pull/4284 in 2.8.0.
        if version is not None and Version("2.0") <= version < Version("2.8"):
            for record in null_depends:
                record["depends"] = []

    link_actions = {p["name"]: p for p in dry_run_install["actions"]["LINK"]}
    fetch_actions = {p["name"]: p for p in dry_run_install["actions"]["FETCH"]}
    if len(link_actions) != len(links) or len(fetch_actions) != len(fetches):
        raise MetadataConsistencyError("Duplicate package names in solver actions")
    for name, fetch in fetch_actions.items():
        reason = record_validation_error(fetch)
        if reason:
            raise MetadataConsistencyError(
                f"Invalid FETCH metadata for {name}: {reason}"
            )
        link = link_actions.get(name)
        if link is not None:
            matched, reason = record_matches_link(fetch, link)
            if not matched:
                raise MetadataConsistencyError(
                    f"FETCH/LINK identity mismatch for {name}: {reason}"
                )
            if "depends" in link:
                depends = link["depends"]
                if (
                    not isinstance(depends, list)
                    or any(not isinstance(dep, str) for dep in depends)
                    or sorted(fetch["depends"] or []) != sorted(depends)
                ):
                    raise MetadataConsistencyError(
                        f"FETCH/LINK dependencies disagree for {name}"
                    )
