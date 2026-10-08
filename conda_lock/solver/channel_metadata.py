"""Check extracted package records using the solver's JSON search commands."""

import json
import os
import shlex
import subprocess

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from packaging.version import Version

from conda_lock.errors import MetadataConsistencyError
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


def query_channel_records(
    conda: PathLike, platform: str, records: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Read channel records without solving or trusting installed metadata.

    Query only the channels of the selected artifacts. Mamba's repoquery command
    handles both full and sharded repodata, so this does not depend on private
    cache filenames or interpret an archive's index.json as channel metadata.
    """
    version = mamba_binary_version(conda)
    names = sorted({record["name"] for record in records})
    if version is not None and version >= Version("2"):
        commands = [[str(conda), "repoquery", "search", *names]]
    else:
        # Conda accepts one MatchSpec per search. Recent versions reject regex
        # package names (conda/conda#16096), so query exact names individually.
        commands = [[str(conda), "search", name] for name in names]
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
                    "Offline Mamba queries may return extracted package-cache metadata; "
                    "cannot independently verify remote channel records. "
                    "Use conda for offline verification or allow an online channel query"
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
        try:
            proc.check_returncode()
            data = json.loads(extract_json_object(proc.stdout))
            if version is not None and version >= Version("2"):
                entries = data["result"]["pkgs"]
            else:
                entries = [record for records in data.values() for record in records]
            if not isinstance(entries, list) or any(
                not isinstance(record, dict) for record in entries
            ):
                raise ValueError("channel query did not return package records")
            if version is None or version < Version("2"):
                entries = [_conda_record_with_url(record) for record in entries]
        except (
            subprocess.CalledProcessError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as exc:
            raise MetadataConsistencyError(
                "Could not query channel metadata for cached packages"
            ) from exc
        result.extend(entries)
    # JSON flattening lost empty arrays before mamba-org/mamba#4284 (2.8.0).
    if version is not None and Version("2") <= version < Version("2.8"):
        for record in result:
            if "depends" in record and record["depends"] is None:
                record["depends"] = []
    return result


def verified_channel_records(
    conda: PathLike, platform: str, records: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Return current metadata only for matching artifact URLs and digests."""
    if not records:
        return {}
    queried = query_channel_records(conda, platform, records)
    by_url: dict[str, list[dict[str, Any]]] = {}
    for record in queried:
        if isinstance(record.get("url"), str):
            by_url.setdefault(normalize_url_for_compare(record["url"]), []).append(
                record
            )
    verified = {}
    for expected in records:
        candidates = by_url.get(normalize_url_for_compare(expected["url"]), [])
        if not candidates:
            raise MetadataConsistencyError(
                f"Channel metadata for package {expected['name']} is unavailable"
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
        verified[expected["name"]] = dict(candidates[0])
        for digest in ("md5", "sha256"):
            if not candidates[0].get(digest) and expected.get(digest):
                verified[expected["name"]][digest] = expected[digest]
    return verified
