import json
import logging
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import time

from collections.abc import Iterable, Iterator, MutableSequence, Sequence
from contextlib import contextmanager
from textwrap import dedent
from typing import (
    Any,
    Literal,
)
from urllib.parse import urlsplit, urlunsplit

from conda_lock.interfaces.vendored_conda import MatchSpec
from conda_lock.invoke_conda import (
    PathLike,
    _get_conda_flags,
    conda_env_override,
    conda_pkgs_dir,
    is_micromamba,
)
from conda_lock.lockfile import apply_categories
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency
from conda_lock.models.channel import Channel, normalize_url_with_placeholders
from conda_lock.models.dry_run_install import DryRunInstall, FetchAction, LinkAction
from conda_lock.models.lock_spec import Dependency, VersionedDependency
from conda_lock.tempdir_manager import temporary_directory


logger = logging.getLogger(__name__)


def _to_match_spec(
    conda_dep_name: str,
    conda_version: str | None,
    build: str | None,
    conda_channel: str | None,
) -> str:
    kwargs = dict(name=conda_dep_name)
    if conda_version:
        kwargs["version"] = conda_version
    if build:
        kwargs["build"] = build
        if "version" not in kwargs:
            kwargs["version"] = "*"
    if conda_channel:
        kwargs["channel"] = conda_channel

    ms = MatchSpec(**kwargs)  # pyright: ignore[reportArgumentType]
    # Since MatchSpec doesn't round trip to the cli well
    if conda_channel:
        # this will return "channel_name::package_name"
        return str(ms)
    else:
        # this will return only "package_name" even if there's a channel in the kwargs
        return ms.conda_build_form()


def extract_json_object(proc_stdout: str) -> str:
    try:
        return proc_stdout[proc_stdout.index("{") : proc_stdout.rindex("}") + 1]
    except ValueError:
        return proc_stdout


def solve_conda(
    conda: PathLike,
    specs: dict[str, Dependency],
    locked: dict[str, LockedDependency],
    update: list[str],
    platform: str,
    channels: list[Channel],
    mapping_url: str,
) -> dict[str, LockedDependency]:
    """
    Solve (or update a previous solution of) conda specs for the given platform

    Parameters
    ----------
    conda :
        Path to conda, mamba, or micromamba
    specs :
        Conda package specifications
    locked :
        Previous solution for the given platform (conda packages only)
    update :
        Named of packages to update to the latest version compatible with specs
    platform :
        Target platform
    channels :
        Channels to query

    """

    conda_specs = [
        _to_match_spec(dep.name, dep.version, dep.build, dep.conda_channel)
        for dep in specs.values()
        if isinstance(dep, VersionedDependency) and dep.manager == "conda"
    ]
    conda_locked = {dep.name: dep for dep in locked.values() if dep.manager == "conda"}
    to_update = set(update).intersection(conda_locked)

    if to_update:
        dry_run_install = update_specs_for_arch(
            conda=conda,
            platform=platform,
            channels=channels,
            specs=conda_specs,
            locked=conda_locked,
            update=list(to_update),
        )
    else:
        dry_run_install = solve_specs_for_arch(
            conda=conda,
            platform=platform,
            channels=channels,
            specs=conda_specs,
        )
    logging.debug("dry_run_install:\n%s", dry_run_install)

    # extract dependencies from package plan
    planned = {}
    for action in dry_run_install["actions"]["FETCH"]:
        dependencies = {}
        for dep in action.get("depends") or []:
            matchspec = MatchSpec(dep)  # pyright: ignore[reportArgumentType]
            name = matchspec.name
            version = (
                matchspec.version.spec_str if matchspec.version is not None else ""
            )
            dependencies[name] = version

        locked_dependency = LockedDependency(
            name=action["name"],
            version=action["version"],
            manager="conda",
            platform=platform,
            dependencies=dependencies,
            # TODO: Normalize URL here and inject env vars
            url=normalize_url_with_placeholders(action["url"], channels=channels),
            # NB: virtual packages may have no hash
            hash=HashModel(
                md5=action["md5"] if "md5" in action else "",
                sha256=action.get("sha256"),
            ),
        )
        planned[action["name"]] = locked_dependency

    # propagate categories from explicit to transitive dependencies
    apply_categories(
        requested={k: v for k, v in specs.items() if v.manager == "conda"},
        planned=planned,
        mapping_url=mapping_url,
    )

    return planned


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


