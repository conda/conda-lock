import json
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

from collections.abc import (
    Generator,
    Iterable,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
)
from contextlib import contextmanager
from posixpath import expandvars
from textwrap import dedent
from typing import Any, cast
from urllib.parse import unquote, urlsplit, urlunsplit

from conda_lock.errors import MetadataConsistencyError
from conda_lock.interfaces.vendored_conda import MatchSpec
from conda_lock.invoke_conda import (
    PathLike,
    _get_conda_flags,
    conda_env_override,
    conda_pkgs_dir,
    extract_json_object,
    is_micromamba,
)
from conda_lock.lockfile import apply_categories
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency
from conda_lock.models.channel import Channel, normalize_url_with_placeholders
from conda_lock.models.dry_run_install import DryRunInstall, FetchAction, LinkAction
from conda_lock.models.lock_spec import Dependency, VersionedDependency
from conda_lock.solver.channel_metadata import (
    metadata_fallback,
    verified_channel_records,
)
from conda_lock.solver.dry_run import reconstruct_fetch_actions_in_place
from conda_lock.solver.graph_integrity import (
    check_categories_assigned,
    check_dependencies_present,
    check_dependency_constraints,
    packages_requiring_repair,
)
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


def _dependency_versions(depends: Iterable[str]) -> dict[str, str]:
    dependency_specs: dict[str, list[MatchSpec]] = {}
    for dep in depends:
        matchspec = MatchSpec(dep)  # pyright: ignore[reportArgumentType]
        dependency_specs.setdefault(matchspec.name, []).append(matchspec)

    dependencies = {}
    for name, matchspecs in dependency_specs.items():
        merged_matchspec = (
            matchspecs[0] if len(matchspecs) == 1 else MatchSpec.merge(matchspecs)[0]
        )
        dependencies[name] = (
            merged_matchspec.version.spec_str
            if merged_matchspec.version is not None
            else ""
        )
    return dependencies


def _metadata_channels(
    channels: Sequence[Channel], specs: Sequence[str]
) -> list[Channel]:
    # A source request such as comet_ml::comet_ml names a channel without adding
    # it to the general channel list. Include it for verification only: adding
    # it to the solve's --channel flags could change package selection/priority.
    result = list(channels)
    for spec in specs:
        if "::" in spec:
            channel = Channel.from_string(spec.rsplit("::", 1)[0])
            if channel not in result:
                result.append(channel)
    return result


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

    metadata_fallback()  # Reject typos before invoking the solver.
    conda_specs = [
        _to_match_spec(dep.name, dep.version, dep.build, dep.conda_channel)
        for dep in specs.values()
        if isinstance(dep, VersionedDependency) and dep.manager == "conda"
    ]
    conda_locked = {dep.name: dep for dep in locked.values() if dep.manager == "conda"}
    metadata_channels = _metadata_channels(channels, conda_specs)
    to_update = set(update).intersection(conda_locked)

    verified_cache: dict[str, dict[str, Any]] = {}
    if to_update:
        dry_run_install = update_specs_for_arch(
            conda=conda,
            platform=platform,
            channels=channels,
            specs=conda_specs,
            locked=conda_locked,
            update=list(to_update),
            verified_cache=verified_cache,
        )
    else:
        dry_run_install = solve_specs_for_arch(
            conda=conda,
            platform=platform,
            channels=channels,
            specs=conda_specs,
            verified_cache=verified_cache,
        )
    logging.debug("dry_run_install:\n%s", dry_run_install)

    records = dry_run_install["actions"]["FETCH"]
    verified = verified_channel_records(
        conda,
        platform,
        records,
        channels=metadata_channels,
        verified_cache=verified_cache,
    )
    if verified is not None:
        records = list(verified.values())
    check_dependency_constraints(records)

    # extract dependencies from package plan
    planned = {}
    for action in records:
        dependencies = _dependency_versions(action.get("depends") or [])

        locked_dependency = LockedDependency(
            name=action["name"],
            version=action["version"],
            manager="conda",
            platform=platform,
            dependencies=dependencies,
            # TODO: Normalize URL here and inject env vars
            url=normalize_url_with_placeholders(
                action["url"], channels=metadata_channels
            ),
            # NB: virtual packages may have no hash
            hash=HashModel(
                md5=action["md5"] if "md5" in action else "",
                sha256=action.get("sha256"),
            ),
        )
        planned[action["name"]] = locked_dependency

    check_dependencies_present(planned)

    # propagate categories from explicit to transitive dependencies
    apply_categories(
        requested={k: v for k, v in specs.items() if v.manager == "conda"},
        planned=planned,
        mapping_url=mapping_url,
    )

    # A solver may select pip alongside Python without a Python -> pip edge,
    # despite injection being disabled. Retain its selection and dependency
    # metadata. Otherwise these category-less packages disappear on serialization.
    # Only orphaned pip inherits Python's categories; explicitly requested or
    # genuinely connected pip keeps the categories assigned above.
    if "pip" in planned and not planned["pip"].categories and "python" in planned:
        for category in sorted(planned["python"].categories):
            apply_categories(
                requested={
                    "pip": VersionedDependency(
                        name="pip", version="*", category=category
                    )
                },
                planned=planned,
                mapping_url=mapping_url,
            )

    check_categories_assigned(planned)
    return planned


