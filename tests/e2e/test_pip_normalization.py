"""Offline pip policy checks, including locks made by a different solver version."""

import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import zipfile

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import zstandard

from conda_lock import conda_solver, invoke_conda
from conda_lock.conda_lock import render_lockfile_for_platform
from conda_lock.lockfile import parse_conda_lock_file, write_conda_lock_file
from conda_lock.lockfile.v2prelim.models import (
    HashModel,
    LockedDependency,
    Lockfile,
    LockMeta,
)
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import Dependency, VersionedDependency


SOLVERS = os.environ.get("CONDA_LOCK_METADATA_SOLVERS", "").split(",")
BASELINE = os.environ.get("CONDA_LOCK_METADATA_BASELINE_SOLVER", SOLVERS[0])
SWITCHES = list(
    dict.fromkeys(
        pair for solver in SOLVERS for pair in [(BASELINE, solver), (solver, BASELINE)]
    )
)
pytestmark = pytest.mark.skipif(
    not SOLVERS[0], reason="set CONDA_LOCK_METADATA_SOLVERS"
)


def tar_bytes(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


@pytest.fixture
def channel(tmp_path, monkeypatch):
    root = tmp_path / "channel"
    records = {"linux-64": {}, "noarch": {}}
    definitions = [
        ("python", "3.10.0", ["runtime"], "linux-64"),
        ("python", "3.12.0", ["runtime"], "linux-64"),
        ("runtime", "1.0", [], "linux-64"),
        ("pip", "24.0", ["python >=3.10,<3.12", "setuptools", "wheel"], "linux-64"),
        ("pip", "25.0", ["python >=3.12", "setuptools", "wheel"], "linux-64"),
        ("setuptools", "1.0", ["python"], "noarch"),
        ("wheel", "1!0.45.0", ["python"], "noarch"),
        ("app", "1.0", ["python"], "linux-64"),
        ("pip-user", "1.0", ["pip"], "linux-64"),
    ]
    for name, version, depends, subdir in definitions:
        directory = root / subdir
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{name}-{version}-0"
        record = dict(
            name=name,
            version=version,
            build="0",
            build_number=0,
            subdir=subdir,
            depends=[],
            timestamp=1577854800000,
            license="MIT",
        )
        if subdir == "noarch":
            record["noarch"] = "generic"
        payload = {f"share/{name}.txt": version.encode()}
        info = {
            "info/index.json": json.dumps(record).encode(),
            "info/files": (f"share/{name}.txt\n").encode(),
        }
        if name == "wheel":
            filename = stem + ".conda"
            with zipfile.ZipFile(directory / filename, "w") as archive:
                archive.writestr("metadata.json", '{"conda_pkg_format_version":2}')
                for label, files in [("info", info), ("pkg", payload)]:
                    archive.writestr(
                        f"{label}-{stem}.tar.zst",
                        zstandard.ZstdCompressor().compress(tar_bytes(files)),
                    )
        else:
            import bz2

            filename = stem + ".tar.bz2"
            (directory / filename).write_bytes(
                bz2.compress(tar_bytes({**info, **payload}))
            )
        content = (directory / filename).read_bytes()
        records[subdir][filename] = {
            **record,
            "depends": depends,
            "size": len(content),
            "md5": hashlib.md5(content).hexdigest(),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    for subdir, packages in records.items():
        for filename in ["repodata.json", "current_repodata.json"]:
            (root / subdir / filename).write_text(
                json.dumps(
                    {
                        "info": {"subdir": subdir},
                        "packages": {
                            fn: record
                            for fn, record in packages.items()
                            if fn.endswith(".tar.bz2")
                        },
                        "packages.conda": {
                            fn: record
                            for fn, record in packages.items()
                            if fn.endswith(".conda")
                        },
                    }
                )
            )
    condarc = tmp_path / "condarc.yaml"
    condarc.write_text("channels: []\n")
    monkeypatch.setenv("CONDARC", str(condarc))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "cache"))
    return root


def solve(
    solver, channel, requested, *, locked=None, update=(), python_version="==3.10.0"
):
    specs: dict[str, Dependency] = {
        name: VersionedDependency(
            name=name,
            version=python_version if name == "python" else "*",
            category=category,
        )
        for name, category in requested.items()
    }
    return conda_solver.solve_conda(
        conda=solver,
        specs=specs,
        locked=locked or {},
        update=list(update),
        platform="linux-64",
        channels=[Channel.from_string(channel.as_uri())],
        mapping_url="unused",
    )