def _link_action_as_fetch(link_action: LinkAction) -> FetchAction | None:
    """Reuse a LINK action's metadata as a FETCH action when complete.

    Mamba/micromamba 2.6.0 returns LINK entries that already include every
    repodata field we need (``url``, ``fn``, ``md5``, ``sha256``,
    ``depends``, ``constrains``, ...). When that is the case we don't need
    to crack open ``repodata_record.json`` on disk -- doubly useful given
    that 2.6.0 reorganized the cache hierarchically by channel/subdir
    (see https://github.com/mamba-org/mamba/pull/4163), invalidating the
    flat-path lookup that ``_get_repodata_record`` used to do.

    Synthesis is rejected unless the LINK has every field that the
    downstream code (``solve_conda``) reads from a FETCH. Critically we
    require ``depends`` to be present *and* a list, otherwise an absent
    or null value would silently erase a package's runtime dependencies.
    """
    for key in _FETCH_KEYS_FROM_LINK:
        if key not in link_action or link_action[key] is None:  # type: ignore[literal-required]
            return None
    if not isinstance(link_action["depends"], list):  # type: ignore[typeddict-item]
        return None
    fetch: FetchAction = {  # pyright: ignore[reportAssignmentType]
        key: link_action[key]  # type: ignore[literal-required]
        for key in _FETCH_KEYS_FROM_LINK
    }
    fetch["sha256"] = link_action.get("sha256")  # type: ignore[typeddict-item]
    constrains = link_action.get("constrains")  # type: ignore[typeddict-item]
    fetch["constrains"] = constrains if isinstance(constrains, list) else []
    return fetch


# libmamba's token regex (`/t/([a-zA-Z0-9-_]{0,2}[a-zA-Z0-9-]*)`) matches
# `/t/<token>` with no requirement on a trailing path -- the URL may end
# right after the token. We accept the same character class without
# requiring a trailing slash.
_TOKEN_PATH_RE = re.compile(r"/t/[a-zA-Z0-9_-]*")


def _libmamba_strip_url_secrets(url: str) -> str:
    """Strip credentials and conda auth tokens from a URL.

    This is a *libmamba-compat helper*, not a general-purpose URL
    sanitizer. The callers (cache path derivation and cache record
    URL comparison) need bit-for-bit parity with how libmamba 2.6.0
    cleans URLs, including the deliberately overbroad ``/t/<chars>``
    handling pinned in tests. Don't reuse this for security-sensitive
    URL scrubbing without re-reading what libmamba's
    ``remove_secrets_and_login_credentials`` actually does.

    Covers the cases that conda-lock encounters for package URLs:

    - ``scheme://user:pass@host/...`` -> userinfo dropped
    - ``host/t/<token>`` and ``host/t/<token>/path`` -> token segment removed
    - ``user:pass@host/...`` (no scheme) -> userinfo dropped
    """
    if "://" in url:
        parsed = urlsplit(url)
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[1]
        cleaned_path = _TOKEN_PATH_RE.sub("", parsed.path)
        return urlunsplit(
            (parsed.scheme, netloc, cleaned_path, parsed.query, parsed.fragment)
        )
    # Scheme-less URL: ``urlsplit`` parks everything in ``path``, so
    # handle userinfo and token explicitly. This mirrors libmamba's
    # explicit no-scheme tests in test_cpp.cpp.
    at_pos = url.find("@")
    slash_pos = url.find("/")
    if at_pos != -1 and (slash_pos == -1 or at_pos < slash_pos):
        url = url[at_pos + 1 :]
    return _TOKEN_PATH_RE.sub("", url)


