import os
import pathlib
import re

from typing import Iterable, NamedTuple

import docker
import pytest
import requests

from docker.models.containers import Container
from ensureconda.resolve import platform_subdir


class QuetzServerInfo(NamedTuple):
    url: str
    user_name: str
    api_key: str


@pytest.fixture(scope="session")
def quetz_server() -> Iterable[QuetzServerInfo]:
    if not (
        platform_subdir().startswith("linux") or platform_subdir().startswith("osx")
    ):
        raise pytest.skip(
            "Docker Quetz fixture only available on osx and linux platforms"
        )

    if platform_subdir().startswith("osx") and ("GITHUB_ACTION" in os.environ):
        raise pytest.skip(
            "Docker Quetz fixture not avilable on osx running on github actions"
        )

    client = docker.from_env()

    image = client.images.pull("mambaorg/quetz")

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
