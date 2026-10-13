import copy
import json
import subprocess

from pathlib import Path

import pytest

from packaging.version import Version

from conda_lock.errors import MetadataConsistencyError
from conda_lock.solver import channel_metadata
from tests.support.fixtures import MAMBA_26_LINK_ACTION


MULTI_NAME_HELP = "usage: conda repoquery search [--no-installed] specs [specs ...]"


@pytest.mark.parametrize("version", ["26.1.1", "26.7.2"])
def test_conda_repoquery_batches_names_without_installed_records(monkeypatch, version):
    # Captured from local-channel searches; only the channel path was replaced.
    fixture = (
        Path(__file__).parents[1]
        / "test-mamba-fixtures"
        / f"repoquery-conda-{version}.json"
    )
    response = fixture.read_text()
    records = json.loads(response)["result"]["pkgs"]
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: None)
    monkeypatch.setenv("CONDA_FLAGS", "")
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        if command[-1] == "--help":
            return subprocess.CompletedProcess(command, 0, MULTI_NAME_HELP)
        assert command[1:6] == [
            "repoquery",
            "search",
            "--no-installed",
            "root-dev",
            "root-main",
        ]
        assert kwargs["env"]["CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY"] == "False"
        return subprocess.CompletedProcess(command, 0, response)

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    result = channel_metadata.verified_channel_records("/solver", "linux-64", records)
    assert result == {record["name"]: record for record in records}
    assert len(seen) == 2  # Local capability probe, then one channel query.


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
    assert refreshed is not None
    assert refreshed[trusted["name"]] == trusted
    assert damaged == original


def test_verified_metadata_preserves_selected_epoch_url_spelling(monkeypatch):
    source = {
        **MAMBA_26_LINK_ACTION,
        "version": "1!1.3.2",
        "fn": "libzlib-1!1.3.2-h25fd6f3_2.conda",
        "url": "https://example.org/c/linux-64/libzlib-1!1.3.2-h25fd6f3_2.conda",
    }
    expected = {**source, "url": source["url"].replace("!", "%21"), "depends": []}
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args: [source]
    )
    result = channel_metadata.verified_channel_records(
        "/solver", "linux-64", [expected]
    )
    assert result is not None
    assert result["libzlib"]["url"] == expected["url"]
    assert result["libzlib"]["depends"] == source["depends"]


def test_missing_channel_artifact_is_unverifiable_not_proven_corrupt(monkeypatch):
    monkeypatch.setattr(channel_metadata, "query_channel_records", lambda *args: [])
    with pytest.raises(MetadataConsistencyError, match="is unavailable"):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [dict(MAMBA_26_LINK_ACTION)]
        )


def test_update_refresh_can_omit_an_unavailable_artifact(monkeypatch):
    expected = dict(MAMBA_26_LINK_ACTION)
    replacement = {**expected, "url": expected["url"] + ".different", "depends": []}
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args: [replacement]
    )
    assert (
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [expected], allow_missing=True
        )
        == {}
    )
    # A different artifact's metadata must never be attached to the old artifact.
    with pytest.raises(MetadataConsistencyError, match="is unavailable"):
        channel_metadata.verified_channel_records("/solver", "linux-64", [expected])


@pytest.mark.parametrize("damage", ["no-url", "hash", "shape", "conflict"])
def test_unavailable_artifact_policy_does_not_hide_invalid_records(monkeypatch, damage):
    expected = dict(MAMBA_26_LINK_ACTION)
    candidate = copy.deepcopy(expected)
    if damage == "no-url":
        candidate.pop("url")
    elif damage == "hash":
        candidate.update(md5="bad", sha256="bad")
    elif damage == "shape":
        candidate["depends"] = None
    else:
        candidate["depends"] = []
    queried = [expected, candidate] if damage == "conflict" else [candidate]
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args: queried
    )
    with pytest.raises(MetadataConsistencyError):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [expected], allow_missing=True
        )


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


def test_refresh_requires_a_matching_digest_not_just_the_same_url(monkeypatch):
    expected = {**MAMBA_26_LINK_ACTION, "sha256": None}
    source = {**MAMBA_26_LINK_ACTION, "md5": None}
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args: [source]
    )
    with pytest.raises(MetadataConsistencyError, match="identity cannot be verified"):
        channel_metadata.verified_channel_records("/solver", "linux-64", [expected])


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