def _normalize_url_for_cache_path(url: str) -> str:
    """Apply mamba 2.6.0's URL normalization for package cache paths.

    Strips credentials/tokens (matching libmamba's
    ``remove_secrets_and_login_credentials``), then mirrors
    ``package_cache_folder_relative_path``: scheme separators ``://``
    become ``/`` and remaining ``:`` / ``\\`` are replaced with ``_``.
    Path separators are preserved.
    """
    cleaned = _libmamba_strip_url_secrets(url)
    return cleaned.replace("://", "/").replace(":", "_").replace("\\", "_")


def _normalize_url_for_compare(url: str) -> str:
    """Mirror libmamba's ``compare_cleaned_url`` semantics.

    libmamba parses both URLs as ``CondaURL``, forces the scheme to
    ``https``, removes credentials, and compares the strings with their
    trailing slashes stripped. Two URLs that differ only by
    ``http``/``https``, by trailing slash, or by carrying credentials
    must still be treated as equal. We don't have ``CondaURL`` here, but
    a credential-stripped + scheme-normalized + slash-trimmed compare
    matches the cases that occur in practice for conda packages.
    """
    cleaned = _libmamba_strip_url_secrets(url)
    parsed = urlsplit(cleaned)
    if parsed.scheme:
        cleaned = urlunsplit(parsed._replace(scheme="https"))
    return cleaned.rstrip("/")


def _hierarchical_cache_subpath(link_action: LinkAction) -> pathlib.Path | None:
    """Return ``<normalized base url>/<subdir>`` for the mamba 2.6.0 layout.

    Prefers the LINK action's ``url`` (stripping the filename), falling back
    to ``base_url`` + ``platform``. Returns ``None`` when neither is usable.
    """
    url = link_action.get("url")
    platform = link_action.get("platform") or link_action.get("subdir") or ""
    directory: str | None = None
    if url and "/" in url:
        directory = url.rsplit("/", 1)[0]
    elif link_action.get("base_url") and platform:
        base = link_action["base_url"].rstrip("/")
        suffix = f"/{platform}"
        if base.endswith(suffix):
            base = base[: -len(suffix)]
        directory = f"{base}/{platform}"
    if directory is None:
        return None
    return pathlib.Path(_normalize_url_for_cache_path(directory))


def _link_action_explicit_or_derived_url(link_action: LinkAction) -> str | None:
    """Best-effort URL for the linked package, with explicit-vs-derived
    intent baked into the name.

    Returns the LINK's explicit ``url`` (from mamba 2.6.0+ or any solver
    that emits the full repodata in LINK) if present. Otherwise derives
    one from ``base_url``/``platform``/``fn`` -- this is the older-conda
    sparse-LINK case, where the URL is reconstructed from disjoint
    fields and is therefore weaker evidence than an explicit value.
    Both paths are still useful for ``_record_matches_link`` to validate
    against a record on disk; future maintainers should treat a derived
    URL as a heuristic, not gospel.
    """
    url = link_action.get("url")
    if url:
        return url
    base_url = link_action.get("base_url")
    fn = link_action.get("fn")
    if not base_url or not fn:
        return None
    base = base_url.rstrip("/")
    platform = link_action.get("platform") or link_action.get("subdir")
    if platform:
        suffix = f"/{platform}"
        if not base.endswith(suffix):
            base = f"{base}{suffix}"
    return f"{base}/{fn}"


