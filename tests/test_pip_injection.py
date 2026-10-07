import json

from pathlib import Path

import pytest

from conda_lock import invoke_conda
from conda_lock.conda_solver import solve_conda
from conda_lock.invoke_conda import PathLike
from conda_lock.lookup import DEFAULT_MAPPING_URL
from conda_lock.models.channel import Channel
from conda_lock.models.lock_spec import VersionedDependency


@pytest.mark.parametrize(
    "requested, expected",
    [
        ({"python": ""}, {"python"}),
        ({"app": ""}, {"app", "python"}),
        ({"python": "", "pip": "==24.0"}, {"python", "pip"}),
        ({"pip-user": ""}, {"pip-user", "python", "pip"}),
    ],
    ids=["python-only", "transitive-python", "explicit-pip-pin", "pip-dependency"],
)
def test_pip_requires_a_request_or_dependency(
    conda_exe: PathLike,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested: dict[str, str],
    expected: set[str],
) -> None:
    channel = tmp_path / "channel"
    records = {}
    for name, version, depends in [
        ("python", "3.12.0", []),
        ("pip", "24.0", ["python >=3.12"]),
        ("pip", "25.0", ["python >=3.12"]),
        ("app", "1.0", ["python"]),
        ("pip-user", "1.0", ["pip"]),
    ]:
        records[f"{name}-{version}-0.tar.bz2"] = {
            "name": name,
            "version": version,
            "build": "0",
            "build_number": 0,
            "depends": depends,
            "subdir": "linux-64",
            "md5": "0" * 32,
            "size": 1,
        }
    for subdir in ["linux-64", "noarch"]:
        directory = channel / subdir
        directory.mkdir(parents=True)
        (directory / "repodata.json").write_text(
            json.dumps({"packages": records if subdir == "linux-64" else {}})
        )
    monkeypatch.setattr(invoke_conda, "CONDA_PKGS_DIRS", str(tmp_path / "pkgs"))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "root"))

    planned = solve_conda(
        conda=conda_exe,
        specs={
            name: VersionedDependency(
                name=name, version=version, manager="conda", category="dev"
            )
            for name, version in requested.items()
        },
        locked={},
        update=[],
        platform="linux-64",
        channels=[Channel.from_string(channel.as_uri())],
        mapping_url=DEFAULT_MAPPING_URL,
    )
    assert set(planned) == expected
    assert "pip" not in planned["python"].dependencies
    assert all(package.categories == {"dev"} for package in planned.values())
    if "pip" in requested:
        assert planned["pip"].version == "24.0"
