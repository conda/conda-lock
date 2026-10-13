"""Local-channel metadata matrix using actual solvers and installable archives.

Set CONDA_LOCK_METADATA_SOLVERS to comma-separated executable paths. The fixture
channel deliberately patches dependencies away from info/index.json. No public
channel, preexisting cache, or Docker daemon is needed.
"""

import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile

import pytest

from conda_lock import conda_solver, invoke_conda
from conda_lock.conda_lock import (
    create_lockfile_from_spec,
    render_lockfile_for_platform,
)
from conda_lock.lockfile import parse_conda_lock_file, write_conda_lock_file
from conda_lock.lockfile.v2prelim.models import UpdateSpecification
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import (
    LockSpecification,
    VersionedDependency,
)
from conda_lock.virtual_package import FakeRepoData, VirtualPackage


SOLVERS = os.environ.get("CONDA_LOCK_METADATA_SOLVERS", "").split(",")
pytestmark = pytest.mark.skipif(
    not SOLVERS[0], reason="set CONDA_LOCK_METADATA_SOLVERS for local-channel matrix"
)


def make_channel(root, definitions=None):
    records = {"linux-64": {}, "noarch": {}}
    definitions = (
        definitions
        if definitions is not None
        else [
            (
                "root-main",
                "1.0",
                ["shared >=1", "shared <3", "__glibc >=2.17"],
                "linux-64",
            ),
            ("root-main", "2.0", ["shared >=1", "__glibc >=2.31"], "linux-64"),
            ("root-dev", "1.0", ["shared >=1", "dev-leaf"], "linux-64"),
            ("root-tool", "1.0", ["shared >=1", "tool-leaf"], "noarch"),
            ("shared", "1.0", [], "linux-64"),
            ("dev-leaf", "1.0", [], "linux-64"),
            ("tool-leaf", "1.0", [], "noarch"),
        ]
    )
    for name, version, depends, subdir in definitions:
        directory = root / subdir
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{name}-{version}-0.tar.bz2"
        index = dict(
            name=name,
            version=version,
            build="0",
            build_number=0,
            subdir=subdir,
            depends=[],
            license="MIT",
            timestamp=1577854800000,
        )
        if subdir == "noarch":
            index["noarch"] = "generic"
        payload = f"share/{name}.txt"
        with tarfile.open(directory / filename, "w:bz2") as archive:
            for path, data in [
                ("info/index.json", json.dumps(index).encode()),
                ("info/files", (payload + "\n").encode()),
                (payload, version.encode()),
            ]:
                info = tarfile.TarInfo(path)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        content = (directory / filename).read_bytes()
        records[subdir][filename] = {
            **index,
            "depends": depends,
            "size": len(content),
            "md5": hashlib.md5(content).hexdigest(),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    for subdir, packages in records.items():
        data = json.dumps(
            dict(info=dict(subdir=subdir), packages=packages, **{"packages.conda": {}})
        )
        (root / subdir / "repodata.json").write_text(data)
        (root / subdir / "current_repodata.json").write_text(data)
    return records


def snapshot(lock):
    return {
        p.name: (
            p.version,
            p.url,
            p.hash.model_dump(),
            p.dependencies,
            sorted(p.categories),
        )
        for p in lock.package
        if not p.name.startswith("__")
    }


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize("glibc", ["2.28", "2.40"])
def test_cold_warm_contaminated_update_and_install(
    tmp_path, monkeypatch, solver, glibc
):
    channel = tmp_path / "channel"
    make_channel(channel)
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(cache))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    condarc = tmp_path / "condarc"
    condarc.write_text("channels: []\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setenv("CONDA_OVERRIDE_GLIBC", "99.0")  # #928 must replace this.
    captures = []
    normalize = conda_solver.reconstruct_fetch_actions_in_place

    def capture(conda, platform, dryrun, **kwargs):
        raw = copy.deepcopy(dryrun)
        (tmp_path / "latest-raw.json").write_text(json.dumps(raw, indent=2))
        normalize(conda, platform, dryrun, **kwargs)
        captures.append({"raw": raw, "normalized": copy.deepcopy(dryrun)})

    monkeypatch.setattr(conda_solver, "reconstruct_fetch_actions_in_place", capture)
    (tmp_path / "virtual").mkdir()
    vp = FakeRepoData(base_path=tmp_path / "virtual")
    for name, version in [("__glibc", glibc), ("__linux", "5.10"), ("__unix", "0")]:
        vp.add_package(VirtualPackage(name=name, version=version), ["linux-64"])
    vp.write()
    deps = [
        VersionedDependency(
            name=name,
            version="==1.0" if name == "root-main" else "*",
            category=category,
        )
        for name, category in [
            ("root-main", "main"),
            ("shared", "main"),
            ("root-dev", "dev"),
            ("root-tool", "tools"),
        ]
    ]
    spec = LockSpecification(
        dependencies={"linux-64": deps},
        channels=[Channel.from_string(channel.as_uri())],
        sources=[],
    )

    def solve(label, update=None):
        with vp:
            lock = create_lockfile_from_spec(
                conda=solver,
                spec=spec,
                lockfile_path=tmp_path / (label + ".yml"),
                virtual_package_repo=vp,
                update_spec=update,
                mapping_url="unused",
            )
        write_conda_lock_file(
            lock,
            tmp_path / (label + ".yml"),
            metadata_choices=set(),
            include_help_text=False,
        )
        parsed = parse_conda_lock_file(tmp_path / (label + ".yml"))
        assert snapshot(lock) == snapshot(parsed)
        return parsed

    cold = solve("cold")
    expected = {"root-main", "shared", "root-dev", "root-tool", "dev-leaf", "tool-leaf"}
    assert set(snapshot(cold)) == expected
    by_name = {p.name: p for p in cold.package}
    # Main dependencies are already installed for every category selection.
    assert by_name["shared"].categories == {"main"}
    assert by_name["dev-leaf"].categories == {"dev"}
    assert by_name["tool-leaf"].categories == {"tools"}
    assert by_name["root-main"].dependencies["shared"] == "<3,>=1"
    cold_update = solve(
        "cold-update", UpdateSpecification(locked=cold.package, update=["root-main"])
    )
    assert snapshot(cold_update) == snapshot(cold)
    # Warm by a channel solve, retaining the channel patches in the cache.
    with vp.conda_virtual_package_overrides("linux-64"):
        env = invoke_conda.conda_env_override("linux-64")
        proc = subprocess.run(
            [
                solver,
                "create",
                "-y",
                "-p",
                str(tmp_path / "warm-prefix"),
                "--override-channels",
                "-c",
                channel.as_uri(),
                "root-main=1.0",
                "root-dev",
                "root-tool",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    warm = solve("warm")
    assert snapshot(warm) == snapshot(cold)
    # Lost root-main -> shared edge is hidden because shared is also a main root.
    corrupt = copy.deepcopy(warm.package)
    next(p for p in corrupt if p.name == "root-main").dependencies = {}
    repaired = solve(
        "repaired-lock", UpdateSpecification(locked=corrupt, update=["root-dev"])
    )
    assert snapshot(repaired) == snapshot(warm)
    assert next(p for p in corrupt if p.name == "root-main").dependencies == {}
    # Old last-spec-wins serialization must be upgraded, not rejected as corruption.
    old_serialization = copy.deepcopy(warm.package)
    next(p for p in old_serialization if p.name == "root-main").dependencies[
        "shared"
    ] = "<3"
    upgraded = solve(
        "old-serialization",
        UpdateSpecification(locked=old_serialization, update=["root-dev"]),
    )
    assert snapshot(upgraded) == snapshot(warm)
    # Removing a source root during an update must drop its exclusive closure,
    # while preserving shared dependencies and the caller's original lock.
    before_removal = snapshot(warm)
    spec.dependencies["linux-64"] = [
        dep for dep in deps if dep.name not in {"root-dev", "shared"}
    ]
    removed = solve(
        "removed-root", UpdateSpecification(locked=warm.package, update=["root-main"])
    )
    assert set(snapshot(removed)) == {"root-main", "shared", "root-tool", "tool-leaf"}
    assert next(p for p in removed.package if p.name == "shared").categories == {"main"}
    assert all(
        snapshot(removed)[name] == before_removal[name] for name in snapshot(removed)
    )
    assert snapshot(warm) == before_removal
    spec.dependencies["linux-64"] = list(deps)
    # A refreshed edge reveals a missing package before solving. Reconsider its
    # dependents in the first solve, rather than accepting an incomplete prefix.
    missing_package = [copy.deepcopy(p) for p in warm.package if p.name != "dev-leaf"]
    next(p for p in missing_package if p.name == "root-dev").dependencies = {
        "shared": ">=1"
    }
    repaired_missing = solve(
        "missing-package",
        UpdateSpecification(locked=missing_package, update=["root-main"]),
    )
    assert snapshot(repaired_missing) == snapshot(warm)
    deps[0].version = "*"
    updated = solve(
        "updated", UpdateSpecification(locked=warm.package, update=["root-main"])
    )
    assert next(p for p in updated.package if p.name == "root-main").version == (
        "2.0" if glibc == "2.40" else "1.0"
    )
    # Every category's explicit install must match its serialized selection.
    for label, dev, extras in [
        ("main", False, set()),
        ("dev", True, set()),
        ("tools", False, {"tools"}),
    ]:
        selected = {"main"} | ({"dev"} if dev else set()) | extras
        expected_names = {p.name for p in updated.package if p.categories & selected}
        explicit = tmp_path / (label + ".lock")
        explicit.write_text(
            "\n".join(
                render_lockfile_for_platform(
                    lockfile=copy.deepcopy(updated),
                    include_dev_dependencies=dev,
                    extras=extras,
                    kind="explicit",
                    platform="linux-64",
                )
            )
            + "\n"
        )
        prefix = tmp_path / ("install-" + label)
        proc = subprocess.run(
            [
                solver,
                "create",
                "-y",
                "-p",
                str(prefix),
                "--file",
                str(explicit),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        installed = {
            json.loads(p.read_text())["name"]
            for p in (prefix / "conda-meta").glob("*.json")
        }
        assert installed == expected_names
        assert {p.stem for p in (prefix / "share").glob("*.txt")} == expected_names
        for package in updated.package:
            if package.name in expected_names:
                assert (
                    prefix / "share" / f"{package.name}.txt"
                ).read_text() == package.version
    # Contaminate the selected artifact's real cache record, preserving payloads.
    affected = 0
    for path in cache.rglob("repodata_record.json"):
        record = json.loads(path.read_text())
        if record["name"] == "root-main":
            record.update(depends=[], constrains=[], timestamp=0, license="")
            record.pop("sha256", None)
            path.write_text(json.dumps(record))
            affected += 1
    assert affected
    cache_update = solve(
        "bad-cache-update",
        UpdateSpecification(locked=warm.package, update=["root-dev"]),
    )
    assert snapshot(cache_update) == snapshot(warm)
    deps[0].version = "==1.0"
    # Rich LINK bypasses the damaged cache; sparse LINK refreshes from the channel.
    contaminated = solve("contaminated")
    assert snapshot(contaminated) == snapshot(cold)
    fresh = tmp_path / "recovery-cache"
    fresh.mkdir()
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(fresh))
    recovered = solve("recovered")
    assert snapshot(recovered) == snapshot(cold)
    (tmp_path / "solver-actions.json").write_text(json.dumps(captures, indent=2))
    assert os.environ["CONDA_OVERRIDE_GLIBC"] == "99.0"


@pytest.mark.parametrize("solver", SOLVERS)
def test_explicit_install_does_not_recover_channel_patches(
    tmp_path, monkeypatch, solver
):
    """Even a fixed solver can write as-built dependencies after an explicit install."""
    from conda_lock.solver.channel_metadata import verified_channel_records

    channel = tmp_path / "channel"
    records = make_channel(channel)
    filename = "root-main-1.0-0.tar.bz2"
    record = {
        **records["linux-64"][filename],
        "fn": filename,
        "url": (channel / "linux-64" / filename).as_uri(),
    }
    explicit = tmp_path / "explicit.lock"
    explicit.write_text("@EXPLICIT\n" + record["url"] + "#" + record["md5"] + "\n")
    cache = tmp_path / "cache"
    cache.mkdir()
    env = {
        **os.environ,
        "CONDA_PKGS_DIRS": str(cache),
        "MAMBA_ROOT_PREFIX": str(tmp_path / "root"),
    }
    proc = subprocess.run(
        [
            solver,
            "create",
            "-y",
            "-p",
            str(tmp_path / "prefix"),
            "--file",
            str(explicit),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    paths = list(cache.rglob("repodata_record.json"))
    assert len(paths) == 1
    installed_record = json.loads(paths[0].read_text())
    # Empty is correct for the archive, but differs from the solver's channel.
    assert not installed_record.get("depends")
    assert record["depends"]
    (tmp_path / "explicit-cache-record.json").write_text(
        json.dumps(installed_record, indent=2)
    )
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(cache))
    refreshed = verified_channel_records(
        solver,
        "linux-64",
        [installed_record],
        channels=[Channel.from_string(channel.as_uri())],
    )
    assert refreshed is not None
    assert refreshed[record["name"]]["depends"] == record["depends"]
    assert not installed_record.get("depends")
