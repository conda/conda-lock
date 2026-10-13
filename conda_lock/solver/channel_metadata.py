"""Check extracted package records using the solver's JSON search commands."""

import json
import logging
import os
import shlex
import subprocess

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, Literal, cast
from urllib.parse import quote, urlsplit, urlunsplit

from packaging.version import Version

from conda_lock.errors import CondaLockError, MetadataConsistencyError
from conda_lock.invoke_conda import (
    PathLike,
    conda_env_override,
    extract_json_object,
    mamba_binary_version,
)
from conda_lock.solver.repodata_cache import (
    normalize_url_for_compare,
    record_matches_link,
    record_validation_error,
)


def metadata_fallback() -> Literal["error", "unbatched", "skip"]:
    value = os.environ.get("CONDA_LOCK_METADATA_FALLBACK") or "error"
    if value not in {"error", "unbatched", "skip"}:
        raise CondaLockError(
            f"Invalid CONDA_LOCK_METADATA_FALLBACK={value!r}; "
            "expected error, unbatched, or skip"
        )
    return cast(Literal["error", "unbatched", "skip"], value)


def _use_unbatched_fallback(fallback: str, platform: str, package_count: int) -> bool:
    """Return False only for an explicit skip; default to an actionable error."""
    if fallback == "error":
        raise MetadataConsistencyError(
            f"The selected solver cannot batch metadata verification for "
            f"{package_count} package(s) on {platform}",
            recovery=(
                "Use a solver with batched repoquery support. For Conda, enable "
                "conda-libmamba-solver >=24.11.0 in the selected installation. "
                "Alternatively set CONDA_LOCK_METADATA_FALLBACK=unbatched for "
                "slow full verification, or =skip to retain unverified metadata "
                "without automatic pruning. See "
                "https://conda.github.io/conda-lock/troubleshooting/#metadata-query-fallbacks"
            ),
        )
    if fallback == "skip":
        logging.getLogger(__name__).warning(
            "CONDA_LOCK_METADATA_FALLBACK=skip: batching is unavailable; "
            "skipping channel verification for %d package(s) on %s. "
            "Missing dependencies or constraints may propagate from lockfile "
            "or package-cache metadata. Automatic pruning is disabled for "
            "unverified updates; integrity checks remain active.",
            package_count,
            platform,
        )
        return False
    logging.getLogger(__name__).warning(
        "Batching is unavailable; verifying %d package(s) on %s with individual "
        "searches because CONDA_LOCK_METADATA_FALLBACK=unbatched. "
        "This may take minutes or hours for large environments.",
        package_count,
        platform,
    )
    return True


def _conda_record_with_url(record: dict[str, Any]) -> dict[str, Any]:
    # Recent Conda search output omits `url`, but its `channel` is the full
    # subdirectory URL. Never infer an artifact location from a channel alias.
    if "url" in record or not isinstance(record.get("channel"), str):
        return record
    location = urlsplit(record["channel"])
    filename = record.get("fn")
    if (
        location.scheme in {"https", "http", "file"}
        and location.path.rstrip("/").rsplit("/", 1)[-1] == record.get("subdir")
        and isinstance(filename, str)
    ):
        url = urlunsplit(
            location._replace(path=location.path.rstrip("/") + "/" + quote(filename))
        )
        return {**record, "url": url}
    return record


