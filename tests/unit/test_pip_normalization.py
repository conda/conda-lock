import copy
import json
import subprocess

from pathlib import Path

import pytest

from conda_lock import conda_solver
from conda_lock.errors import MetadataConsistencyError
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency
from conda_lock.models.lock_spec import VersionedDependency


def records_for(dependencies):
    return {
        name: {
            "name": name,
            "version": "1.0",
            "depends": deps,
            "subdir": "linux-64",
            "fn": f"{name}-1.0-0.conda",
            "url": f"https://example.org/c/linux-64/{name}-1.0-0.conda",
            "md5": "0" * 32,
        }
        for name, deps in dependencies.items()
    }


@pytest.mark.parametrize("pip_root", ["pip", "app"])
def test_connected_pip_does_not_require_another_channel_query(monkeypatch, pip_root):
    records = records_for(
        {"python": [], "pip": ["python", "wheel"], "wheel": ["python"]}
    )
    if pip_root == "app":
        records.update(records_for({"app": ["pip"]}))
    monkeypatch.setenv("CONDA_FLAGS", "--offline")
    monkeypatch.setattr(
        conda_solver,
        "solve_specs_for_arch",
        lambda **kwargs: {"actions": {"FETCH": list(records.values())}},
    )

    def query(*args):
        pytest.fail("Connected pip must not trigger another channel query")

    monkeypatch.setattr(conda_solver, "verified_channel_records", query)
    planned = conda_solver.solve_conda(
        conda="mamba",
        specs={
            name: VersionedDependency(name=name, version="*", category=category)
            for name, category in {"python": "main", pip_root: "dev"}.items()
        },
        locked={},
        update=[],
        platform="linux-64",
        channels=[],
        mapping_url="unused",
    )
    assert planned["python"].categories == {"main"}
    assert planned["pip"].categories == planned["wheel"].categories == {"dev"}


@pytest.mark.parametrize(
    "requested, genuine_pip_dependency, extra, expected",
    [
        ({"python": "main"}, False, None, {"python": "main"}),
        (
            {"python": "main", "pip": "dev"},
            False,
            None,
            {"python": "main", "pip": "dev", "wheel": "dev"},
        ),
        (
            {"python": "main", "wheel": "tools"},
            False,
            None,
            {"python": "main", "wheel": "tools"},
        ),
        (
            {"python": "main", "app": "dev"},
            True,
            None,
            {"python": "main", "app": "dev", "pip": "dev", "wheel": "dev"},
        ),
        ({"python": "main"}, False, "unrelated", "have no category"),
        ({"python": "main"}, False, "missing", "missing required packages"),
    ],
)
def test_normalization_uses_verified_edges_and_preserves_categories(
    monkeypatch, requested, genuine_pip_dependency, extra, expected
):
    dependencies = {"python": [], "pip": ["python", "wheel"], "wheel": ["python"]}
    if genuine_pip_dependency:
        dependencies["app"] = ["pip"]
    if extra == "unrelated":
        dependencies["unrelated"] = []
    if extra == "missing":
        dependencies["python"] = ["absent"]
    canonical = records_for(dependencies)
    raw = copy.deepcopy(canonical)
    # Simulate a synthetic edge, or a lost genuine incoming edge to pip.
    if genuine_pip_dependency:
        raw["app"]["depends"] = []
    else:
        raw["python"]["depends"] = [*raw["python"]["depends"], "pip"]
    before = copy.deepcopy(raw)
    monkeypatch.setattr(
        conda_solver,
        "solve_specs_for_arch",
        lambda **kwargs: {"actions": {"FETCH": list(raw.values())}},
    )
    monkeypatch.setattr(
        "conda_lock.solver.channel_metadata.query_channel_records",
        lambda *args: list(canonical.values()),
    )

    def solve():
        return conda_solver.solve_conda(
            conda="mamba",
            specs={
                name: VersionedDependency(name=name, version="*", category=cat)
                for name, cat in requested.items()
            },
            locked={},
            update=[],
            platform="linux-64",
            channels=[],
            mapping_url="unused",
        )

    if isinstance(expected, str):
        with pytest.raises(MetadataConsistencyError, match=expected):
            solve()
    else:
        planned = solve()
        assert {
            name: next(iter(dep.categories)) for name, dep in planned.items()
        } == expected
        assert all(len(dep.categories) == 1 for dep in planned.values())
        assert planned["python"].dependencies == {}
        if genuine_pip_dependency:
            assert planned["app"].dependencies == {"pip": ""}
        assert {entry.name for dep in planned.values() for entry in dep.to_v1()} == set(
            expected
        )
    assert raw == before


def test_update_omits_old_injected_pip_before_prefix_creation_and_pinning(monkeypatch):
    canonical = records_for({"python": [], "pip": ["python", "wheel"], "wheel": []})
    locked = {
        name: LockedDependency(
            name=name,
            version="1.0",
            manager="conda",
            platform="linux-64",
            dependencies={"pip": ""}
            if name == "python"
            else dict.fromkeys(record["depends"], ""),
            url=record["url"],
            hash=HashModel(md5=record["md5"]),
            categories={"main"},
        )
        for name, record in canonical.items()
    }
    before = copy.deepcopy(locked)
    monkeypatch.setattr(
        "conda_lock.solver.channel_metadata.query_channel_records",
        lambda *args: list(canonical.values()),
    )
    seen = []

    def installed(conda, platform, prefix):
        entries = [
            json.loads(path.read_text())
            for path in (Path(prefix) / "conda-meta").glob("*.json")
        ]
        assert [entry["name"] for entry in entries] == ["python"]
        assert entries[0]["depends"] == []
        seen.append(prefix)
        return {entry["name"]: entry for entry in entries}

    def run(command, **kwargs):
        pinned = (Path(seen[0]) / "conda-meta/pinned").read_text()
        assert "pip" not in pinned and "wheel" not in pinned
        return subprocess.CompletedProcess(command, 0, '{"actions": {}}')

    monkeypatch.setattr(conda_solver, "_get_installed_conda_packages", installed)
    monkeypatch.setattr(conda_solver.subprocess, "run", run)
    plan = conda_solver.update_specs_for_arch(
        conda="mamba",
        specs=["python"],
        locked=locked,
        update=["python"],
        platform="linux-64",
        channels=[],
    )
    assert [record["name"] for record in plan["actions"]["FETCH"]] == ["python"]
    assert locked == before