def _record_matches_link(
    record: FetchAction, link_action: LinkAction
) -> tuple[bool, str | None]:
    """Confirm a ``repodata_record.json`` corresponds to the LINK we're looking up.

    Mamba 2.6.0's hierarchy exists precisely to disambiguate same-named
    distributions across channels, so a basename match in the cache is not
    proof of identity. We validate ``name`` and ``version`` positively, and
    cross-check ``subdir`` / ``fn`` / ``md5`` / ``sha256`` whenever both
    sides expose them. URL vs channel resolves like this:

    - If both sides expose a URL (LINK's explicit ``url`` or one derived
      from ``base_url``+``fn``), compare with ``compare_cleaned_url``
      semantics. We do NOT additionally enforce channel-string equality
      here, so mirrored channels whose channel string spelling differs
      (``"conda-forge"`` vs the canonical URL) but whose package URLs
      match are accepted -- matching libmamba's precedence.
    - Otherwise fall back to a channel-string compare. A sparse LINK
      with no URL but a record carrying a URL would previously have been
      accepted with no validation past name/version; this branch closes
      that blind spot when both sides carry channel.

    Returns ``(matched, reason_if_rejected)``.
    """
    if record.get("name") != link_action.get("name"):
        return (
            False,
            f"name mismatch: record={record.get('name')!r} link={link_action.get('name')!r}",
        )
    if record.get("version") != link_action.get("version"):
        return (
            False,
            f"version mismatch: record={record.get('version')!r} link={link_action.get('version')!r}",
        )
    record_subdir = record.get("subdir")
    link_subdir = link_action.get("platform") or link_action.get("subdir")
    if record_subdir and link_subdir and record_subdir != link_subdir:
        return False, f"subdir mismatch: record={record_subdir!r} link={link_subdir!r}"
    for field in ("fn", "md5", "sha256"):
        link_val = link_action.get(field)  # type: ignore[call-overload]
        record_val = record.get(field)  # type: ignore[call-overload]
        if link_val and record_val and link_val != record_val:
            return False, f"{field} mismatch"
    record_url = record.get("url")
    link_url = _link_action_explicit_or_derived_url(link_action)
    url_compared = False
    if record_url and link_url:
        if _normalize_url_for_compare(link_url) != _normalize_url_for_compare(
            record_url
        ):
            return False, f"url mismatch: record={record_url!r} link={link_url!r}"
        url_compared = True
    if not url_compared:
        link_channel = link_action.get("channel")
        record_channel = record.get("channel")
        if link_channel and record_channel and link_channel != record_channel:
            return (
                False,
                f"channel mismatch: record={record_channel!r} link={link_channel!r}",
            )
    return True, None


def _candidate_record_paths(
    pkgs_dir: pathlib.Path,
    dist_name: str,
    link_action: LinkAction,
) -> list[pathlib.Path]:
    """Candidate ``repodata_record.json`` locations, in priority order.

    Conda and pre-2.6 mamba use a flat layout (``<pkgs_dir>/<dist_name>/...``).
    Mamba/micromamba 2.6.0 nests packages under the channel and subdir
    derived from the package URL (see mamba-org/mamba#4163). We compute the
    expected hierarchical path from the LINK metadata rather than walking the
    cache, then fall back to the legacy flat path.
    """
    candidates: list[pathlib.Path] = []
    sub = _hierarchical_cache_subpath(link_action)
    if sub is not None:
        candidates.append(pkgs_dir / sub / dist_name / "info" / "repodata_record.json")
    candidates.append(pkgs_dir / dist_name / "info" / "repodata_record.json")
    return candidates


def _get_repodata_record(
    pkgs_dirs: list[pathlib.Path],
    dist_name: str,
    link_action: LinkAction,
) -> FetchAction | None:
    """Get the repodata_record.json of a given distribution from the package cache.

    On rare occasion during the CI tests, conda fails to find a package in the
    package cache, perhaps because the package is still being processed? Waiting
    for 0.1 seconds seems to solve the issue. Here we allow for a full second
    to elapse before giving up.

    Distinct failure modes (missing file, JSON corruption, identity
    mismatch) are logged at DEBUG so that a final ``not found`` doesn't
    bury whether we never saw the file or saw it and rejected it.
    """
    NUM_RETRIES = 10
    # Track failure reasons in two buckets so the final warning surfaces
    # the most actionable one. "Rejected" beats "missing": if we found a
    # repodata_record.json and rejected it for, say, sha256 mismatch,
    # that's the signal worth logging -- not the trivia that the flat
    # legacy fallback path didn't exist.
    last_rejected: str | None = None
    last_missing: str | None = None
    for retry in range(1, NUM_RETRIES + 1):
        for pkgs_dir in pkgs_dirs:
            for candidate in _candidate_record_paths(pkgs_dir, dist_name, link_action):
                if not candidate.is_file():
                    last_missing = f"file not found: {candidate}"
                    logger.debug(last_missing)
                    continue
                try:
                    with open(candidate) as f:
                        record: FetchAction = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    last_rejected = f"failed to read {candidate}: {exc}"
                    logger.debug(last_rejected)
                    continue
                matched, reason = _record_matches_link(record, link_action)
                if matched:
                    return record
                last_rejected = f"identity mismatch at {candidate}: {reason}"
                logger.debug(last_rejected)
        # Per-retry messages stay at DEBUG so a single missing package
        # doesn't drown operator output in 11 nearly-identical warnings.
        # We log a single WARNING below after the retry loop ends.
        final_reason = last_rejected or last_missing
        logger.debug(
            f"Failed to find repodata_record.json for {dist_name} "
            f"(last reason: {final_reason}). "
            f"Retrying in 0.1 seconds ({retry}/{NUM_RETRIES})"
        )
        time.sleep(0.1)
    final_reason = last_rejected or last_missing
    logger.warning(
        f"Failed to find repodata_record.json for {dist_name}. Giving up. "
        f"Last reason: {final_reason}"
    )
    return None


