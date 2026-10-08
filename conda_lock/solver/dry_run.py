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
from conda_lock.invoke_conda import PathLike, get_pkgs_dirs, mamba_binary_version
from conda_lock.models.dry_run_install import DryRunInstall, FetchAction, LinkAction
from conda_lock.solver.channel_metadata import verified_channel_records
from conda_lock.solver.repodata_cache import (
    get_repodata_record,
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

    Mamba-family solvers return LINK entries that already include every
    repodata field we need (``url``, ``fn``, ``md5``, ``sha256``,
    ``depends``, ``constrains``, ...) -- captured and verified for
    micromamba 1.5.12 through 2.8.1; conda and the Python ``mamba``
    1.x CLI emit sparse conda-meta LINKs instead. When the fields are
    all present we don't need to crack open ``repodata_record.json``
    on disk -- doubly useful given that mamba 2.6.0 reorganized the
    cache hierarchically by channel/subdir
    (see https://github.com/mamba-org/mamba/pull/4163), invalidating the
    flat-path lookup that ``get_repodata_record`` used to do.

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


def reconstruct_fetch_actions_in_place(
    conda: PathLike, platform: str, dry_run_install: DryRunInstall | dict[str, Any]
) -> None:
    """Normalize a conda/mamba dryrun so every planned package has a FETCH.

    Conda may choose to link a previously downloaded distribution from
    ``pkgs_dirs`` rather than downloading a fresh one, in which case
    its dryrun returns only a LINK action, which for conda and the
    Python ``mamba`` 1.x CLI lacks the ``url`` / ``md5`` / ``sha256``
    / ``depends`` fields the package plan needs.
    For each LINK without a matching FETCH, this function either
    synthesizes one from the LINK metadata (mamba-family fast path)
    or verifies cached ``repodata_record.json`` against channel metadata.

    **Mutates ``dry_run_install`` in place and returns ``None``.**
    The input's ``actions["FETCH"]`` list is extended (and
    ``actions["LINK"]`` / ``actions["FETCH"]`` keys created if
    absent). The ``_in_place`` suffix and the ``None`` return follow
    the ``list.sort`` convention: mutation is the entire point, and
    returning the mutated object would let a caller mistake this for
    a pure function and keep using the (also mutated) input. If you
    need the original dryrun pristine, deep-copy before calling.
    """
    if "LINK" not in dry_run_install["actions"]:
        dry_run_install["actions"]["LINK"] = []
    if "FETCH" not in dry_run_install["actions"]:
        dry_run_install["actions"]["FETCH"] = []

    _validate_live_actions(conda, dry_run_install)
    link_actions = {p["name"]: p for p in dry_run_install["actions"]["LINK"]}
    fetch_actions = {p["name"]: p for p in dry_run_install["actions"]["FETCH"]}
    link_only_names = set(link_actions.keys()).difference(fetch_actions.keys())

    # Mamba-family solvers put the full repodata into LINK actions, so we
    # can often synthesize FETCH without going to disk. Resolve those first
    # and only run another subprocess to discover ``pkgs_dirs`` if
    # anything is left over.
    deferred: list[tuple[str, LinkAction]] = []
    for link_pkg_name in link_only_names:
        link_action = link_actions[link_pkg_name]
        from_link = link_action_as_fetch(link_action)
        if from_link is not None:
            dry_run_install["actions"]["FETCH"].append(from_link)
        else:
            deferred.append((link_pkg_name, link_action))

    if deferred:
        pkgs_dirs = get_pkgs_dirs(conda=conda, platform=platform)
    else:
        pkgs_dirs = []

    reconstructed = []
    for _link_pkg_name, link_action in deferred:
        if "dist_name" in link_action:
            dist_name = link_action["dist_name"]
        elif "fn" in link_action:
            dist_name = str(link_action["fn"])
            if dist_name.endswith(".tar.bz2"):
                dist_name = dist_name[:-8]
            elif dist_name.endswith(".conda"):
                dist_name = dist_name[:-6]
            else:
                raise ValueError(f"Unknown filename format: {dist_name}")
        else:
            raise ValueError(f"Unable to extract the dist_name from {link_action}.")
        repodata = get_repodata_record(pkgs_dirs, dist_name, link_action)
        if repodata is None:
            raise MetadataConsistencyError(
                f"No valid cache record matching distribution '{dist_name}' "
                f"was found in pkgs_dirs {pkgs_dirs}"
            )
        reconstructed.append(repodata)
    if reconstructed:
        verified = verified_channel_records(conda, platform, reconstructed)
        dry_run_install["actions"]["FETCH"].extend(
            cast(FetchAction, record) for record in verified.values()
        )
