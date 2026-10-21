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

from pathlib import Path

import pytest

from conda_lock import conda_solver, invoke_conda
from conda_lock.conda_lock import (
    create_lockfile_from_spec,
    render_lockfile_for_platform,
)
from conda_lock.errors import MetadataConsistencyError
from conda_lock.lockfile import parse_conda_lock_file, write_conda_lock_file
from conda_lock.lockfile.v2prelim.models import UpdateSpecification
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import (
    Dependency,
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
        (root / subdir).mkdir(parents=True, exist_ok=True)
        data = json.dumps(
            dict(info=dict(subdir=subdir), packages=packages, **{"packages.conda": {}})
        )
        (root / subdir / "repodata.json").write_text(data)
        (root / subdir / "current_repodata.json").write_text(data)
    return records


@pytest.mark.parametrize("solver", SOLVERS)
def test_create_ignores_configured_default_packages(tmp_path, monkeypatch, solver):
    channel = tmp_path / "channel"
    make_channel(channel, [(name, "1.0", [], "linux-64") for name in ("root", "extra")])
    condarc = tmp_path / "condarc"
    condarc.write_text("channels: []\ncreate_default_packages: [extra]\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setenv("MAMBARC", str(condarc))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cache"))
    result = conda_solver.solve_conda(
        conda=solver,
        specs={"root": VersionedDependency(name="root", version="*")},
        locked={},
        update=[],
        platform="linux-64",
        channels=[Channel.from_string(channel.as_uri())],
        mapping_url="unused",
    )
    assert set(result) == {"root"}


