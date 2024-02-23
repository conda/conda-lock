import os
import pathlib
import platform
import re
import typing

from pathlib import Path
from typing import Any, Iterable, NamedTuple, NoReturn

import docker
import filelock
import pytest
import requests

from docker.models.containers import Container
from ensureconda.resolve import platform_subdir

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