def _get_pkgs_dirs(
    *,
    conda: PathLike,
    platform: str,
    method: Literal["config", "info"] | None = None,
) -> list[pathlib.Path]:
    """Extract the package cache directories from the conda configuration."""
    if method is None:
        method = "config" if is_micromamba(conda) else "info"
    if method == "config":
        # 'package cache' was added to 'micromamba info' in v1.4.6.
        args = [str(conda), "config", "--json", "list", "pkgs_dirs"]
    elif method == "info":
        args = [str(conda), "info", "--json"]
    env = conda_env_override(platform)
    output = subprocess.check_output(args, env=env).decode()
    json_object_str = extract_json_object(output)
    json_object: dict[str, Any] = json.loads(json_object_str)
    pkgs_dirs_list: list[str]
    if "pkgs_dirs" in json_object:
        pkgs_dirs_list = json_object["pkgs_dirs"]
    elif "package cache" in json_object:
        pkgs_dirs_list = json_object["package cache"]
    else:
        raise ValueError(
            f"Unable to extract pkgs_dirs from {json_object}. "
            "Please report this issue to the conda-lock developers."
        )
    pkgs_dirs = [pathlib.Path(d) for d in pkgs_dirs_list]
    return pkgs_dirs


def _reconstruct_fetch_actions(
    conda: PathLike,
    platform: str,
    dry_run_install: DryRunInstall | dict[str, dict[str, list[Any]]],
) -> DryRunInstall | dict[str, dict[str, list[Any]]]:
    """
    Conda may choose to link a previously downloaded distribution from pkgs_dirs rather
    than downloading a fresh one. Find the repodata record in existing distributions
    that have only a LINK action, and use it to synthesize a corresponding FETCH action
    with the metadata we need to extract for the package plan.
    """
    if "LINK" not in dry_run_install["actions"]:
        dry_run_install["actions"]["LINK"] = []
    if "FETCH" not in dry_run_install["actions"]:
        dry_run_install["actions"]["FETCH"] = []

    link_actions = {p["name"]: p for p in dry_run_install["actions"]["LINK"]}
    fetch_actions = {p["name"]: p for p in dry_run_install["actions"]["FETCH"]}
    link_only_names = set(link_actions.keys()).difference(fetch_actions.keys())

    # Mamba 2.6.0 puts the full repodata into LINK actions, so we can often
    # synthesize FETCH without going to disk. Resolve those first and only
    # query the (potentially expensive) ``pkgs_dirs`` listing if anything
    # is left over.
    deferred: list[tuple[str, LinkAction]] = []
    for link_pkg_name in link_only_names:
        link_action = link_actions[link_pkg_name]
        from_link = _link_action_as_fetch(link_action)
        if from_link is not None:
            dry_run_install["actions"]["FETCH"].append(from_link)
        else:
            deferred.append((link_pkg_name, link_action))

    pkgs_dirs = (
        _get_pkgs_dirs(conda=conda, platform=platform) if deferred else []
    )

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
        repodata = _get_repodata_record(pkgs_dirs, dist_name, link_action)
        if repodata is None:
            raise FileNotFoundError(
                f"Distribution '{dist_name}' not found in pkgs_dirs {pkgs_dirs}"
            )
        dry_run_install["actions"]["FETCH"].append(repodata)
    return dry_run_install


