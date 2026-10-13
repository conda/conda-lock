import json
import subprocess

import pytest

from packaging.version import Version

from conda_lock.errors import MetadataConsistencyError
from conda_lock.solver import channel_metadata
from tests.support.fixtures import MAMBA_26_LINK_ACTION


MULTI_NAME_HELP = "usage: conda repoquery search [--no-installed] specs [specs ...]"


@pytest.mark.parametrize("version", [None, Version("2.3.2"), Version("2.9.0")])
@pytest.mark.parametrize("offline", [False, True])
def test_query_uses_solver_search_and_accepts_empty_dependencies(
    monkeypatch, version, offline
):
    monkeypatch.setenv("CONDA_FLAGS", "--offline" if offline else "")
    monkeypatch.setenv("MAMBA_ADD_PIP_AS_PYTHON_DEPENDENCY", "True")
    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", "unbatched")
    record = {
        **MAMBA_26_LINK_ACTION,
        "depends": None if version == Version("2.3.2") else [],
    }
    response = {"result": {"pkgs": [record]}} if version else {record["name"]: [record]}
    commands = []
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: version)

    def run(command, **kwargs):
        if command[-1] == "--help":
            return subprocess.CompletedProcess(command, 2, "", "no repoquery plugin")
        if command[1:3] == ["config", "list"]:
            return subprocess.CompletedProcess(command, 0, '{"offline": false}')
        commands.append(command)
        assert kwargs["env"]["MAMBA_ADD_PIP_AS_PYTHON_DEPENDENCY"] == "False"
        return subprocess.CompletedProcess(command, 0, json.dumps(response))

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    if version and offline:
        with pytest.raises(MetadataConsistencyError, match="Offline Mamba"):
            channel_metadata.query_channel_records("/solver", "linux-64", [record])
        assert not commands
        return
    result = channel_metadata.query_channel_records("/solver", "linux-64", [record])
    assert result is not None
    assert result[0]["depends"] == []
    assert commands[0][1:3] == (
        ["repoquery", "search"] if version else ["search", "libzlib"]
    )
    assert (
        commands[0][commands[0].index("--channel") + 1]
        == "https://conda.anaconda.org/conda-forge"
    )
    assert ("--offline" in commands[0]) is offline


@pytest.mark.parametrize(
    "status, response, message",
    [(1, "", "exited with status 1"), (0, "not JSON", "unexpected output")],
)
@pytest.mark.parametrize("fallback", ["error", "unbatched", "skip"])
def test_failed_repoquery_does_not_fall_back_to_other_metadata(
    monkeypatch, status, response, message, fallback
):
    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", fallback)
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: None)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert command[1:3] == ["repoquery", "search"]
        if command[-1] == "--help":
            return subprocess.CompletedProcess(command, 0, MULTI_NAME_HELP)
        return subprocess.CompletedProcess(
            command, status, response, "sensitive stderr"
        )

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    with pytest.raises(MetadataConsistencyError, match=message) as exc:
        channel_metadata.query_channel_records(
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION]
        )
    assert "fresh package cache" not in str(exc.value)
    assert "sensitive stderr" not in str(exc.value)
    assert len(commands) == 2


@pytest.mark.parametrize("fallback", ["error", "unbatched", "skip"])
def test_remote_verification_respects_offline_from_configuration(monkeypatch, fallback):
    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", fallback)
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(
        channel_metadata, "mamba_binary_version", lambda _: Version("2.9")
    )

    def run(command, **kwargs):
        if command[-1] == "--help":
            return subprocess.CompletedProcess(command, 2, "", "no repoquery plugin")
        assert command[1:3] == ["config", "list"]
        return subprocess.CompletedProcess(command, 0, '{"offline": true}')

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    with pytest.raises(MetadataConsistencyError, match="Offline Mamba") as exc:
        channel_metadata.query_channel_records(
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION]
        )
    message = str(exc.value)
    assert "is not supported" in message
    assert "config list offline --sources" in message
    assert "No channel query was attempted" in message
    assert "fresh package cache" not in message


@pytest.mark.parametrize(
    "help_text, status, supported",
    [
        (MULTI_NAME_HELP, 0, True),
        (MULTI_NAME_HELP.replace("specs [specs", "specs\n    [specs"), 0, True),
        ("usage: search [--no-installed] spec", 0, False),
        ("usage: search specs [specs ...]", 0, False),
        (MULTI_NAME_HELP, 2, False),
    ],
)
def test_conda_batch_capability_requires_multiple_specs(
    monkeypatch, help_text, status, supported
):
    monkeypatch.setattr(
        channel_metadata.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, status, help_text),
    )
    assert channel_metadata._supports_conda_repoquery("/solver", {}) is supported
