import copy
import json
import subprocess

import pytest

from packaging.version import Version

from conda_lock.errors import MetadataConsistencyError
from conda_lock.solver import channel_metadata
from tests.support.fixtures import MAMBA_26_LINK_ACTION


@pytest.mark.parametrize("version", [None, Version("2.3.2"), Version("2.9.0")])
@pytest.mark.parametrize("offline", [False, True])
def test_query_uses_channel_api_and_accepts_empty_dependencies(
    monkeypatch, version, offline
):
    monkeypatch.setenv("CONDA_FLAGS", "--offline" if offline else "")
    monkeypatch.setenv("MAMBA_ADD_PIP_AS_PYTHON_DEPENDENCY", "True")
    record = {
        **MAMBA_26_LINK_ACTION,
        "depends": None if version == Version("2.3.2") else [],
    }
    response = {"result": {"pkgs": [record]}} if version else {record["name"]: [record]}
    commands = []
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: version)

    def run(command, **kwargs):
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
    assert result[0]["depends"] == []
    assert commands[0][1:3] == (
        ["repoquery", "search"] if version else ["search", "^libzlib$"]
    )
    assert (
        commands[0][commands[0].index("--channel") + 1]
        == "https://conda.anaconda.org/conda-forge"
    )
    assert ("--offline" in commands[0]) is offline


def test_channel_dependencies_cannot_be_replaced_by_archive_dependencies(monkeypatch):
    trusted = dict(MAMBA_26_LINK_ACTION)
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args: [trusted]
    )
    damaged = {**trusted, "depends": []}
    original = copy.deepcopy(damaged)
    refreshed = channel_metadata.verified_channel_records(
        "/solver", "linux-64", [damaged]
    )
    assert refreshed[trusted["name"]] == trusted
    assert damaged == original


def test_missing_channel_artifact_is_unverifiable_not_proven_corrupt(monkeypatch):
    monkeypatch.setattr(channel_metadata, "query_channel_records", lambda *args: [])
    with pytest.raises(MetadataConsistencyError, match="is unavailable"):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [dict(MAMBA_26_LINK_ACTION)]
        )


def test_refresh_requires_a_matching_digest_not_just_the_same_url(monkeypatch):
    expected = {**MAMBA_26_LINK_ACTION, "sha256": None}
    source = {**MAMBA_26_LINK_ACTION, "md5": None}
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args: [source]
    )
    with pytest.raises(MetadataConsistencyError, match="identity cannot be verified"):
        channel_metadata.verified_channel_records("/solver", "linux-64", [expected])


def test_remote_verification_respects_offline_from_configuration(monkeypatch):
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(
        channel_metadata, "mamba_binary_version", lambda _: Version("2.9")
    )

    def run(command, **kwargs):
        assert command[1:3] == ["config", "list"]
        return subprocess.CompletedProcess(command, 0, '{"offline": true}')

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    with pytest.raises(MetadataConsistencyError, match="Offline Mamba"):
        channel_metadata.query_channel_records(
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION]
        )


@pytest.mark.parametrize("version", [None, Version("2.7.0"), Version("2.8.0")])
def test_channel_query_does_not_normalize_null_after_upstream_fix(monkeypatch, version):
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: version)
    record = {**MAMBA_26_LINK_ACTION, "depends": None}
    response = {"result": {"pkgs": [record]}} if version else {record["name"]: [record]}

    def run(command, **kwargs):
        if command[1:3] == ["config", "list"]:
            return subprocess.CompletedProcess(command, 0, '{"offline": false}')
        return subprocess.CompletedProcess(command, 0, json.dumps(response))

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    if version == Version("2.7.0"):
        verified = channel_metadata.verified_channel_records(
            "/solver", "linux-64", [record]
        )
        assert verified[MAMBA_26_LINK_ACTION["name"]]["depends"] == []
    else:
        with pytest.raises(
            MetadataConsistencyError, match="identity cannot be verified"
        ):
            channel_metadata.verified_channel_records("/solver", "linux-64", [record])