def _supports_conda_repoquery(conda: PathLike, env: Mapping[str, str]) -> bool:
    # repoquery is supplied by conda-libmamba-solver, which need not be installed
    # or enabled. Check the command, not the Conda version or solver setting.
    proc = subprocess.run(  # noqa: UP022  # Poetry monkeypatch breaks capture_output
        [str(conda), "repoquery", "search", "--help"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )
    # --no-installed predates multi-name search. Since 24.11.0, argparse's
    # usage also exposes specs [specs ...]; normalize wrapping before checking.
    # https://github.com/conda/conda-libmamba-solver/blob/24.11.0/conda_libmamba_solver/repoquery.py
    help_text = " ".join(proc.stdout.split())
    return (
        proc.returncode == 0
        and "--no-installed" in help_text
        and "specs [specs ...]" in help_text
    )


def _query_result_records(
    proc: subprocess.CompletedProcess[str],
) -> list[dict[str, Any]]:
    try:
        proc.check_returncode()
        data = json.loads(extract_json_object(proc.stdout))
        if isinstance(data.get("result"), dict):
            entries = data["result"]["pkgs"]  # repoquery
        else:
            if not all(isinstance(value, list) for value in data.values()):
                raise ValueError("channel query did not return package records")
            entries = [record for records in data.values() for record in records]
        if not isinstance(entries, list) or any(
            not isinstance(record, dict) for record in entries
        ):
            raise ValueError("channel query did not return package records")
        return entries
    except subprocess.CalledProcessError as exc:
        raise MetadataConsistencyError(
            f"Channel metadata query exited with status {proc.returncode}",
            recovery=(
                "Check that the selected solver can access the original channels, "
                "including their authentication, proxy and offline settings. "
                "A query failure does not establish that the lockfile is corrupt."
            ),
        ) from exc
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as exc:
        raise MetadataConsistencyError(
            "Channel metadata query returned unexpected output",
            recovery=(
                "Check the solver version and repoquery/search support. "
                "If this persists with a current solver, report its version "
                "to conda-lock; clear credentials from any shared output."
            ),
        ) from exc


def query_channel_records(
    conda: PathLike, platform: str, records: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]] | None:
    """Read channel records without solving or trusting installed metadata.

    Query only the channels of the selected artifacts. Mamba's repoquery command
    handles both full and sharded repodata, so this does not depend on private
    cache filenames or interpret an archive's index.json as channel metadata.
    None means the user explicitly skipped verification on a solver without
    batching. Query failures never activate that fallback.
    """
    fallback = metadata_fallback()
    version = mamba_binary_version(conda)
    names = sorted({record["name"] for record in records})
    if not names:
        return []
    flags = ["--json", "--override-channels"]
    offline = "--offline" in shlex.split(os.environ.get("CONDA_FLAGS", ""))
    channels = set()
    for record in records:
        parsed = urlsplit(record["url"])
        channel_path = parsed.path.rsplit("/", 2)[0]
        channels.add(
            urlunsplit(parsed._replace(path=channel_path, query="", fragment=""))
        )
    for channel in sorted(channels):
        flags.extend(["--channel", channel])
    env = {
        **conda_env_override(platform),
        "MAMBA_ADD_PIP_AS_PYTHON_DEPENDENCY": "False",
    }
    if version is not None and version >= Version("2"):
        commands = [[str(conda), "repoquery", "search", *names]]
    elif version is None and _supports_conda_repoquery(conda, env):
        # Batch names so Conda loads channel metadata once. Explicitly exclude
        # the active prefix: installed records cannot verify channel metadata.
        commands = [[str(conda), "repoquery", "search", "--no-installed", *names]]
    else:
        if not _use_unbatched_fallback(fallback, platform, len(names)):
            return None
        # Legacy Mamba and Conda without repoquery accept one MatchSpec per
        # search. Recent Conda rejects regex names (conda/conda#16096).
        commands = [[str(conda), "search", name] for name in names]
    if version is not None and version >= Version("2"):
        if all(urlsplit(channel).scheme == "file" for channel in channels):
            # Offline Mamba also loads extracted package records. For local
            # channels read repodata directly instead; no network is involved.
            offline = False
            env.update(MAMBA_OFFLINE="False", CONDA_OFFLINE="False")
        else:
            config = subprocess.run(  # noqa: UP022  # Poetry monkeypatch breaks capture_output
                [str(conda), "config", "list", "offline", "--json"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf8",
            )
            try:
                config.check_returncode()
                configured_offline = json.loads(extract_json_object(config.stdout))[
                    "offline"
                ]
                if not isinstance(configured_offline, bool):
                    raise ValueError("invalid offline setting")
            except (
                subprocess.CalledProcessError,
                ValueError,
                KeyError,
                TypeError,
            ) as exc:
                raise MetadataConsistencyError(
                    "Could not determine Mamba's offline setting"
                ) from exc
            if offline or configured_offline:
                raise MetadataConsistencyError(
                    "Offline Mamba verification of remote channel metadata is not "
                    "supported: queries may return extracted package-cache records",
                    recovery=(
                        "Remove --offline from CONDA_FLAGS and disable offline in the "
                        "selected solver's configuration, including MAMBA_OFFLINE or "
                        "CONDA_OFFLINE, then retry with channel access. Inspect settings "
                        "with `mamba config list offline --sources` (use `micromamba` "
                        "instead for micromamba). No channel query was attempted."
                    ),
                )
    if offline:
        flags.append("--offline")
    result = []
    for command in commands:
        proc = subprocess.run(  # noqa: UP022  # Poetry monkeypatch breaks capture_output
            [*command, *flags],
            # repoquery can inject Python -> pip while loading records, even though
            # it is not solving. Verification needs the actual channel dependencies.
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf8",
        )
        entries = _query_result_records(proc)
        if version is None or version < Version("2"):
            entries = [_conda_record_with_url(record) for record in entries]
        result.extend(entries)
    # JSON flattening lost empty arrays before mamba-org/mamba#4284 (2.8.0).
    if version is not None and Version("2") <= version < Version("2.8"):
        for record in result:
            if "depends" in record and record["depends"] is None:
                record["depends"] = []
    return result


def verified_channel_records(
    conda: PathLike,
    platform: str,
    records: Sequence[Mapping[str, Any]],
    *,
    allow_missing: bool = False,
    verified_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Return verified records, or None for the explicit batching opt-out.

    Only the pre-update refresh may omit unavailable artifacts: its caller must
    ask the solver for replacements. Reconstruction of an already selected plan
    still requires every artifact. Query and identity failures are always fatal.

    verified_cache is owned by one solver/platform operation. Store only checked
    artifacts, not all versions returned by search, and recheck identity/hashes
    on every hit. Returned records are independent copies. No entries survive
    the operation: this reuses a channel observation, not persistent metadata,
    and cannot detect a later channel patch to an artifact already verified.
    """
    if not records:
        return {}
    cache = verified_cache if verified_cache is not None else {}
    missing = [
        record
        for record in records
        if normalize_url_for_compare(record["url"]) not in cache
    ]
    queried = query_channel_records(conda, platform, missing) if missing else []
    if queried is None:
        return None  # Never present the input records as verified channel metadata.
    by_url = {url: [record] for url, record in cache.items()}
    for record in queried:
        if (
            not isinstance(record.get("name"), str)
            or not isinstance(record.get("url"), str)
            or not record["url"]
        ):
            raise MetadataConsistencyError(
                "Channel metadata query returned a record without a name or artifact URL"
            )
        url = normalize_url_for_compare(record["url"])
        if url not in cache:
            by_url.setdefault(url, []).append(record)
    verified = {}
    for expected in records:
        candidates = by_url.get(normalize_url_for_compare(expected["url"]), [])
        if not candidates:
            if allow_missing:
                continue
            raise MetadataConsistencyError(
                f"Channel metadata for package {expected['name']} is unavailable",
                recovery=(
                    "The original artifact may have been removed or moved; this does "
                    "not prove the old lockfile is corrupt. Restore access to its "
                    "original channel metadata, or regenerate from the source files "
                    "to a new lockfile path without --update and review package changes. "
                    "Clearing the package cache cannot restore a removed channel record."
                ),
            )
        for candidate in candidates:
            reason = record_validation_error(candidate)
            matched, _ = record_matches_link(candidate, expected)
            same_digest = any(
                candidate.get(key) and candidate.get(key) == expected.get(key)
                for key in ("md5", "sha256")
            )
            if reason or not matched or not same_digest:
                raise MetadataConsistencyError(
                    f"Channel artifact identity cannot be verified for {expected['name']}"
                )
            if any(
                sorted(candidate.get(field) or [])
                != sorted(candidates[0].get(field) or [])
                for field in ("depends", "constrains")
            ):
                raise MetadataConsistencyError(
                    f"Conflicting channel metadata for {expected['name']}"
                )
        cache[normalize_url_for_compare(expected["url"])] = deepcopy(candidates[0])
        verified[expected["name"]] = deepcopy(candidates[0])
        # Verification may return equivalent URL spelling (notably ! vs %21).
        # Refresh metadata without rewriting the selected artifact's location.
        verified[expected["name"]]["url"] = expected["url"]
        for digest in ("md5", "sha256"):
            if not candidates[0].get(digest) and expected.get(digest):
                verified[expected["name"]][digest] = expected[digest]
    return verified
