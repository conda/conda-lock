import os
import pathlib
import platform
import re
import shutil
import sys
import typing

from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

import docker
import filelock
import pytest
import requests

from docker.models.containers import Container
from ensureconda.resolve import platform_subdir

from conda_lock._vendor.poetry.locations import DEFAULT_CACHE_DIR
from conda_lock.invoke_conda import PathLike, _ensureconda


TESTS_DIR = Path(__file__).parent


@pytest.fixture(
    scope="session",
    params=[
        pytest.param("conda"),
        pytest.param("mamba"),
        pytest.param("micromamba"),
    ],
)
def _conda_exe_type(request: Any) -> str:
    "Internal fixture to iterate over"
    return request.param


@pytest.fixture(scope="session")
@typing.no_type_check
def conda_exe(_conda_exe_type: str) -> PathLike:
    kwargs = dict(
        mamba=False,
        micromamba=False,
        conda=False,
        conda_exe=False,
    )
    if platform.system().lower() == "windows":
        if _conda_exe_type == "micromamba":
            pytest.skip(reason="micromamba tests are failing on windows")

    kwargs[_conda_exe_type] = True
    _conda_exe = _ensureconda(**kwargs)

    if _conda_exe is not None:
        return _conda_exe
    pytest.skip(f"{_conda_exe_type} is not installed")


@pytest.fixture(scope="session")
def mamba_exe() -> pathlib.Path:
    """Provides a fixture for tests that require mamba"""
    kwargs = dict(
        mamba=True,
        micromamba=False,
        conda=False,
        conda_exe=False,
    )
    _conda_exe = _ensureconda(**kwargs)
    if _conda_exe is not None:
        return _conda_exe
    pytest.skip("mamba is not installed")


class QuetzServerInfo(NamedTuple):
    url: str
    user_name: str
    api_key: str


@pytest.fixture(scope="session")
def quetz_server() -> Iterable[QuetzServerInfo]:
    if not (
        platform_subdir().startswith("linux") or platform_subdir().startswith("osx")
    ):
        pytest.skip("Docker Quetz fixture only available on osx and linux platforms")

    if platform_subdir().startswith("osx") and ("GITHUB_ACTION" in os.environ):
        pytest.skip(
            "Docker Quetz fixture not avilable on osx running on github actions"
        )

    client = docker.from_env()

    image = client.images.pull("mambaorg/quetz:latest")

    container: Container = client.containers.run(
        image,
        command="quetz run --copy-conf /etc/config.toml --dev --host 0.0.0.0 /run/quetz",
        volumes={
            str(pathlib.Path(__file__).parent / "quetz" / "dev_config.toml"): {
                "bind": "/etc/config.toml",
                "mode": "ro",
            }
        },
        ports={"8000/tcp": None},
        detach=True,
        remove=True,
    )

    try:
        logstream = container.logs(stdout=True, stderr=False, stream=True, follow=True)
        for line in logstream:
            line = line.strip()
            if isinstance(line, bytes):
                line = line.decode()
            match = re.match(r'Test API key created for user "(.*)"\: (.*)', line)
            if match:
                user_name = match.group(1)
                api_key = match.group(2)
                break
        else:
            raise RuntimeError("No user found")

        logstream = container.logs(stdout=False, stderr=True, stream=True, follow=True)
        for line in logstream:
            line = line.strip()
            if isinstance(line, bytes):
                line = line.decode()
            match = re.match(".*Uvicorn running on.*", line)
            if match:
                break
        else:
            raise RuntimeError("No user found")

        container.reload()
        print(container.ports)
        port = container.ports["8000/tcp"]

        ip = port[0]["HostIp"]
        if ip == "0.0.0.0":
            ip = "localhost"
        quetz_url = f"http://{ip}:{port[0]['HostPort']}"

        print(quetz_url)
        # Create a private channel that is a conda-forge proxy
        response = requests.post(
            f"{quetz_url}/api/channels",
            headers={
                "X-API-Key": api_key,
                "accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "name": "proxy-channel",
                "private": True,
                "mirror_channel_url": "https://conda-static.anaconda.org/conda-forge",
                "mirror_mode": "proxy",
            },
        )
        print(response.json())
        response.raise_for_status()

        yield QuetzServerInfo(quetz_url, user_name, api_key)
    finally:
        container.stop()


def test_quetz(quetz_server: QuetzServerInfo) -> None:
    channel = requests.get(
        f"{quetz_server.url}/api/channels/proxy-channel",
        headers={"X-API-Key": quetz_server.api_key},
    ).json()
    assert (
        channel["mirror_channel_url"] == "https://conda-static.anaconda.org/conda-forge"
    )


@pytest.fixture()
def install_lock():
    """Limit concurrent install operations."""
    with filelock.FileLock(str(TESTS_DIR.joinpath("install.lock"))):
        yield


@pytest.fixture()
def cleared_poetry_cache(tmp_path_factory, testrun_uid: str):
    """Ensure no concurrency for tests that rely on the cache being cleared"""
    # testrun_uid comes from xdist <https://stackoverflow.com/a/62765653>
    # The idea for using FileLock with the base temp directory comes from
    # <https://pytest-xdist.readthedocs.io/en/latest/how-to.html#making-session-scoped-fixtures-execute-only-once>
    root_tmp_dir = tmp_path_factory.getbasetemp().parent
    testrun_lockfile = root_tmp_dir / f".conda_lock_pytest_{testrun_uid}.lock"
    with filelock.FileLock(testrun_lockfile):
        # Use `pytest -s` to see these messages
        print(
            f"Clearing {DEFAULT_CACHE_DIR} based on lock {testrun_lockfile}",
            file=sys.stderr,
        )
        clear_poetry_cache()
        yield
        print(f"Releasing lock {testrun_lockfile}", file=sys.stderr)


def clear_poetry_cache() -> None:
    # We are going to rmtree the cache directory. Let's be extra careful to make
    # sure we only delete a directory named "pypoetry-conda-lock" or one of its
    # subdirectories.
    to_delete = DEFAULT_CACHE_DIR.resolve()
    assert to_delete.name == "pypoetry-conda-lock" or (
        to_delete.parent.name == "pypoetry-conda-lock" and to_delete.name == "Cache"
    )
    # Do another independent check that triggers even if we're in optimized mode
    if "pypoetry-conda-lock" in to_delete.parts:
        shutil.rmtree(DEFAULT_CACHE_DIR, ignore_errors=True)
    else:
        raise RuntimeError(f"Refusing to delete {to_delete} as it does not look right")
