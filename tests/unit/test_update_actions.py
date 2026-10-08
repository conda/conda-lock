import json
import subprocess

from copy import deepcopy

import pytest

from conda_lock import conda_solver
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency
from conda_lock.models.channel import Channel
from conda_lock.models.dry_run_install import FetchAction


@pytest.mark.parametrize("removal_only", [False, True])
def test_update_does_not_resurrect_unlinked_packages(monkeypatch, removal_only):
    locked = {
        name: LockedDependency(
            name=name,
            version="1.0",
            manager="conda",
            platform="linux-64",
            dependencies={},
            url=f"https://example.org/c/linux-64/{name}-1.0-0.conda",
            hash=HashModel(md5="0" * 32),
            categories={"main"},
        )
        for name in ["root", "removed", "unchanged"]
    }
    monkeypatch.setattr(
        "conda_lock.solver.channel_metadata.query_channel_records",
        lambda *args: [dep.to_fetch_action() for dep in locked.values()],
    )
    original = deepcopy(locked)
    installed = {name: dep.to_fetch_action() for name, dep in locked.items()}
    monkeypatch.setattr(
        conda_solver, "_get_installed_conda_packages", lambda *args: installed
    )
    actions = {"UNLINK": [installed["removed"]]}
    if not removal_only:
        updated: FetchAction = {
            **installed["root"],
            "version": "2.0",
            "fn": "root-2.0-0.conda",
            "url": "https://example.org/c/linux-64/root-2.0-0.conda",
        }
        actions.update(LINK=[updated], FETCH=[updated])
    monkeypatch.setattr(
        conda_solver.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, json.dumps({"actions": actions})
        ),
    )
    result = conda_solver.update_specs_for_arch(
        conda="micromamba",
        specs=["root"],
        locked=locked,
        update=["root"],
        platform="linux-64",
        channels=[Channel.from_string("https://example.org/c")],
    )
    assert {p["name"] for p in result["actions"]["FETCH"]} == {"root", "unchanged"}
    assert {p["name"] for p in result["actions"]["LINK"]} == {"root", "unchanged"}
    assert locked == original


@pytest.mark.parametrize("old_dependencies", [{}, {"leaf": "<3"}])
def test_update_refreshes_metadata_before_solving_without_mutating_old_lock(
    monkeypatch, old_dependencies
):
    locked = {
        name: LockedDependency(
            name=name,
            version="1.0",
            manager="conda",
            platform="linux-64",
            dependencies=old_dependencies if name == "root" else {},
            url=f"https://example.org/c/linux-64/{name}-1.0-0.conda",
            hash=HashModel(md5="0" * 32),
            categories={"dev"},
        )
        for name in ["root", "leaf"]
    }
    original = deepcopy(locked)
    records = {name: dep.to_fetch_action() for name, dep in locked.items()}
    records["root"]["depends"] = ["leaf >=1", "leaf <3"]
    records["root"]["constrains"] = ["optional <2"]
    monkeypatch.setattr(
        "conda_lock.solver.channel_metadata.query_channel_records",
        lambda *args: list(records.values()),
    )
    inspected = []

    def installed(conda, platform, prefix):
        from pathlib import Path

        by_name = {}
        for path in (Path(prefix) / "conda-meta").glob("*.json"):
            record = json.loads(path.read_text())
            if record["name"] == "root":
                assert record["depends"] == ["leaf >=1", "leaf <3"]
                assert record["constrains"] == ["optional <2"]
                assert "sha256" not in record
                inspected.append(True)
            by_name[record["name"]] = {
                key: value
                for key, value in record.items()
                if key not in ("depends", "constrains")
            }
        return by_name

    monkeypatch.setattr(conda_solver, "_get_installed_conda_packages", installed)
    monkeypatch.setattr(
        conda_solver.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, '{"actions": {}}'),
    )
    result = conda_solver.update_specs_for_arch(
        conda="micromamba",
        specs=["root"],
        locked=locked,
        update=["root"],
        platform="linux-64",
        channels=[Channel.from_string("https://example.org/c")],
    )
    root = next(p for p in result["actions"]["FETCH"] if p["name"] == "root")
    assert root["depends"] == ["leaf >=1", "leaf <3"]
    assert conda_solver._dependency_versions(root["depends"] or []) == {
        "leaf": "<3,>=1"
    }
    assert inspected == [True]
    assert locked == original
