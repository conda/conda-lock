"""Lockfile-side heal of carry-forward mamba 2.1.1-2.3.3 corruption.

When a previous ``conda-lock lock`` ran against a corrupt
``repodata_record.json`` cache (mamba/micromamba 2.1.1-2.3.3,
conda/conda-lock#896), the resulting lockfile may carry empty
``dependencies: {}`` for the affected packages. Without intervention,
``conda-lock lock --update`` reads that lockfile back via
``fake_conda_environment``, ``to_fetch_action()`` emits a FETCH with
empty ``depends``, ``apply_categories`` cannot reach the package via
the dependency graph, and the freshly-emitted lockfile inherits the
empty deps -- forever.

The fix is to consult the local package cache *on the way in*: if
``info/index.json`` exists for the package, it carries the
canonical ``depends`` (extracted from the tarball at install time
and unaffected by the bug). This module exposes
``heal_locked_dependencies_from_cache``, which mutates the input
lockfile in place and returns a ``LockfileHealReport`` so the
caller (and the test suite) can inspect what happened without
parsing log lines.

Policy decisions (warn? fail? continue?) are deliberately not in
this module. ``heal_locked_dependencies_from_cache`` simply
classifies each empty-deps entry as healed, confirmed legit-empty,
or ambiguous, and reports what it found. ``update_specs_for_arch``
in ``conda_solver`` is the orchestration layer that decides what
to do with that report.
"""

import json
import logging
import pathlib

from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from conda_lock.interfaces.vendored_conda import MatchSpec
from conda_lock.invoke_conda import PathLike, get_pkgs_dirs
from conda_lock.lockfile.v2prelim.models import LockedDependency
from conda_lock.models.dry_run_install import LinkAction
from conda_lock.solver.repodata_cache import candidate_record_paths


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LockfileHealReport:
    """Structured outcome from a lockfile-heal pass.

    Each tuple holds package names from the input lockfile classified
    by what the local cache said about them. Mutually exclusive: a
    name appears in at most one tuple.

    Attributes
    ----------
    healed
        Entries the cache proved corrupt (lockfile said empty
        ``dependencies``, ``info/index.json`` listed real depends).
        These have been fixed in place on the input ``locked`` dict.
    confirmed_legit_empty
        Entries the cache proved are legit-empty (``info/index.json``
        also lists no depends). Unchanged; reported for visibility
        so callers and tests can distinguish "no cache evidence"
        from "cache evidence agrees this is empty".
    ambiguous
        Entries the cache could not adjudicate -- ``info/index.json``
        was missing, unreadable, or rejected by identity check.
        Could be either legit-empty (e.g. ``tzdata``) or
        carry-forward corrupt without local evidence. Unchanged;
        the caller decides whether to warn the operator.
    """

    healed: tuple[str, ...]
    confirmed_legit_empty: tuple[str, ...]
    ambiguous: tuple[str, ...]


def locked_dep_as_link_action(dep: LockedDependency) -> tuple[LinkAction, str]:
    """Project a ``LockedDependency`` into a LinkAction-shaped dict and a
    dist_name. Used by ``heal_locked_dependencies_from_cache`` so we can
    reuse the cache lookup machinery (``candidate_record_paths``,
    ``record_matches_link``) on lockfile entries.
    """
    parts = urlsplit(dep.url)
    filename = pathlib.PurePosixPath(parts.path).name
    if filename.endswith(".tar.bz2"):
        dist_name = filename[:-8]
    elif filename.endswith(".conda"):
        dist_name = filename[:-6]
    else:
        dist_name = filename
    link_action: LinkAction = cast(
        LinkAction,
        {
            "name": dep.name,
            "version": dep.version,
            "platform": dep.platform,
            "subdir": dep.platform,
            "dist_name": dist_name,
            "fn": filename,
            "url": dep.url,
            "md5": dep.hash.md5 or "",
            "sha256": dep.hash.sha256 or "",
        },
    )
    return link_action, dist_name