def solve_specs_for_arch(
    conda: PathLike,
    channels: Sequence[Channel],
    specs: list[str],
    platform: str,
    *,
    verified_cache: dict[str, dict[str, Any]] | None = None,
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
        reconstruct_fetch_actions_in_place(
            conda,
            platform,
            dryrun_install,
            channels=_metadata_channels(channels, specs),
            verified_cache=verified_cache,
        )
        return dryrun_install
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
    output_json = json.loads(output.decode("utf-8"))
    if isinstance(output_json, MutableMapping) and "packages" in output_json:
        output_json = output_json["packages"]
    installed: dict[str, LinkAction] = {entry["name"]: entry for entry in output_json}
    return installed


def _unrequested_packages(
    records: Mapping[str, Mapping[str, Any]], requested: Iterable[str]
) -> set[str]:
    """Find packages outside the requested closure; verify before omitting them.

    If a root or reachable edge is missing, defer pruning so the solver can
    repair the graph and the final consistency checks can validate it.
    """
    needed: set[str] = set()
    pending = list(requested)
    while True:
        if not pending:
            # As with category assignment, retain a possible pip injection once
            # Python is reachable. Missing edges still defer pruning, and this
            # exception does not excuse unrelated packages.
            if "python" in needed and "pip" in records and "pip" not in needed:
                pending.append("pip")
            else:
                break
        name = pending.pop()
        if name in needed or name.startswith("__"):
            continue
        if name not in records:
            return set()
        needed.add(name)
        pending.extend(MatchSpec(spec).name for spec in records[name]["depends"])
    return set(records).difference(needed)


def _log_artifact_replacements(
    platform: str,
    locked: Mapping[str, LockedDependency],
    unavailable: set[str],
    records: Sequence[Mapping[str, Any]],
) -> None:
    # Filenames include versions and builds without exposing channel credentials.
    selected = {record["name"]: record for record in records}
    for name in sorted(unavailable):
        old_filename = unquote(urlsplit(locked[name].url).path.rsplit("/", 1)[-1])
        new_filename = (
            unquote(urlsplit(selected[name]["url"]).path.rsplit("/", 1)[-1])
            if name in selected
            else "not selected"
        )
        logger.info(
            "Artifact replacement on %s: %s -> %s",
            platform,
            old_filename,
            new_filename,
        )


def _packages_requiring_replacement(
    records: Mapping[str, Mapping[str, Any]], unavailable: set[str]
) -> set[str]:
    """Re-solve affected parents instead of retaining an inconsistent prefix."""
    parents: dict[str, set[str]] = {}
    for name, record in records.items():
        for dependency in record["depends"]:
            parents.setdefault(MatchSpec(dependency).name, set()).add(name)
    affected = set(unavailable)
    pending = list(unavailable)
    while pending:
        for name in parents.get(pending.pop(), set()).difference(affected):
            affected.add(name)
            pending.append(name)
    return affected


def _write_update_pins(
    prefix: str,
    source_specs: Sequence[str],
    installed: Mapping[str, LinkAction],
    update: Sequence[str],
    *,
    pin_installed: bool,
) -> None:
    # Conda may relax historical requests, so replacement solves also express
    # source requirements as hard pins. Mamba's existing selective-update policy
    # additionally pins retained packages; unavailable artifacts never enter here.
    pins = list(source_specs)
    if pin_installed:
        pins.extend(
            f"{name} =={installed[name]['version']}"
            for name in sorted(set(installed).difference(update))
        )
    if pins:
        filename = pathlib.Path(prefix) / "conda-meta" / "pinned"
        assert not filename.exists()
        filename.write_text("\n".join(pins) + "\n")


def update_specs_for_arch(
    conda: PathLike,
    specs: list[str],
    locked: dict[str, LockedDependency],
    update: list[str],
    platform: str,
    channels: Sequence[Channel],
    *,
    verified_cache: dict[str, dict[str, Any]] | None = None,
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

    if verified_cache is None:
        verified_cache = {}
    metadata_channels = _metadata_channels(channels, specs)
    expected_records = [dependency.to_fetch_action() for dependency in locked.values()]
    current_records = verified_channel_records(
        conda,
        platform,
        expected_records,
        channels=metadata_channels,
        allow_missing=True,
        verified_cache=verified_cache,
    )
    metadata_verified = current_records is not None
    unavailable: set[str] = set()
    if current_records is None:
        current_records = {record["name"]: record for record in expected_records}
    else:
        unavailable = set(locked).difference(current_records)
        if unavailable:
            logger.warning(
                "The channel query no longer lists the locked artifacts for %s. "
                "Solving again under the source requirements; these packages may "
                "change even if they were not named in --update.",
                sorted(unavailable),
            )
        refreshed = sorted(
            name
            for name, dep in locked.items()
            if name in current_records
            and dep.dependencies
            != _dependency_versions(current_records[name]["depends"])
        )
        if refreshed:
            logger.info(
                "Refreshed dependency metadata from verified channel artifacts: %s",
                refreshed,
            )
    requested = {MatchSpec(spec).name for spec in specs}
    if metadata_verified:
        unrequested = _unrequested_packages(current_records, requested)
        current_records = {
            name: record
            for name, record in current_records.items()
            if name not in unrequested
        }
    inconsistent = (
        packages_requiring_repair(list(current_records.values()))
        if metadata_verified
        else set()
    )
    repair = unavailable | inconsistent
    reinstall = (
        _packages_requiring_replacement(current_records, repair) | set(update)
        if repair
        else set()
    )
    if inconsistent:
        logger.info(
            "Refreshed requirements need a new selection for %s; including their "
            "dependents in the initial update solve.",
            sorted(inconsistent),
        )
    with fake_conda_environment(
        (
            dep
            for name, dep in locked.items()
            if name in current_records and name not in reinstall
        ),
        platform=platform,
        repodata=current_records,
        specs=specs if repair else None,
    ) as prefix:
        installed = _get_installed_conda_packages(conda, platform, prefix)
        spec_for_name = {MatchSpec(v).name: v for v in specs}  # pyright: ignore
        to_update = [
            spec_for_name.get(name, name)
            for name in sorted(set(installed).intersection(update))
        ]
        if repair:
            # Mamba update ignores absent packages. Install all current roots so
            # unavailable roots and transitive packages can be selected afresh.
            # Omit explicit update targets from the fake prefix too: otherwise
            # Mamba install may keep their old versions instead of updating them.
            if is_micromamba(conda) or pathlib.Path(conda).name.startswith("mamba"):
                to_update = list(specs)
            else:
                # Conda uses the fake prefix's requested-spec history to retain
                # other roots and their source constraints without upgrading them.
                to_update = [
                    spec for spec in specs if MatchSpec(spec).name not in installed
                ]
            to_update += [name for name in update if name not in requested]
        dryrun_install: DryRunInstall
        if to_update:
            _write_update_pins(
                prefix,
                specs if repair else (),
                installed,
                update,
                pin_installed=pathlib.Path(conda).name.startswith("mamba"),
            )
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
                args = [
                    str(conda),
                    "install" if repair else "update",
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
                    "update" if is_micromamba(conda) and not repair else "install",
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
                message = err_json.get("message") or (
                    f"solver exited with status {proc.returncode}"
                )
                removal_context = (
                    f". Previously locked artifacts are no longer listed for "
                    f"{sorted(unavailable)}. Replacements must satisfy the current "
                    "source requirements; review any pins that prevent replacement."
                    if unavailable
                    else ""
                )
                raise RuntimeError(
                    f"Could not lock the environment for platform {platform}: "
                    f"{message}{removal_context}"
                ) from exc

            dryrun_install = json.loads(extract_json_object(proc.stdout))
        else:
            dryrun_install = {"actions": {"LINK": [], "FETCH": []}}

        actions = dryrun_install.setdefault("actions", {"LINK": [], "FETCH": []})
        actions.setdefault("LINK", [])
        actions.setdefault("FETCH", [])
        updated = {entry["name"] for entry in actions["LINK"]}
        removed = {entry["name"] for entry in actions.get("UNLINK", [])}
        for package in set(installed).difference(updated, removed):
            # This is the case where the package is unchanged.
            # Carry refreshed metadata, or the original lock metadata in skip mode.
            original_fetch_action = cast(FetchAction, current_records[package].copy())
            dryrun_install["actions"]["FETCH"].append(original_fetch_action)

            # Then create a LINK action to indicate that the package is installed.
            installed_link_action = installed[package]
            dryrun_install["actions"]["LINK"].append(installed_link_action)

        reconstruct_fetch_actions_in_place(
            conda,
            platform,
            dryrun_install,
            channels=metadata_channels,
            verified_cache=verified_cache,
        )
        records = {
            record["name"]: record for record in dryrun_install["actions"]["FETCH"]
        }
        verified = verified_channel_records(
            conda,
            platform,
            list(records.values()),
            channels=metadata_channels,
            verified_cache=verified_cache,
        )
        if verified is None and repair:
            raise MetadataConsistencyError(
                "Cannot verify replacements for unavailable or inconsistent locked artifacts",
                recovery=(
                    "Replacement packages require channel verification. Use a solver "
                    "with batched repoquery support, or set "
                    "CONDA_LOCK_METADATA_FALLBACK=unbatched for slower full verification. "
                    "See https://conda.github.io/conda-lock/flags/#conda_lock_metadata_fallback"
                ),
            )
        if verified is not None:
            records = verified
            dryrun_install["actions"]["FETCH"] = [
                cast(FetchAction, record) for record in records.values()
            ]
            unrequested = _unrequested_packages(records, requested)
            if metadata_verified and unrequested.issubset(current_records):
                # Only old packages can be omitted. New unexplained orphans
                # still fail the final category guard. The graph is verified.
                dryrun_install["actions"]["FETCH"] = [
                    record
                    for record in dryrun_install["actions"]["FETCH"]
                    if record["name"] not in unrequested
                ]
                dryrun_install["actions"]["LINK"] = [
                    record
                    for record in dryrun_install["actions"]["LINK"]
                    if record["name"] not in unrequested
                ]
        if unavailable:
            _log_artifact_replacements(
                platform, locked, unavailable, dryrun_install["actions"]["FETCH"]
            )
        return dryrun_install


@contextmanager
def fake_conda_environment(
    locked: Iterable[LockedDependency],
    platform: str,
    repodata: Mapping[str, Mapping[str, Any]] | None = None,
    specs: Sequence[str] | None = None,
) -> Generator[str]:
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
        if specs is not None:
            # Conda reads source requirements from its normal transaction history.
            # This prefix is synthetic, so the date carries no historical meaning.
            (conda_meta / "history").write_text(
                "==> 2000-01-01 00:00:00 <==\n"
                f"# cmd: conda-lock\n# update specs: {list(specs)!r}\n"
            )
        for dep in (
            dep for dep in locked if dep.manager == "conda" and dep.platform == platform
        ):
            url = urlsplit(dep.url)
            path = pathlib.PurePosixPath(url.path)
            channel = urlunsplit(
                (url.scheme, url.hostname, str(path.parent), None, None)
            )
            truncated_path = path.with_name(unquote(path.name))
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
                "fn": unquote(path.name),
                "depends": [f"{k} {v}".strip() for k, v in dep.dependencies.items()],
            }
            if repodata is not None:
                # Retain raw dependency build constraints, constrains and other
                # solver metadata from the channel record, or the original
                # lockfile record when verification was explicitly skipped.
                entry.update(repodata[dep.name])
                # Expand placeholders at the solver IO boundary; retain them
                # in lockfile metadata.
                entry["url"] = expandvars(entry["url"])
                # repoquery's channel may be a bare name or empty for file URLs.
                # Conda prefix records need the actual subdirectory location.
                artifact_url = urlsplit(entry["url"])
                entry["channel"] = urlunsplit(
                    artifact_url._replace(
                        path=artifact_url.path.rsplit("/", 1)[0], query="", fragment=""
                    )
                )
            # mamba requires these to be stringlike so null are not allowed here
            if dep.hash.sha256 is not None:
                entry["sha256"] = dep.hash.sha256
            elif entry.get("sha256") is None:
                entry.pop("sha256", None)

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