@pytest.mark.parametrize("solver", SOLVERS)
def test_channel_qualified_source_is_verified_on_fresh_lock_and_update(
    tmp_path, monkeypatch, solver
):
    source = tmp_path / "source"
    make_channel(source)
    other = tmp_path / "other"
    make_channel(
        other, [("decoy", "1.0", [], "linux-64"), ("decoy-all", "1.0", [], "noarch")]
    )
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cache"))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    monkeypatch.setenv("CONDA_FLAGS", "")
    condarc = tmp_path / "condarc"
    condarc.write_text(f"channels: []\nchannel_alias: {tmp_path.as_uri()}\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setenv("MAMBARC", str(condarc))
    channels = [Channel.from_string(other.as_uri())]
    root_spec = VersionedDependency(
        name="root-main", version="1.0", conda_channel="source"
    )
    specs: dict[str, Dependency] = {"root-main": root_spec}

    def solve(locked, update):
        return conda_solver.solve_conda(
            conda=solver,
            specs=specs,
            locked=locked,
            update=update,
            platform="linux-64",
            channels=channels,
            mapping_url="unused",
        )

    locked = solve({}, [])
    assert set(locked) == {"root-main", "shared"}
    assert locked["root-main"].version == "1.0"
    root_spec.version = "2.0"
    updated = solve(locked, ["root-main"])
    assert set(updated) == {"root-main", "shared"}
    assert updated["root-main"].version == "2.0"
    assert updated["shared"] == locked["shared"]
    assert channels == [Channel.from_string(other.as_uri())]
    assert all(package.url.startswith(source.as_uri()) for package in updated.values())


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize(
    "removed",
    [
        "other-root",
        "update-target",
        "transitive",
        "all",
        "source-pin",
        "retained-source-pin",
        "changed-retained-source-pin",
        "no-replacement",
        "removed-source",
    ],
)
def test_update_replaces_unavailable_artifacts(
    tmp_path, monkeypatch, caplog, solver, removed
):
    channel = tmp_path / "channel"
    definitions = [
        ("analysis-tool", "1.0", ["shared >=1"], "noarch"),
        ("runtime", "1.0", [], "linux-64"),
        ("shared", "1.0", [], "linux-64"),
        ("unrelated", "1.0", [], "linux-64"),
    ]
    make_channel(channel, definitions)
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    condarc = tmp_path / "condarc"
    condarc.write_text("channels: []\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setenv("MAMBARC", str(condarc))
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cold-cache"))
    specs: dict[str, Dependency] = {
        name: VersionedDependency(name=name, version="*", category=category)
        for name, category in [
            ("analysis-tool", "dev"),
            ("runtime", "main"),
            ("unrelated", "main"),
        ]
    }

    def solve(locked, update):
        return conda_solver.solve_conda(
            conda=solver,
            specs=specs,
            locked=locked,
            update=update,
            platform="linux-64",
            channels=[Channel.from_string(channel.as_uri())],
            mapping_url="unused",
        )

    locked = solve({}, [])
    original = copy.deepcopy(locked)
    removed_names = {
        "other-root": {"analysis-tool"},
        "update-target": {"runtime"},
        "transitive": {"shared"},
        "all": {name for name, _, _, _ in definitions},
        "source-pin": {"analysis-tool"},
        "retained-source-pin": {"analysis-tool"},
        "changed-retained-source-pin": {"analysis-tool"},
        "no-replacement": {"analysis-tool"},
        "removed-source": {"analysis-tool"},
    }[removed]
    available = [
        definition for definition in definitions if definition[0] not in removed_names
    ]
    available.extend(
        (name, "2.0", depends, subdir)
        for name, _, depends, subdir in definitions
        if removed != "no-replacement" or name != "analysis-tool"
    )
    # Remove only repodata entries. Keeping the archives proves that an old URL
    # still being downloadable does not authorize retaining a delisted artifact.
    make_channel(channel, available)
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "update-cache"))
    if removed == "source-pin":
        specs["analysis-tool"] = VersionedDependency(
            name="analysis-tool", version="==1.0", category="dev"
        )
    if removed == "retained-source-pin":
        specs["unrelated"] = VersionedDependency(name="unrelated", version="==3.0")
    if removed == "changed-retained-source-pin":
        specs["unrelated"] = VersionedDependency(name="unrelated", version="==2.0")
    if removed == "removed-source":
        del specs["analysis-tool"]
    if removed in {"source-pin", "retained-source-pin", "no-replacement"}:
        with pytest.raises(RuntimeError, match="Replacements must satisfy") as exc:
            solve(locked, ["runtime"])
        assert ": None" not in str(exc.value)
        assert "Artifact replacement on" not in caplog.text
    else:
        result = solve(locked, ["runtime"])
        assert set(result) == (
            set(locked) - {"analysis-tool", "shared"}
            if removed == "removed-source"
            else set(locked)
        )
        for name in (removed_names & set(result)) | {"runtime"}:
            assert result[name].version == "2.0"
            assert result[name].url != locked[name].url
        if removed != "removed-source":
            assert result["analysis-tool"].dependencies == {"shared": ">=1"}
            assert (
                result["analysis-tool"].categories
                == result["shared"].categories
                == {"dev"}
            )
        assert (
            result["runtime"].categories == result["unrelated"].categories == {"main"}
        )
        if removed == "changed-retained-source-pin":
            assert result["unrelated"].version == "2.0"
        elif "unrelated" not in removed_names:
            assert result["unrelated"] == locked["unrelated"]
        for name in removed_names:
            replacement = (
                f"{name}-{result[name].version}-0.tar.bz2"
                if name in result
                else "not selected"
            )
            assert f"{name}-1.0-0.tar.bz2 -> {replacement}" in caplog.text
    assert locked == original


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
def test_update_removes_obsolete_transitive_packages(tmp_path, monkeypatch, solver):
    channel = tmp_path / "channel"
    make_channel(channel)
    for filename in ("repodata.json", "current_repodata.json"):
        path = channel / "linux-64" / filename
        data = json.loads(path.read_text())
        data["packages"]["root-main-1.0-0.tar.bz2"]["depends"] = ["shared", "dev-leaf"]
        data["packages"]["root-main-2.0-0.tar.bz2"]["depends"] = []
        path.write_text(json.dumps(data))
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cache"))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    condarc = tmp_path / "condarc"
    condarc.write_text("channels: []\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setenv("CONDA_FLAGS", "")
    specs: dict[str, Dependency] = {
        "root-main": VersionedDependency(name="root-main", version="==1.0"),
        "root-tool": VersionedDependency(
            name="root-tool", version="*", category="tools"
        ),
    }

    def solve(locked, update):
        return conda_solver.solve_conda(
            conda=solver,
            specs=specs,
            locked=locked,
            update=update,
            platform="linux-64",
            channels=[Channel.from_string(channel.as_uri())],
            mapping_url="unused",
        )

    original = solve({}, [])
    before = copy.deepcopy(original)
    specs["root-main"] = VersionedDependency(name="root-main", version="==2.0")
    updated = solve(original, ["root-main"])
    assert set(updated) == {"root-main", "root-tool", "shared", "tool-leaf"}
    assert updated["root-main"].version == "2.0"
    assert original["shared"].categories == {"main"}
    assert updated["shared"].categories == {"tools"}
    for name in ("root-tool", "shared", "tool-leaf"):
        assert updated[name].url == original[name].url
        assert updated[name].hash == original[name].hash
    assert original == before


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


@pytest.mark.parametrize("solver", SOLVERS)
def test_update_refreshes_a_legitimate_channel_patch(tmp_path, monkeypatch, solver):
    channel = tmp_path / "channel"
    records = make_channel(channel)
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    condarc = tmp_path / "condarc"
    condarc.write_text("channels: []\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cold-cache"))
    specs: dict[str, Dependency] = {
        name: VersionedDependency(name=name, version="==1.0", category="dev")
        for name in ["root-dev", "root-tool"]
    }

    def solve(locked, update):
        return conda_solver.solve_conda(
            conda=solver,
            specs=specs,
            locked=locked,
            update=update,
            platform="linux-64",
            channels=[Channel.from_string(channel.as_uri())],
            mapping_url="unused",
        )

    locked = solve({}, [])
    original = copy.deepcopy(locked)
    # Patch repodata only; the selected package archives and their hashes stay put.
    records["linux-64"]["root-dev-1.0-0.tar.bz2"]["depends"] = [
        "shared >=1,<2",
        "dev-leaf",
    ]
    for subdir, packages in records.items():
        data = json.dumps(
            dict(info=dict(subdir=subdir), packages=packages, **{"packages.conda": {}})
        )
        for filename in ["repodata.json", "current_repodata.json"]:
            (channel / subdir / filename).write_text(data)
    # Ensure the solver reads the patched channel instead of an earlier snapshot.
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "update-cache"))
    updated = solve(locked, ["root-tool"])
    assert locked == original
    assert updated["root-dev"].dependencies["shared"] == ">=1,<2"
    for name, package in updated.items():
        assert package.url == locked[name].url
        assert package.hash == locked[name].hash
        assert package.categories == {"dev"}
    assert set(updated) == set(locked)


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize("python_requires_pip", [False, True])
def test_channel_query_preserves_real_python_dependencies(
    tmp_path, monkeypatch, solver, python_requires_pip
):
    """Querying pip alongside Python must not fabricate or remove an edge."""
    from conda_lock.solver.channel_metadata import query_channel_records

    channel = tmp_path / "channel"
    records = {}
    for name in ["python", "pip"]:
        filename = f"{name}-3.0-0.tar.bz2"
        records[filename] = {
            "name": name,
            "version": "3.0",
            "build": "0",
            "build_number": 0,
            "depends": ["pip"] if name == "python" and python_requires_pip else [],
            "subdir": "linux-64",
            "md5": "0" * 32,
            "size": 1,
        }
    for subdir in ["linux-64", "noarch"]:
        directory = channel / subdir
        directory.mkdir(parents=True)
        data = json.dumps({"packages": records if subdir == "linux-64" else {}})
        for filename in ["repodata.json", "current_repodata.json"]:
            (directory / filename).write_text(data)
    monkeypatch.setenv("MAMBA_ADD_PIP_AS_PYTHON_DEPENDENCY", "True")
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cache"))
    queried = query_channel_records(
        solver,
        "linux-64",
        [
            {**record, "url": (channel / "linux-64" / filename).as_uri()}
            for filename, record in records.items()
        ],
        channels=[Channel.from_string(channel.as_uri())],
    )
    assert queried is not None
    python = next(record for record in queried if record["name"] == "python")
    assert python["depends"] == (["pip"] if python_requires_pip else [])
    assert os.environ["MAMBA_ADD_PIP_AS_PYTHON_DEPENDENCY"] == "True"