def solve_specs_for_arch(
    conda: PathLike,
    channels: Sequence[Channel],
    specs: list[str],
    platform: str,
) -> DryRunInstall | dict[str, dict[str, list[Any]]]:
    """
    Solve conda specifications for the given platform

    Parameters
    ----------
    conda :
        Path to conda, mamba, or micromamba
    channels :
        Channels to query
    specs :
        Conda package specifications
    platform :
        Target platform

    """
    args: MutableSequence[str] = [
        str(conda),
        "create",
        "--prefix",
        os.path.join(conda_pkgs_dir(), "prefix"),
        "--dry-run",
        "--json",
    ]
    args.extend(_get_conda_flags(channels=channels, platform=platform))
    args.extend(specs)
    logger.info("%s using specs %s", platform, specs)
    logger.debug(f"Running command {shlex.join(args)}")
    proc = subprocess.run(  # noqa: UP022  # Poetry monkeypatch breaks capture_output
        [str(arg) for arg in args],
        env=conda_env_override(platform),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )

    def print_proc(proc: subprocess.CompletedProcess) -> None:
        print(f"    Command: {proc.args}", file=sys.stderr)
        if proc.stdout:
            print(f"    STDOUT:\n{proc.stdout}", file=sys.stderr)
        if proc.stderr:
            print(f"    STDERR:\n{proc.stderr}", file=sys.stderr)

    try:
        proc.check_returncode()
    except subprocess.CalledProcessError:
        try:
            err_json = json.loads(proc.stdout)
            try:
                message = err_json["message"]
            except KeyError:
                print("Message key not found in json! returning the full json text")
                message = err_json
        except json.JSONDecodeError as e:
            print(f"Failed to parse json, {e}", file=sys.stderr)
            message = proc.stdout

        print(
            f"Could not lock the environment for platform {platform}", file=sys.stderr
        )
        if message:
            print(message, file=sys.stderr)
        print_proc(proc)

        raise

    try:
        dryrun_install: DryRunInstall = json.loads(extract_json_object(proc.stdout))
        return _reconstruct_fetch_actions(conda, platform, dryrun_install)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse json: '{proc.stdout}'") from e


