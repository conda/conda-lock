"""Orchestration entry points for conda-lock's solver pipeline.

The narrow responsibilities split out of this module live under
``conda_lock.solver``:

- ``solver.repodata_cache``: URL normalization, cache path
  derivation, record identity checks, mamba 2.1.1-2.3.3 stub-record
  detection, and ``info/index.json``-based healing.
- ``solver.dry_run``: normalize a solver's ``--dry-run --json``
  output into a uniform shape with rich FETCH actions, falling back
  to disk when the LINK metadata is sparse.
- ``solver.lockfile_heal``: repair carry-forward empty
  ``dependencies`` from the local cache before
  ``fake_conda_environment`` propagates them.
- ``solver.graph_integrity``: forward-reachability check on the
  planned package set, with the
  ``CONDA_LOCK_ALLOW_ORPHANED_LOCKFILE`` escape hatch.

What's left here is glue: drive the conda/mamba subprocess for the
fresh-solve and update paths, translate the dryrun into
``LockedDependency`` shapes, run categorization, and assert graph
integrity. The fake-prefix machinery used by ``--update`` also
lives here because it is a peer of the subprocess invocation, not
a cache or graph concern.
"""

import json
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

from collections.abc import Iterable, Iterator, MutableSequence, Sequence
from contextlib import contextmanager
from textwrap import dedent
from urllib.parse import urlsplit, urlunsplit

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
from conda_lock.models.dry_run_install import DryRunInstall, LinkAction
from conda_lock.models.lock_spec import Dependency, VersionedDependency
from conda_lock.solver.dry_run import reconstruct_fetch_actions
from conda_lock.solver.graph_integrity import assert_no_orphaned_conda_packages
from conda_lock.solver.lockfile_heal import heal_locked_dependencies_from_cache
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

    # Forward-reachability check on the planned package set. The
    # policy, escape hatch, and the rationale for not
    # reverse-propagating categories all live in
    # ``conda_lock.solver.graph_integrity``.
    assert_no_orphaned_conda_packages(planned, platform)

    return planned


def solve_specs_for_arch(
    conda: PathLike,
    channels: Sequence[Channel],
    specs: list[str],
    platform: str,
) -> DryRunInstall:
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
        return reconstruct_fetch_actions(conda, platform, dryrun_install)
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
) -> DryRunInstall:
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

    # Heal lockfile entries with empty ``dependencies`` from the local
    # cache before ``fake_conda_environment`` and ``to_fetch_action()``
    # carry the empty deps forward. Without this step, a lockfile
    # generated against a corrupt mamba 2.1.1-2.3.3 cache stays corrupt
    # across every subsequent ``conda-lock lock --update`` even after
    # the user upgrades to mamba 2.6.0+. See conda/conda-lock#896.
    #
    # The heal layer answers narrow per-entry questions; this function
    # owns the user-facing log policy.
    heal_report = heal_locked_dependencies_from_cache(
        locked, conda=conda, platform=platform
    )
    if heal_report.healed:
        logger.warning(
            "Healed %d lockfile entry/entries with empty 'dependencies' "
            "from the local package cache on %s: %s. The previous lockfile "
            "was likely generated against a corrupt cache from "
            "mamba/micromamba 2.1.1-2.3.3 (see conda/conda-lock#896 / "
            "mamba-org/mamba#4110); the new lockfile will be correct.",
            len(heal_report.healed),
            platform,
            sorted(heal_report.healed),
        )
    if heal_report.ambiguous:
        # Ambiguous entries have no cache evidence either way: legit-empty
        # leaves (``tzdata``, ``_libgcc_mutex``, ``python_abi``, ...) and
        # corrupt-empty entries are indistinguishable here. Healable-
        # elsewhere is not evidence about an ambiguous entry; partial
        # caches are normal.
        #
        # Two failure modes follow. ``assert_no_orphaned_conda_packages``
        # downstream catches the silent-vanishing variant (corrupt
        # entry's transitive deps become orphans). It does NOT catch
        # the "categorized corrupt-carrier" variant where the corrupt
        # entry is itself a requested or otherwise-reachable root and
        # its missing transitive deps are reachable via other paths;
        # the lockfile remains internally inconsistent and re-locks may
        # silently drift. The WARNING below is visibility for that
        # harder-to-detect case so an operator can regenerate from
        # sources.
        logger.warning(
            "On platform %s, %d lockfile entry/entries have empty "
            "'dependencies' and the local package cache has no "
            "info/index.json to confirm: %s. %d entry/entries on this "
            "platform were proven corrupt and healed in place. If you "
            "suspect a lockfile generated against a corrupt "
            "mamba/micromamba 2.1.1-2.3.3 cache (see "
            "conda/conda-lock#896 / mamba-org/mamba#4110) -- e.g. far "
            "more empty-deps entries than the legitimate handful "
            "(tzdata, python_abi, _libgcc_mutex, _openmp_mutex, "
            "nlohmann_json-abi) -- the safe repair is to regenerate "
            "the lockfile from sources on a known-clean cache "
            "(`mamba clean -a` then `conda-lock lock -f <your "
            "sources>`). Do NOT use a potentially-corrupt lockfile to "
            "populate a real environment: packages that already "
            "vanished during v1 serialization are unrecoverable from "
            "it. The orphan check below will hard-fail the "
            "silent-drop variant; this WARNING is visibility for the "
            "harder-to-detect categorized-carrier case where the "
            "corrupt entry is itself a requested or "
            "transitively-reachable root.",
            platform,
            len(heal_report.ambiguous),
            sorted(heal_report.ambiguous),
            len(heal_report.healed),
        )

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

        reconstructed = reconstruct_fetch_actions(conda, platform, dryrun_install)
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
