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
    "dependencies, requested, expected",
    [
        ({"python": []}, {"python": "main"}, {"python": {"main"}}),
        (
            {"python": [], "pip": ["python", "wheel"], "wheel": []},
            {"python": "main"},
            {"python": {"main"}, "pip": {"main"}, "wheel": {"main"}},
        ),
        (
            {"python": [], "pip": ["python", "wheel"], "wheel": []},
            {"python": "main", "wheel": "tools"},
            {"python": {"main"}, "pip": {"main"}, "wheel": {"main"}},
        ),
        (
            {"app": ["python"], "python": [], "pip": ["python", "wheel"], "wheel": []},
            {"app": "dev"},
            {"app": {"dev"}, "python": {"dev"}, "pip": {"dev"}, "wheel": {"dev"}},
        ),
        (
            {"app": ["python"], "tool": ["python"], "python": [], "pip": ["python"]},
            {"app": "dev", "tool": "tools"},
            {
                "app": {"dev"},
                "tool": {"tools"},
                "python": {"dev", "tools"},
                "pip": {"dev", "tools"},
            },
        ),
        (
            {"python": ["pip"], "pip": ["python", "wheel"], "wheel": []},
            {"python": "main", "pip": "dev"},
            {"python": {"main"}, "pip": {"main"}, "wheel": {"main"}},
        ),
        (
            {"python": [], "pip": ["python", "wheel", "__linux"], "wheel": ["pip"]},
            {"python": "main"},
            {"python": {"main"}, "pip": {"main"}, "wheel": {"main"}},
        ),
    ],
    ids=[
        "no-pip",
        "orphan-pip",
        "shared-wheel",
        "transitive-python",
        "multiple-categories",
        "returned-python-pip-edge",
        "cycle-and-virtual",
    ],
)
def test_solver_selection_and_edges_survive_pip_category_assignment(
    monkeypatch, dependencies, requested, expected
):
    records = records_for(dependencies)
    before = copy.deepcopy(records)
    monkeypatch.setenv("CONDA_FLAGS", "--offline")
    monkeypatch.setattr(
        conda_solver,
        "solve_specs_for_arch",
        lambda **kwargs: {"actions": {"FETCH": list(records.values())}},
    )
    monkeypatch.setattr(
        conda_solver,
        "verified_channel_records",
        lambda *args: pytest.fail("Pip must not cause a verification query"),
    )
    planned = conda_solver.solve_conda(
        conda="solver",
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
    assert set(planned) == set(records)
    assert {name: dep.categories for name, dep in planned.items()} == expected
    for name, package in planned.items():
        assert package.dependencies == dict.fromkeys(dependencies[name], "")
        assert package.url == records[name]["url"]
        assert package.hash.md5 == records[name]["md5"]
    assert {entry.name for dep in planned.values() for entry in dep.to_v1()} == set(
        records
    )
    assert records == before


@pytest.mark.parametrize(
    "dependencies, requested, error",
    [
        (
            {"python": [], "pip": ["python"], "unrelated": []},
            ["python"],
            "have no category",
        ),
        ({"root": [], "python": [], "pip": ["python"]}, ["root"], "have no category"),
        ({"root": [], "pip": []}, ["root"], "have no category"),
        (
            {"python": [], "pip": ["python"], "wheel": []},
            ["python", "pip"],
            "have no category",
        ),
        (
            {"python": [], "pip": ["python", "absent"]},
            ["python"],
            "missing required packages",
        ),
        ({"python": [], "pip": ["python >=2"]}, ["python"], "does not satisfy"),
    ],
    ids=[
        "unrelated-orphan",
        "unreachable-python",
        "no-python",
        "disconnected-wheel",
        "missing-pip-dependency",
        "unsatisfied-pip-dependency",
    ],
)
def test_pip_exception_does_not_relax_other_integrity_checks(
    monkeypatch, dependencies, requested, error
):
    records = records_for(dependencies)
    monkeypatch.setattr(
        conda_solver,
        "solve_specs_for_arch",
        lambda **kwargs: {"actions": {"FETCH": list(records.values())}},
    )
    monkeypatch.setattr(
        conda_solver,
        "verified_channel_records",
        lambda *args: pytest.fail("Pip must not cause a verification query"),
    )
    with pytest.raises(MetadataConsistencyError, match=error):
        conda_solver.solve_conda(
            conda="solver",
            specs={
                name: VersionedDependency(name=name, version="*") for name in requested
            },
            locked={},
            update=[],
            platform="linux-64",
            channels=[],
            mapping_url="unused",
        )


def test_update_retains_pip_component_in_prefix_pins_and_result(monkeypatch):
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
        assert {entry["name"] for entry in entries} == set(canonical)
        assert (
            next(entry for entry in entries if entry["name"] == "python")["depends"]
            == []
        )
        seen.append(prefix)
        return {entry["name"]: entry for entry in entries}

    def run(command, **kwargs):
        pinned = (Path(seen[0]) / "conda-meta/pinned").read_text()
        assert "pip ==1.0" in pinned and "wheel ==1.0" in pinned
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
    assert {record["name"] for record in plan["actions"]["FETCH"]} == set(canonical)
    assert locked == before