def _get_installed_conda_packages(
    conda: PathLike,
    platform: str,
    prefix: str,
) -> dict[str, LinkAction]:
    """
    Get the installed conda packages for the given prefix.

    Try to get installed packages, first with --no-pip flag, then without if that fails.
    The --no-pip flag was added in Conda v2.1.0 (2013), but for mamba/micromamba only in
    v2.0.7 (March 2025).
    """
    try:
        output = subprocess.check_output(
            [str(conda), "list", "--no-pip", "-p", prefix, "--json"],
            env=conda_env_override(platform),
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        err_output = (
            e.output.decode("utf-8") if isinstance(e.output, bytes) else e.output
        )
        if "The following argument was not expected: --no-pip" in err_output:
            logger.warning(
                f"The '--no-pip' flag is not supported by {conda}. Please consider upgrading."
            )
            # Retry without --no-pip
            output = subprocess.check_output(
                [str(conda), "list", "-p", prefix, "--json"],
                env=conda_env_override(platform),
                stderr=subprocess.STDOUT,
            )
        else:
            # Re-raise if it's a different error.
            raise
    decoded_output = output.decode("utf-8")
    installed: dict[str, LinkAction] = {
        entry["name"]: entry for entry in json.loads(decoded_output)
    }
    return installed


def update_specs_for_arch(
    conda: PathLike,
    specs: list[str],
    locked: dict[str, LockedDependency],
    update: list[str],
    platform: str,
    channels: Sequence[Channel],
) -> DryRunInstall | dict[str, dict[str, list[Any]]]:
    """
    Update a previous solution for the given platform

    Parameters
    ----------
    conda :
        Path to conda, mamba, or micromamba
    specs :
        Conda package specifications
    locked :
        Previous solution for the given platform (conda packages only)
    update :
        Named of packages to update to the latest version compatible with specs
    platform :
        Target platform
    channels :
        Channels to query

    """

    with fake_conda_environment(locked.values(), platform=platform) as prefix:
        installed = _get_installed_conda_packages(conda, platform, prefix)
        spec_for_name = {MatchSpec(v).name: v for v in specs}  # pyright: ignore
        to_update = [
            spec_for_name[name] for name in set(installed).intersection(update)
        ]
        if to_update:
            # NB: [micro]mamba and mainline conda have different semantics for `install` and `update`
            # - conda:
            #   * update -> apply all nonmajor updates unconditionally (unless pinned)
            #   * install -> install or update target to latest version compatible with constraint
            # - micromamba:
            #   * update -> update target to latest version compatible with constraint
            #   * install -> update target if current version incompatible with constraint, otherwise _do nothing_
            # - mamba:
            #   * update -> apply all nonmajor updates unconditionally (unless pinned)
            #   * install -> update target if current version incompatible with constraint, otherwise _do nothing_
            # Our `update` should always update the target to the latest version compatible with the constraint,
            # while updating as few other packages as possible. With mamba this can only be done with pinning.
            if pathlib.Path(conda).name.startswith("mamba"):
                # pin non-updated packages to prevent _any_ movement
                pinned_filename = pathlib.Path(prefix) / "conda-meta" / "pinned"
                assert not pinned_filename.exists()
                with open(pinned_filename, "w") as pinned:
                    for name in set(installed.keys()).difference(update):
                        pinned.write(f"{name} =={installed[name]['version']}\n")
                args = [
                    str(conda),
                    "update",
                    *_get_conda_flags(channels=channels, platform=platform),
                ]
                print(
                    "Warning: mamba cannot update single packages without resorting to pinning. "
                    "If the update fails to solve, try with conda or micromamba instead.",
                    file=sys.stderr,
                )
            else:
                args = [
                    str(conda),
                    "update" if is_micromamba(conda) else "install",
                    *_get_conda_flags(channels=channels, platform=platform),
                ]
            cmd = [
                str(arg)
                for arg in [*args, "-p", prefix, "--json", "--dry-run", *to_update]
            ]
            logger.debug(f"Running command {shlex.join(cmd)}")
            proc = subprocess.run(  # noqa: UP022  # Poetry monkeypatch breaks capture_output
                cmd,
                env=conda_env_override(platform),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf8",
            )

            try:
                proc.check_returncode()
            except subprocess.CalledProcessError as exc:
                err_json = json.loads(proc.stdout)
                raise RuntimeError(
                    f"Could not lock the environment for platform {platform}: {err_json.get('message')}"
                ) from exc

            dryrun_install: DryRunInstall = json.loads(extract_json_object(proc.stdout))
        else:
            dryrun_install = {"actions": {"LINK": [], "FETCH": []}}

        if "actions" not in dryrun_install:
            dryrun_install["actions"] = {"LINK": [], "FETCH": []}

        updated = {entry["name"]: entry for entry in dryrun_install["actions"]["LINK"]}
        for package in set(installed).difference(updated):
            # This is the case where the package is unchanged.
            # First create a FETCH action based on the original lockfile entry.
            original_lockfile_entry = locked[package]
            original_fetch_action = original_lockfile_entry.to_fetch_action()
            dryrun_install["actions"]["FETCH"].append(original_fetch_action)

            # Then create a LINK action to indicate that the package is installed.
            installed_link_action = installed[package]
            dryrun_install["actions"]["LINK"].append(installed_link_action)

        reconstructed = _reconstruct_fetch_actions(conda, platform, dryrun_install)
        return reconstructed


@contextmanager
def fake_conda_environment(
    locked: Iterable[LockedDependency], platform: str
) -> Iterator[str]:
    """
    Create a fake conda prefix containing metadata corresponding to the provided dependencies

    Parameters
    ----------
    locked :
        Previous solution
    platform :
        Target platform

    """
    with temporary_directory(prefix="conda-lock-fake-env-") as prefix:
        conda_meta = pathlib.Path(prefix) / "conda-meta"
        conda_meta.mkdir()
        (conda_meta / "history").touch()
        for dep in (
            dep for dep in locked if dep.manager == "conda" and dep.platform == platform
        ):
            url = urlsplit(dep.url)
            path = pathlib.PurePosixPath(url.path)
            channel = urlunsplit(
                (url.scheme, url.hostname, str(path.parent), None, None)
            )
            truncated_path = path
            while truncated_path.suffix in {".tar", ".bz2", ".gz", ".conda"}:
                truncated_path = truncated_path.with_suffix("")
            build = truncated_path.name.split("-")[-1]
            try:
                build_number = int(build.split("_")[-1])
            except ValueError:
                build_number = 0
            entry = {
                "name": dep.name,
                "channel": channel,
                "url": dep.url,
                "md5": dep.hash.md5,
                "build": build,
                "build_number": build_number,
                "version": dep.version,
                "subdir": path.parent.name,
                "fn": path.name,
                "depends": [f"{k} {v}".strip() for k, v in dep.dependencies.items()],
            }
            # mamba requires these to be stringlike so null are not allowed here
            if dep.hash.sha256 is not None:
                entry["sha256"] = dep.hash.sha256

            with open(conda_meta / (truncated_path.name + ".json"), "w") as f:
                json.dump(entry, f, indent=2)
            make_fake_python_binary(prefix)
        yield prefix


def make_fake_python_binary(prefix: str) -> None:
    """Create a fake python binary in the given prefix.

    Our fake Conda environment contains metadata indicating that `python`
    is installed in the prefix, however no packages are installed.

    This is intended to prevent failure of `PrefixData.load_site_packages`
    which was introduced in libmamba v2. That function invokes the command
    `python -q -m pip inspect --local` to check for installed pip packages,
    where the `python` binary is the one in the conda prefix. Our fake
    prefix only contains the package metadata records, not the actual
    packages or binaries. At this stage for conda-lock, we are only
    interested in the conda packages, so we spoof the `python` binary
    to return an empty stdout so that things proceed without error.
    """
    # Write the fake Python script to a file
    fake_python_script = pathlib.Path(prefix) / "fake_python_script.py"
    fake_python_script.write_text(
        dedent(
            """\
            import sys
            import shlex

            cmd = shlex.join(sys.argv)

            stderr_message = f'''\
            This is a fake python binary generated by conda-lock.

            It prevents libmamba from failing when it tries to check for installed \
            pip packages.

            For more details, see the docstring for `make_fake_python_binary`.

            This was called as:
                {cmd}
            '''

            print(stderr_message, file=sys.stderr, flush=True, end='')

            if "-m pip" in cmd:
                # Simulate an empty `pip inspect` output
                print('{}', flush=True)
            else:
                raise RuntimeError("Expected to invoke pip module with `-m pip`.")
            """
        )
    )

    if sys.platform == "win32":
        # On Windows, copy sys.executable to prefix/Scripts/python.exe
        fake_python_binary = pathlib.Path(prefix) / "Scripts" / "python.exe"
        fake_python_binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sys.executable, fake_python_binary)

        # Adjust the environment to ensure our fake script is executed
        # Create a wrapper batch file that sets PYTHONPATH
        wrapper_batch = pathlib.Path(prefix) / "Scripts" / "python.bat"
        wrapper_batch_content = dedent(f"""\
            @echo off
            set PYTHONPATH={prefix};%PYTHONPATH%
            "{fake_python_binary}" %*
        """)
        wrapper_batch.write_text(wrapper_batch_content)
    else:
        # On Unix-like systems, create a shell script that calls the script
        fake_python_binary = pathlib.Path(prefix) / "bin" / "python"
        fake_python_binary.parent.mkdir(parents=True, exist_ok=True)
        shell_script_content = dedent(f"""\
            #!/usr/bin/env sh
            "{sys.executable}" "{fake_python_script}" "$@"
        """)
        fake_python_binary.write_text(shell_script_content)
        fake_python_binary.chmod(0o755)