@pytest.mark.parametrize("version", [None, Version("2.7.0"), Version("2.8.0")])
def test_channel_query_does_not_normalize_null_after_upstream_fix(monkeypatch, version):
    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", "unbatched")
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: version)
    record = {**MAMBA_26_LINK_ACTION, "depends": None}
    response = {"result": {"pkgs": [record]}} if version else {record["name"]: [record]}

    def run(command, **kwargs):
        if command[-1] == "--help":
            return subprocess.CompletedProcess(command, 2, "", "no repoquery plugin")
        if command[1:3] == ["config", "list"]:
            return subprocess.CompletedProcess(command, 0, '{"offline": false}')
        return subprocess.CompletedProcess(command, 0, json.dumps(response))

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    if version == Version("2.7.0"):
        verified = channel_metadata.verified_channel_records(
            "/solver", "linux-64", [record]
        )
        assert verified is not None
        assert verified[MAMBA_26_LINK_ACTION["name"]]["depends"] == []
    else:
        with pytest.raises(
            MetadataConsistencyError, match="identity cannot be verified"
        ):
            channel_metadata.verified_channel_records("/solver", "linux-64", [record])


def test_conda_exact_name_queries_reconstruct_urls_from_explicit_channel(monkeypatch):
    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", "unbatched")
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: None)
    source = {
        **MAMBA_26_LINK_ACTION,
        "channel": "https://conda.anaconda.org/conda-forge/linux-64",
    }
    source.pop("url")
    seen = []

    def run(command, **kwargs):
        if command[-1] == "--help":
            return subprocess.CompletedProcess(command, 2, "", "no repoquery plugin")
        assert command[1] == "search"
        name = command[2]
        assert name in {"libzlib", "other"}
        seen.append(name)
        records = [source] if name == "libzlib" else []
        return subprocess.CompletedProcess(command, 0, json.dumps({name: records}))

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    records = [MAMBA_26_LINK_ACTION, {**MAMBA_26_LINK_ACTION, "name": "other"}]
    result = channel_metadata.query_channel_records("/solver", "linux-64", records)
    assert result is not None
    assert seen == ["libzlib", "other"]
    assert result[0]["url"] == MAMBA_26_LINK_ACTION["url"]
    verified = channel_metadata.verified_channel_records(
        "/solver", "linux-64", [MAMBA_26_LINK_ACTION]
    )
    assert verified is not None
    assert verified["libzlib"]["md5"] == source["md5"]
    source["channel"] = "conda-forge"
    with pytest.raises(
        MetadataConsistencyError, match="without a name or artifact URL"
    ):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION]
        )


@pytest.mark.parametrize("fallback", ["error", "", "unbatched", "skip"])
@pytest.mark.parametrize("version", [None, Version("1.5.12")])
def test_metadata_fallback_requires_explicit_choice(
    monkeypatch, caplog, fallback, version
):
    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", fallback)
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: version)
    records = json.loads(
        (
            Path(__file__).parents[1]
            / "test-mamba-fixtures"
            / "repoquery-conda-26.1.1.json"
        ).read_text()
    )["result"]["pkgs"]
    names = sorted(record["name"] for record in records)
    searches = []

    def run(command, **kwargs):
        if command[-1] == "--help":
            # conda-libmamba-solver 23.9 through 24.9: flag exists, batching does not.
            return subprocess.CompletedProcess(
                command, 0, "usage: search [--no-installed] spec"
            )
        assert command[1] == "search"
        searches.append(command[2])
        found = [record for record in records if record["name"] == command[2]]
        return subprocess.CompletedProcess(command, 0, json.dumps({command[2]: found}))

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    if fallback in {"error", ""}:
        with pytest.raises(MetadataConsistencyError, match="cannot batch") as error:
            channel_metadata.verified_channel_records("/solver", "linux-64", records)
        assert "CONDA_LOCK_METADATA_FALLBACK=unbatched" in str(error.value)
        assert searches == []
    else:
        verified = channel_metadata.verified_channel_records(
            "/solver", "linux-64", records
        )
        if fallback == "skip":
            assert verified is None
            assert searches == []
            assert "Missing dependencies or constraints may propagate" in caplog.text
        else:
            assert verified == {record["name"]: record for record in records}
            assert searches == names
            assert "minutes or hours" in caplog.text


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


@pytest.mark.parametrize(
    "response, message",
    [
        ({"result": {"pkgs": []}}, "is unavailable"),
        (
            {
                "result": {
                    "pkgs": [{**MAMBA_26_LINK_ACTION, "md5": "bad", "sha256": "bad"}]
                }
            },
            "identity cannot be verified",
        ),
    ],
)
def test_skip_setting_does_not_bypass_supported_verification(
    monkeypatch, response, message
):
    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", "skip")
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: None)

    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            MULTI_NAME_HELP if command[-1] == "--help" else json.dumps(response),
        )

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    with pytest.raises(MetadataConsistencyError, match=message):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION]
        )