@pytest.mark.parametrize(
    "solver", [s for s in SOLVERS if Path(s).name in {"conda", "conda.exe"}]
)
@pytest.mark.parametrize("fallback", ["error", "unbatched", "skip"])
def test_update_without_repoquery(tmp_path, monkeypatch, caplog, solver, fallback):
    """Disable the real plugin, then exercise each fallback through an update."""
    channel = tmp_path / "channel"
    make_channel(channel)
    condarc = tmp_path / "condarc"
    condarc.write_text("channels: []\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cache"))
    monkeypatch.delenv("CONDA_LOCK_METADATA_FALLBACK", raising=False)
    specs: dict[str, Dependency] = {
        name: VersionedDependency(name=name, version="==1.0", category=category)
        for name, category in [
            ("root-dev", "main"),
            ("dev-leaf", "main"),
            ("root-tool", "dev"),
        ]
    }

    def solve(locked, update):
        return conda_solver.solve_conda(
            conda=solver,
            specs=specs,
            locked=locked,
            update=update,
            platform="linux-64",
            channels=[Channel.from_string(channel.as_uri())],
            mapping_url="unused",
        )

    locked = solve({}, [])
    assert locked["shared"].categories == {"main"}
    monkeypatch.setenv("CONDA_NO_PLUGINS", "true")
    monkeypatch.setenv("CONDA_SOLVER", "classic")
    # Erasing this list hides an edge without creating an orphan: root-tool
    # still reaches shared, and dev-leaf is explicitly requested.
    locked["root-dev"].dependencies = {}
    original = copy.deepcopy(locked)
    if fallback == "error":
        with pytest.raises(MetadataConsistencyError, match="cannot batch"):
            solve(locked, ["root-tool"])
    else:
        monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", fallback)
        updated = solve(locked, ["root-tool"])
        assert set(updated) == set(locked)
        assert locked == original
        for name in updated:
            assert updated[name].url == locked[name].url
            assert updated[name].hash == locked[name].hash
        if fallback == "unbatched":
            assert updated["root-dev"].dependencies == {
                "shared": ">=1",
                "dev-leaf": "",
            }
            assert updated["shared"].categories == {"main"}
            assert "individual searches" in caplog.text
        else:
            assert updated["root-dev"].dependencies == {}
            assert updated["shared"].categories == {"dev"}
            assert "skipping channel verification" in caplog.text


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize(
    "patch", ["new-dependency", "tighter-constraint", "source-conflict"]
)
def test_channel_patch_repairs_initial_update_request(
    tmp_path, monkeypatch, solver, patch
):
    channel = tmp_path / "channel"
    definitions = [
        (
            "analysis-tool",
            "1.0",
            [] if patch == "new-dependency" else ["runtime-lib >=1"],
            "noarch",
        ),
        ("reporting-tool", "1.0", [], "linux-64"),
        ("reporting-tool", "2.0", [], "linux-64"),
        ("runtime-lib", "1.0", [], "linux-64"),
        ("runtime-lib", "2.0", [], "linux-64"),
        ("unrelated", "1.0", [], "linux-64"),
        ("unrelated", "2.0", [], "linux-64"),
    ]
    records = make_channel(channel, definitions)
    rc = tmp_path / "condarc"
    rc.write_text("channels: []\n")
    monkeypatch.setenv("CONDARC", str(rc))
    monkeypatch.setenv("MAMBARC", str(rc))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cache"))

    def publish():
        for subdir, packages in records.items():
            for filename in ["repodata.json", "current_repodata.json"]:
                (channel / subdir / filename).write_text(
                    json.dumps(
                        {
                            "info": {"subdir": subdir},
                            "packages": packages,
                            "packages.conda": {},
                        }
                    )
                )

    delayed = {
        name: records["linux-64"].pop(f"{name}-2.0-0.tar.bz2")
        for name in ["reporting-tool", "runtime-lib", "unrelated"]
    }
    publish()
    specs: dict[str, Dependency] = {
        name: VersionedDependency(
            name=name, version="==1.0" if name == "analysis-tool" else "*"
        )
        for name in ["analysis-tool", "reporting-tool", "unrelated"]
    }
    if patch == "source-conflict":
        specs["runtime-lib"] = VersionedDependency(name="runtime-lib", version="==1.0")

    def solve(locked, update):
        return conda_solver.solve_conda(
            conda=solver,
            specs=specs,
            locked=locked,
            update=update,
            platform="linux-64",
            channels=[Channel.from_string(channel.as_uri())],
            mapping_url="unused",
        )

    old = solve({}, [])
    original = copy.deepcopy(old)
    records["noarch"]["analysis-tool-1.0-0.tar.bz2"]["depends"] = ["runtime-lib >=2"]
    records["linux-64"].update(
        {f"{name}-2.0-0.tar.bz2": record for name, record in delayed.items()}
    )
    publish()
    if patch == "source-conflict":
        with pytest.raises(RuntimeError):
            solve(old, ["reporting-tool"])
    else:
        result = solve(old, ["reporting-tool"])
        assert result["analysis-tool"].dependencies == {"runtime-lib": ">=2"}
        assert result["analysis-tool"].url == old["analysis-tool"].url
        assert result["analysis-tool"].hash == old["analysis-tool"].hash
        assert (
            result["reporting-tool"].version == result["runtime-lib"].version == "2.0"
        )
        assert result["unrelated"] == old["unrelated"]
    assert old == original


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize("operation", ["fresh", "update"])
def test_final_verification_observes_channel_patch_after_solving(
    tmp_path, monkeypatch, solver, operation
):
    channel = tmp_path / "channel"
    records = make_channel(
        channel,
        [
            ("analysis-tool", "1.0", [], "noarch"),
            ("analysis-tool", "2.0", [], "noarch"),
            ("test-tool", "1.0", ["shared"], "linux-64"),
            ("shared", "1.0", [], "linux-64"),
        ],
    )
    rc = tmp_path / "condarc"
    rc.write_text("channels: []\n")
    monkeypatch.setenv("CONDARC", str(rc))
    monkeypatch.setenv("MAMBARC", str(rc))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cache"))
    new = records["noarch"].pop("analysis-tool-2.0-0.tar.bz2")

    def publish():
        for filename in ["repodata.json", "current_repodata.json"]:
            (channel / "noarch" / filename).write_text(
                json.dumps(
                    {
                        "info": {"subdir": "noarch"},
                        "packages": records["noarch"],
                        "packages.conda": {},
                    }
                )
            )

    def solve(locked, update):
        return conda_solver.solve_conda(
            conda=solver,
            platform="linux-64",
            channels=[Channel.from_string(channel.as_uri())],
            locked=locked,
            update=update,
            mapping_url="unused",
            specs={
                name: VersionedDependency(name=name, version="*", category=cat)
                for name, cat in [("analysis-tool", "main"), ("test-tool", "dev")]
            },
        )

    publish()
    old = solve({}, []) if operation == "update" else {}
    if operation == "update":
        records["noarch"]["analysis-tool-2.0-0.tar.bz2"] = new
        publish()
    selected_version = "2.0" if old else "1.0"
    run = subprocess.run
    published = []

    def publish_after_solve(command, **kwargs):
        result = run(command, **kwargs)
        if "--dry-run" in command and result.returncode == 0:
            # Simulate a real channel publisher after the solver's metadata read;
            # do not modify or synthesize the solver's returned records.
            records["noarch"][f"analysis-tool-{selected_version}-0.tar.bz2"][
                "depends"
            ] = ["shared"]
            publish()
            published.append(True)
        return result

    monkeypatch.setattr(subprocess, "run", publish_after_solve)
    result = solve(old, ["analysis-tool"] if old else [])
    assert published == [True]
    assert result["analysis-tool"].version == selected_version
    assert result["analysis-tool"].dependencies == {"shared": ""}
    assert result["shared"].categories == {"main"}
    assert set(result) == {"analysis-tool", "test-tool", "shared"}
