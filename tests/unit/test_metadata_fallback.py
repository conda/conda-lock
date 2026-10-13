"""Exercise the explicit skip fallback through update and cache reconstruction."""

import json
import subprocess

from copy import deepcopy
from pathlib import Path

import pytest

from conda_lock import conda_solver
from conda_lock.errors import MetadataConsistencyError
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency
from conda_lock.models.lock_spec import VersionedDependency
from conda_lock.solver import channel_metadata, dry_run
from tests.support.fixtures import MAMBA_26_LINK_ACTION


@pytest.fixture
def skip_verification(monkeypatch):
    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", "skip")
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: None)
    monkeypatch.setattr(
        channel_metadata, "_supports_conda_repoquery", lambda *args: False
    )


def make_locked(dependencies):
    return {
        name: LockedDependency(
            name=name,
            version="1.0",
            manager="conda",
            platform="linux-64",
            dependencies=deps,
            categories={"main"},
            hash=HashModel(md5="0" * 32),
            url=f"https://example.org/c/linux-64/{name}-1.0-0.conda",
        )
        for name, deps in dependencies.items()
    }


def test_skip_update_preserves_metadata_never_prunes_and_honors_unlink(
    skip_verification, monkeypatch, caplog
):
    locked = make_locked(
        {"root": {"leaf": ">=1"}, "leaf": {}, "orphan": {}, "removed": {}}
    )
    before = deepcopy(locked)
    installed = {name: dep.to_fetch_action() for name, dep in locked.items()}
    prefixes = []

    def read_prefix(conda, platform, prefix):
        records = {
            p["name"]: p
            for path in (Path(prefix) / "conda-meta").glob("*.json")
            for p in [json.loads(path.read_text())]
        }
        prefixes.append(records)
        assert set(records) == set(locked)  # The pre-update prune must not run.
        for name, record in records.items():
            assert record["depends"] == installed[name]["depends"]
            assert record["url"] == locked[name].url
            assert record["md5"] == locked[name].hash.md5
        return installed

    monkeypatch.setattr(conda_solver, "_get_installed_conda_packages", read_prefix)
    updated = {
        **installed["root"],
        "version": "2.0",
        "depends": [],
        "fn": "root-2.0-0.conda",
        "url": "https://example.org/c/linux-64/root-2.0-0.conda",
    }

    def solve(command, **kwargs):
        assert command[1] == "install"  # No metadata queries in skip mode.
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "actions": {
                        "LINK": [updated],
                        "FETCH": [updated],
                        "UNLINK": [installed["removed"]],
                    }
                }
            ),
        )

    monkeypatch.setattr(conda_solver.subprocess, "run", solve)
    plan = conda_solver.update_specs_for_arch(
        conda="conda",
        specs=["root"],
        locked=locked,
        update=["root"],
        platform="linux-64",
        channels=[],
    )
    assert len(prefixes) == 1
    records = {p["name"]: p for p in plan["actions"]["FETCH"]}
    assert set(records) == {"root", "leaf", "orphan"}  # No post-update prune either.
    assert records["root"]["depends"] == []
    assert records["leaf"] == installed["leaf"]
    assert locked == before
    assert "skipping channel verification" in caplog.text
    assert "Refreshed dependency metadata" not in caplog.text


@pytest.mark.parametrize(
    "dependencies, message",
    [
        ({"root": {}, "orphan": {}}, "have no category"),
        ({"root": {"missing": ""}}, "missing required packages"),
        ({"root": {"leaf": ">=2"}, "leaf": {}}, "does not satisfy"),
    ],
)
def test_skip_keeps_integrity_guards(
    skip_verification, monkeypatch, dependencies, message
):
    locked = make_locked(dependencies)
    installed = {name: dep.to_fetch_action() for name, dep in locked.items()}
    monkeypatch.setattr(
        conda_solver, "_get_installed_conda_packages", lambda *args: installed
    )
    monkeypatch.setattr(
        conda_solver.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, '{"actions": {}}'
        ),
    )
    with pytest.raises(MetadataConsistencyError, match=message):
        conda_solver.solve_conda(
            conda="conda",
            specs={"root": VersionedDependency(name="root", version="*")},
            locked=locked,
            update=["root"],
            platform="linux-64",
            channels=[],
            mapping_url="unused",
        )


@pytest.mark.parametrize("damage", ["erased-deps", "wrong-hash", "null-deps"])
def test_skip_sparse_cache_reconstruction_keeps_identity_checks_and_warns(
    skip_verification, monkeypatch, tmp_path, caplog, damage
):
    record = {**MAMBA_26_LINK_ACTION, "depends": [], "timestamp": 0, "license": ""}
    sparse = {
        k: v for k, v in record.items() if k not in {"depends", "timestamp", "license"}
    }
    if damage == "wrong-hash":
        record["md5"] = record["sha256"] = "wrong"
    elif damage == "null-deps":
        record["depends"] = None
    cache = tmp_path / "pkgs"
    info = cache / MAMBA_26_LINK_ACTION["fn"].removesuffix(".conda") / "info"
    info.mkdir(parents=True)
    (info / "repodata_record.json").write_text(json.dumps(record))
    monkeypatch.setattr(dry_run, "get_pkgs_dirs", lambda **kwargs: [cache])
    monkeypatch.setattr(
        channel_metadata.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Skip must not query channels"),
    )
    plan = {"actions": {"LINK": [sparse], "FETCH": []}}
    if damage == "erased-deps":
        dry_run.reconstruct_fetch_actions_in_place("conda", "linux-64", plan)
        assert plan["actions"]["FETCH"] == [record]
        assert "skipping channel verification" in caplog.text
        assert "timestamp=0 and an empty license" in caplog.text
    else:
        with pytest.raises(MetadataConsistencyError, match="No valid cache record"):
            dry_run.reconstruct_fetch_actions_in_place("conda", "linux-64", plan)
