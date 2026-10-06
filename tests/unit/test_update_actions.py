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