def _index_matches_link(index: dict[str, Any], link_action: LinkAction) -> bool:
    """Identity-check an ``info/index.json`` against the LINK metadata
    before trusting it as the canonical depends source.

    The hierarchical lookup is keyed on the (credential-stripped)
    package URL, so a legitimate hit can't be a cross-channel impostor.
    The legacy flat layout (``<pkgs>/<dist_name>/info/...``), however,
    *is* keyed on ``dist_name`` alone; two packages built with the
    same ``name-version-build`` triple under different channels will
    collide there, and ``info/index.json`` does not carry the URL or
    channel for us to disambiguate. Validate every field both sides
    expose:

    - ``name`` and ``version`` must match positively.
    - ``build`` (parsed from the dist_name) must match when the index
      reports it.
    - ``subdir`` must be compatible: equal, or one of the two is
      ``noarch`` (a noarch package may legitimately satisfy a
      concrete-platform LINK and vice versa).

    Returns False on any concrete mismatch; True if no contradictions
    were observed.
    """
    if index.get("name") and link_action.get("name"):
        if index["name"] != link_action["name"]:
            return False
    if index.get("version") and link_action.get("version"):
        if str(index["version"]) != str(link_action["version"]):
            return False
    # Parse the build off the LINK's filename: ``<name>-<version>-<build>``.
    expected_build: str | None = None
    fn = link_action.get("fn") or ""
    if isinstance(fn, str) and fn:
        stem = fn
        for ext in (".tar.bz2", ".conda"):
            if stem.endswith(ext):
                stem = stem[: -len(ext)]
                break
        parts = stem.rsplit("-", 2)
        if len(parts) == 3:
            expected_build = parts[2]
    index_build = index.get("build")
    if expected_build and index_build and index_build != expected_build:
        return False
    index_subdir = index.get("subdir")
    link_subdir = link_action.get("platform") or link_action.get("subdir")
    if index_subdir and link_subdir and index_subdir != link_subdir:
        # Allow ``noarch`` to cross-validate against a concrete subdir.
        if index_subdir != "noarch" and link_subdir != "noarch":
            return False
    return True


def find_cache_index_for_locked_dep(
    pkgs_dirs: list[pathlib.Path],
    dist_name: str,
    link_action: LinkAction,
) -> dict[str, Any] | None:
    """Locate ``info/index.json`` for a package in the local cache.

    ``info/index.json`` is extracted directly from the package tarball
    at install time and is unaffected by the mamba 2.1.1-2.3.3
    ``repodata_record.json`` corruption (mamba-org/mamba#4052,
    conda/conda-lock#896). When healing a corrupt lockfile entry we
    need only the canonical ``depends`` from the package itself --
    ``url`` / ``md5`` / ``sha256`` are already on the lockfile entry --
    so ``info/index.json`` is the right (and lighter-weight) source.

    Walks the same candidate paths as ``candidate_record_paths``
    (mamba 2.6.0 hierarchical + legacy flat) but for the sibling
    ``index.json`` file, which may be present even when
    ``repodata_record.json`` was never written or is missing/corrupt.
    Each loaded index is identity-checked against the LINK metadata
    via ``_index_matches_link`` -- the legacy flat layout is
    indexed by ``dist_name`` alone and can hold same-dist-name
    packages from different channels, so an unchecked match could
    silently heal the lockfile with metadata from the wrong artifact.
    """
    for pkgs_dir in pkgs_dirs:
        for record_path in candidate_record_paths(pkgs_dir, dist_name, link_action):
            index_path = record_path.parent / "index.json"
            if not index_path.is_file():
                continue
            try:
                with open(index_path) as f:
                    index = cast(dict, json.load(f))
            except (OSError, json.JSONDecodeError):
                continue
            if not _index_matches_link(index, link_action):
                logger.debug(
                    "lockfile heal: rejecting %s as cross-package/channel match for %s",
                    index_path,
                    link_action.get("name"),
                )
                continue
            return index
    return None