def serialize(packages, channel, path):
    lock = Lockfile(
        package=list(copy.deepcopy(packages).values()),
        metadata=LockMeta(
            content_hash={"linux-64": "0" * 64},
            channels=[Channel.from_string(channel.as_uri())],
            platforms=["linux-64"],
            sources=[],
        ),
    )
    write_conda_lock_file(lock, path, metadata_choices=set(), include_help_text=False)
    parsed = parse_conda_lock_file(path)
    assert {p.name: p for p in parsed.package} == packages
    return parsed


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize(
    "requested, expected",
    [
        ({"python": "main"}, {"python": "main", "runtime": "main"}),
        ({"app": "dev"}, {"app": "dev", "python": "dev", "runtime": "dev"}),
        (
            {"python": "main", "pip": "dev"},
            {
                "python": "main",
                "runtime": "main",
                "pip": "dev",
                "setuptools": "dev",
                "wheel": "dev",
            },
        ),
        (
            {"python": "main", "wheel": "tools"},
            {"python": "main", "runtime": "main", "wheel": "tools"},
        ),
        (
            {"python": "main", "pip-user": "dev"},
            {
                "python": "main",
                "runtime": "main",
                "pip-user": "dev",
                "pip": "dev",
                "setuptools": "dev",
                "wheel": "dev",
            },
        ),
    ],
)
def test_pip_categories_survive_serialization_and_warm_cache(
    tmp_path, channel, solver, requested, expected
):
    planned = solve(solver, channel, requested)
    assert {name: dep.categories for name, dep in planned.items()} == {
        name: {cat} for name, cat in expected.items()
    }
    assert "pip" not in planned["python"].dependencies
    lock = serialize(planned, channel, tmp_path / "cold.yml")
    if "wheel" in planned:
        assert planned["wheel"].version == "1!0.45.0"
        assert planned["wheel"].url.endswith(".conda")
    # Main-only installs must omit dev-only pip; all-category installs warm every record.
    for label, dev, extras in [("main", False, set()), ("all", True, {"tools"})]:
        explicit = tmp_path / f"{label}.lock"
        explicit.write_text(
            "\n".join(
                render_lockfile_for_platform(
                    lockfile=copy.deepcopy(lock),
                    include_dev_dependencies=dev,
                    extras=extras,
                    kind="explicit",
                    platform="linux-64",
                )
            )
            + "\n"
        )
        prefix = tmp_path / label
        proc = subprocess.run(
            [solver, "create", "-y", "-p", str(prefix), "--file", str(explicit)],
            env=invoke_conda.conda_env_override("linux-64"),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        categories = {"main"} | ({"dev"} if dev else set()) | extras
        selected = {name for name, cat in expected.items() if cat in categories}
        assert {
            json.loads(path.read_text())["name"]
            for path in (prefix / "conda-meta").glob("*.json")
        } == selected
        assert {path.stem for path in (prefix / "share").glob("*.txt")} == selected
    warm = solve(solver, channel, requested)
    assert warm == planned
    updated = solve(
        solver, channel, requested, locked=warm, update=[next(iter(requested))]
    )
    assert updated == planned
    serialize(updated, channel, tmp_path / "updated.yml")


@pytest.mark.parametrize("solver", SOLVERS)
@pytest.mark.parametrize("pip_root", ["pip", "pip-user"])
def test_fresh_offline_lock_with_connected_pip(
    tmp_path, monkeypatch, channel, solver, pip_root
):
    """Warm an HTTP repodata cache, then lock without queries or network access."""
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            requests.append(self.path)

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Handler, directory=str(channel))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    specs: dict[str, Dependency] = {
        "python": VersionedDependency(name="python", version="==3.10.0"),
        pip_root: VersionedDependency(name=pip_root, version="*", category="dev"),
    }

    def lock():
        return conda_solver.solve_conda(
            conda=solver,
            specs=specs,
            locked={},
            update=[],
            platform="linux-64",
            channels=[Channel.from_string(url)],
            mapping_url="unused",
        )

    try:
        warm = lock()
        assert requests
        requests.clear()
        if pip_root == "pip":
            monkeypatch.setenv("CONDA_FLAGS", "--offline")
        else:
            (tmp_path / "condarc.yaml").write_text("channels: []\noffline: true\n")
        offline = lock()
        assert not requests
        assert offline == warm
        assert offline["python"].categories == {"main"}
        assert offline["pip"].categories == offline["wheel"].categories == {"dev"}
        serialize(offline, channel, tmp_path / "offline.yml")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("source_solver, destination_solver", SWITCHES)
def test_solver_switch_removes_old_injection_before_python_update(
    tmp_path, channel, source_solver, destination_solver
):
    # Capture an actual old-style plan with injection enabled, not a fabricated lock.
    env = {
        **invoke_conda.conda_env_override("linux-64"),
        "MAMBA_ADD_PIP_AS_PYTHON_DEPENDENCY": "True",
        "CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY": "True",
    }
    proc = subprocess.run(
        [
            source_solver,
            "create",
            "--dry-run",
            "--json",
            "-p",
            str(tmp_path / "old-prefix"),
            "--override-channels",
            "-c",
            channel.as_uri(),
            "python=3.10.0",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    raw = json.loads(invoke_conda.extract_json_object(proc.stdout))
    (tmp_path / "old-solver-plan.json").write_text(json.dumps(raw, indent=2))
    conda_solver.reconstruct_fetch_actions_in_place(source_solver, "linux-64", raw)
    old = {
        record["name"]: LockedDependency(
            name=record["name"],
            version=record["version"],
            manager="conda",
            platform="linux-64",
            dependencies=conda_solver._dependency_versions(record["depends"] or []),
            url=record["url"],
            hash=HashModel(md5=record["md5"], sha256=record.get("sha256")),
            categories={"main"},
        )
        for record in raw["actions"]["FETCH"]
    }
    assert old["pip"].version == "24.0"
    assert "<3.12" in old["pip"].dependencies["python"]
    before = copy.deepcopy(old)
    serialized = serialize(old, channel, tmp_path / "old.yml")
    updated = solve(
        destination_solver,
        channel,
        {"python": "main"},
        locked={p.name: p for p in serialized.package},
        update=["python"],
        python_version="==3.12.0",
    )
    assert set(updated) == {"python", "runtime"}
    assert updated["python"].version == "3.12.0"
    assert updated["runtime"].url == old["runtime"].url
    assert updated["runtime"].hash == old["runtime"].hash
    assert all(dep.categories == {"main"} for dep in updated.values())
    assert old == before
    serialize(updated, channel, tmp_path / "new.yml")