def _parse_match_spec_to_name_version(spec_str: str) -> tuple[str, str]:
    """Decompose a conda match spec like ``"__glibc >=2.17,<3.0.a0"`` into
    ``(name, version_spec)``."""
    ms = MatchSpec(spec_str)  # pyright: ignore[reportArgumentType]
    name = ms.name
    version = ms.version.spec_str if ms.version is not None else ""
    return name, version


def heal_locked_dependencies_from_cache(
    locked: dict[str, LockedDependency],
    conda: PathLike,
    platform: str,
) -> LockfileHealReport:
    """Recover ``dependencies`` for lockfile entries that look corrupt.

    A lockfile generated against a corrupt cache (mamba 2.1.1-2.3.3,
    conda/conda-lock#896) inherits empty ``dependencies: {}`` for the
    affected packages. The lockfile is then a *carrier* for the bug:
    ``update_specs_for_arch`` reads it back, ``to_fetch_action()`` emits
    a FETCH with empty ``depends``, ``apply_categories`` cannot reach
    the package via the dependency graph, and the fresh lockfile inherits
    the empty deps -- forever.

    Heal on the way in by looking up the package's ``info/index.json``
    in the local cache. That file is extracted from the tarball at
    install time and was never affected by the bug, so even when
    ``repodata_record.json`` is missing or corrupt, ``info/index.json``
    holds the canonical ``depends``.

    Some packages legitimately have no runtime dependencies (``tzdata``,
    ``python_abi``, ``nlohmann_json-abi``, ``_libgcc_mutex``, ...) so an
    empty ``dependencies`` map alone is not proof of corruption. We use
    the cache as a per-entry discriminator:

    - Cache confirms canonical depends are non-empty -> heal in place
      and record the entry under ``healed``. The contradiction
      (cache says non-empty, lockfile says empty) is proof of mamba
      2.1.1-2.3.3 corruption for *this* entry.
    - Cache confirms canonical depends are empty -> record under
      ``confirmed_legit_empty``.
    - Cache has no entry for the package -> record under ``ambiguous``.
      We cannot tell legit-empty from corrupt-empty without canonical
      metadata. Critically, healable-elsewhere is NOT evidence about
      ambiguous-here: partial caches are normal (a user may have
      ``libzlib`` cached but not ``tzdata``). The orchestration layer
      decides whether and how to surface this to the operator; this
      function does not log anything beyond per-entry DEBUG breadcrumbs.

    Mutates ``locked`` in place. Returns a ``LockfileHealReport``
    summarizing what happened to each empty-deps entry.
    """
    candidates = [
        name
        for name, dep in locked.items()
        if dep.manager == "conda" and not dep.dependencies and dep.platform == platform
    ]
    if not candidates:
        return LockfileHealReport(healed=(), confirmed_legit_empty=(), ambiguous=())
    pkgs_dirs = get_pkgs_dirs(conda=conda, platform=platform)
    healed: list[str] = []
    confirmed_legit_empty: list[str] = []
    ambiguous: list[str] = []
    for name in candidates:
        dep = locked[name]
        link_action, dist_name = locked_dep_as_link_action(dep)
        index = find_cache_index_for_locked_dep(pkgs_dirs, dist_name, link_action)
        if index is None:
            ambiguous.append(name)
            continue
        index_depends = index.get("depends") or []
        if not index_depends:
            confirmed_legit_empty.append(name)
            continue
        recovered: dict[str, str] = {}
        for spec_str in index_depends:
            recovered_name, recovered_version = _parse_match_spec_to_name_version(
                spec_str
            )
            recovered[recovered_name] = recovered_version
        dep.dependencies = recovered
        healed.append(name)
    return LockfileHealReport(
        healed=tuple(healed),
        confirmed_legit_empty=tuple(confirmed_legit_empty),
        ambiguous=tuple(ambiguous),
    )
