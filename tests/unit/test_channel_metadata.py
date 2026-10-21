import copy
import json
import subprocess

from pathlib import Path

import pytest

from packaging.version import Version

from conda_lock.errors import CondaLockError, MetadataConsistencyError
from conda_lock.models.channel import Channel
from conda_lock.solver import channel_metadata
from tests.support.fixtures import MAMBA_26_LINK_ACTION


CHANNELS = [Channel.from_string("https://conda.anaconda.org/conda-forge")]


MULTI_NAME_HELP = "usage: conda repoquery search [--no-installed] specs [specs ...]"


@pytest.fixture(autouse=True)
def clear_capability_cache():
    channel_metadata._cached_conda_repoquery.cache_clear()
    yield
    channel_metadata._cached_conda_repoquery.cache_clear()


def test_capability_probe_is_reused_until_solver_or_environment_changes(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, MULTI_NAME_HELP)

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    for _ in range(2):
        assert channel_metadata._supports_conda_repoquery("/solver", {})
    assert len(calls) == 1
    assert channel_metadata._supports_conda_repoquery("/other/solver", {})
    assert channel_metadata._supports_conda_repoquery(
        "/solver", {"CONDA_NO_PLUGINS": "true"}
    )
    assert len(calls) == 3


@pytest.mark.parametrize(
    "exception,empty", [("PackagesNotFoundError", True), ("CondaHTTPError", False)]
)
def test_legacy_search_distinguishes_absence_from_query_failure(exception, empty):
    proc = subprocess.CompletedProcess(
        ["conda", "search"], 1, json.dumps({"exception_name": exception})
    )
    if empty:
        assert channel_metadata._query_result_records(proc) == []
    else:
        with pytest.raises(MetadataConsistencyError, match="query exited"):
            channel_metadata._query_result_records(proc)


@pytest.mark.parametrize(
    "solver,advice",
    [("/bin/mamba", "Upgrade to Mamba 2"), ("/bin/conda", "conda-libmamba-solver")],
)
def test_unavailable_batching_identifies_the_selected_solver(solver, advice):
    with pytest.raises(MetadataConsistencyError) as error:
        channel_metadata._use_unbatched_fallback("error", "linux-64", 1, solver)
    assert Path(solver).name in str(error.value)
    assert advice in str(error.value)


@pytest.mark.parametrize(
    "flags,expected",
    [
        (
            "-k --repodata-fn=current_repodata.json",
            ["-k", "--repodata-fn", "current_repodata.json"],
        ),
        (
            '--ssl-verify "/tmp/company certificate.pem" --repodata-ttl 0',
            ["--ssl-verify", "/tmp/company certificate.pem", "--repodata-ttl", "0"],
        ),
        ("--solver classic --strict-channel-priority -vv", []),
    ],
)
def test_verification_forwards_transport_and_repodata_flags(
    monkeypatch, flags, expected
):
    monkeypatch.setenv("CONDA_FLAGS", flags)
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: None)
    monkeypatch.setattr(
        channel_metadata, "_supports_conda_repoquery", lambda *args: True
    )
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"result": {"pkgs": []}}')

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    channel_metadata.query_channel_records(
        "conda", "linux-64", [MAMBA_26_LINK_ACTION], channels=[]
    )
    assert commands[0][commands[0].index("--json") + 1 :] == expected


@pytest.mark.parametrize(
    "flag",
    [
        "-c https://example.org/extra",
        "--channel=https://example.org/extra",
        "-c=https://example.org/extra",
        "-chttps://example.org/extra",
    ],
)
def test_extra_channels_keep_solve_priority_and_credentials(monkeypatch, flag):
    monkeypatch.setenv("CONDA_FLAGS", flag)
    extra = Channel.from_string("https://example.org/extra")
    channels, flags = channel_metadata.metadata_query_configuration([*CHANNELS, extra])
    assert channels == [extra, *CHANNELS]
    assert flags == []


@pytest.mark.parametrize(
    "flags", ["--unknown-option", "--prefix /tmp/different", "--channel"]
)
def test_unsupported_metadata_flags_fail_before_solving(monkeypatch, flags):
    from conda_lock import conda_solver

    monkeypatch.setenv("CONDA_FLAGS", flags)
    monkeypatch.setattr(
        conda_solver,
        "solve_specs_for_arch",
        lambda **kw: pytest.fail("must fail before solve"),
    )
    with pytest.raises(CondaLockError, match="CONDA_FLAGS option"):
        conda_solver.solve_conda(
            conda="conda",
            specs={},
            locked={},
            update=[],
            platform="linux-64",
            channels=[],
            mapping_url="unused",
        )


def test_invalid_positional_flags_do_not_echo_credentials(monkeypatch):
    monkeypatch.setenv("CONDA_FLAGS", "https://user:secret@example.org/private")
    with pytest.raises(CondaLockError) as error:
        channel_metadata.metadata_query_configuration([])
    assert "positional argument" in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "expected_token, queried_token",
    [
        ("**********", "*****"),  # create vs repoquery on Mamba 2
        ("$CHANNEL_TOKEN", "<TOKEN>"),  # stored lock vs Conda
        ("${CHANNEL_TOKEN}", "*****"),  # stored lock vs Mamba
        ("${CHANNEL_TOKEN}", None),  # Conda repoquery can omit the token entirely
    ],
)
def test_private_channel_verification_uses_configured_credentials(
    monkeypatch, expected_token, queried_token
):
    monkeypatch.setenv("CHANNEL_TOKEN", "test-secret")
    placeholder = expected_token if expected_token.startswith("$") else "$CHANNEL_TOKEN"
    channel = Channel.from_string(f"https://example.org/t/{placeholder}/private")
    filename = MAMBA_26_LINK_ACTION["fn"]

    def record(token):
        token_path = f"/t/{token}" if token is not None else ""
        return {
            **MAMBA_26_LINK_ACTION,
            "url": f"https://example.org{token_path}/private/linux-64/{filename}",
        }

    expected = {**record(expected_token), "depends": []}
    original = copy.deepcopy(expected)
    canonical = record(queried_token)
    commands = []
    monkeypatch.setenv("CONDA_FLAGS", "")
    monkeypatch.setattr(channel_metadata, "mamba_binary_version", lambda _: None)

    def run(command, **kwargs):
        commands.append(command)
        if command[-1] == "--help":
            return subprocess.CompletedProcess(command, 0, MULTI_NAME_HELP)
        assert command[command.index("--channel") + 1] == (
            "https://example.org/t/test-secret/private"
        )
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"result": {"pkgs": [canonical]}})
        )

    monkeypatch.setattr(channel_metadata.subprocess, "run", run)
    cache = {}
    verified = channel_metadata.verified_channel_records(
        "/solver",
        "linux-64",
        [expected],
        channels=[channel],
        allow_missing=True,
        verified_cache=cache,
    )
    assert verified is not None
    assert verified["libzlib"] == {**canonical, "url": expected["url"]}
    assert expected == original

    # Another solve phase can spell the same URL differently; it must reuse the
    # verified observation, but still check hashes and preserve its input URL.
    alternate = record("**********")
    again = channel_metadata.verified_channel_records(
        "/solver",
        "linux-64",
        [alternate],
        channels=[channel],
        verified_cache=cache,
    )
    assert again is not None
    assert again["libzlib"]["url"] == alternate["url"]
    assert len(commands) == 2  # capability probe + one batch query
    with pytest.raises(MetadataConsistencyError, match="identity cannot be verified"):
        channel_metadata.verified_channel_records(
            "/solver",
            "linux-64",
            [{**alternate, "md5": "wrong"}],
            channels=[channel],
            verified_cache=cache,
        )
    assert len(commands) == 2

    # Equal names, builds and hashes in a different channel do not establish
    # that the expected artifact is still published in its original location.
    moved = {**canonical, "url": canonical["url"].replace("/private/", "/other/")}
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args, **kwargs: [moved]
    )
    assert (
        channel_metadata.verified_channel_records(
            "/solver",
            "linux-64",
            [expected],
            channels=[channel],
            allow_missing=True,
        )
        == {}
    )


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
    result = channel_metadata.verified_channel_records(
        "/solver", "linux-64", records, channels=CHANNELS
    )
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
            channel_metadata.query_channel_records(
                "/solver", "linux-64", [record], channels=CHANNELS
            )
        assert not commands
        return
    result = channel_metadata.query_channel_records(
        "/solver", "linux-64", [record], channels=CHANNELS
    )
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
        channel_metadata, "query_channel_records", lambda *args, **kwargs: [trusted]
    )
    damaged = {**trusted, "depends": []}
    original = copy.deepcopy(damaged)
    refreshed = channel_metadata.verified_channel_records(
        "/solver", "linux-64", [damaged], channels=CHANNELS
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
        channel_metadata, "query_channel_records", lambda *args, **kwargs: [source]
    )
    result = channel_metadata.verified_channel_records(
        "/solver", "linux-64", [expected], channels=CHANNELS
    )
    assert result is not None
    assert result["libzlib"]["url"] == expected["url"]
    assert result["libzlib"]["depends"] == source["depends"]


def test_missing_channel_artifact_is_unverifiable_not_proven_corrupt(monkeypatch):
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args, **kwargs: []
    )
    with pytest.raises(MetadataConsistencyError, match="is unavailable"):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [dict(MAMBA_26_LINK_ACTION)], channels=CHANNELS
        )


def test_update_refresh_can_omit_an_unavailable_artifact(monkeypatch):
    expected = dict(MAMBA_26_LINK_ACTION)
    replacement = {**expected, "url": expected["url"] + ".different", "depends": []}
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args, **kwargs: [replacement]
    )
    assert (
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [expected], channels=CHANNELS, allow_missing=True
        )
        == {}
    )
    # A different artifact's metadata must never be attached to the old artifact.
    with pytest.raises(MetadataConsistencyError, match="is unavailable"):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [expected], channels=CHANNELS
        )


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
        channel_metadata, "query_channel_records", lambda *args, **kwargs: queried
    )
    with pytest.raises(MetadataConsistencyError):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [expected], channels=CHANNELS, allow_missing=True
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
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION], channels=CHANNELS
        )
    assert "fresh package cache" not in str(exc.value)
    assert "sensitive stderr" not in str(exc.value)
    assert len(commands) == 2


def test_refresh_requires_a_matching_digest_not_just_the_same_url(monkeypatch):
    expected = {**MAMBA_26_LINK_ACTION, "sha256": None}
    source = {**MAMBA_26_LINK_ACTION, "md5": None}
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args, **kwargs: [source]
    )
    with pytest.raises(MetadataConsistencyError, match="identity cannot be verified"):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [expected], channels=CHANNELS
        )


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
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION], channels=CHANNELS
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
            "/solver", "linux-64", [record], channels=CHANNELS
        )
        assert verified is not None
        assert verified[MAMBA_26_LINK_ACTION["name"]]["depends"] == []
    else:
        with pytest.raises(
            MetadataConsistencyError, match="identity cannot be verified"
        ):
            channel_metadata.verified_channel_records(
                "/solver", "linux-64", [record], channels=CHANNELS
            )


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
    result = channel_metadata.query_channel_records(
        "/solver", "linux-64", records, channels=CHANNELS
    )
    assert result is not None
    assert seen == ["libzlib", "other"]
    assert result[0]["url"] == MAMBA_26_LINK_ACTION["url"]
    verified = channel_metadata.verified_channel_records(
        "/solver", "linux-64", [MAMBA_26_LINK_ACTION], channels=CHANNELS
    )
    assert verified is not None
    assert verified["libzlib"]["md5"] == source["md5"]
    source["channel"] = "conda-forge"
    with pytest.raises(
        MetadataConsistencyError, match="without a name or artifact URL"
    ):
        channel_metadata.verified_channel_records(
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION], channels=CHANNELS
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
            channel_metadata.verified_channel_records(
                "/solver", "linux-64", records, channels=CHANNELS
            )
        assert "CONDA_LOCK_METADATA_FALLBACK=unbatched" in str(error.value)
        assert searches == []
    else:
        verified = channel_metadata.verified_channel_records(
            "/solver", "linux-64", records, channels=CHANNELS
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


@pytest.mark.parametrize("value", [" ", "skpi", "false"])
def test_invalid_metadata_fallback_fails_before_solving(monkeypatch, value):
    from conda_lock import conda_solver

    monkeypatch.setenv("CONDA_LOCK_METADATA_FALLBACK", value)
    monkeypatch.setattr(
        conda_solver,
        "solve_specs_for_arch",
        lambda **kw: pytest.fail("Invalid setting must fail before solving"),
    )
    with pytest.raises(CondaLockError, match="Invalid CONDA_LOCK_METADATA_FALLBACK"):
        conda_solver.solve_conda(
            conda="/solver",
            specs={},
            locked={},
            update=[],
            platform="linux-64",
            channels=[],
            mapping_url="unused",
        )


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
            "/solver", "linux-64", [MAMBA_26_LINK_ACTION], channels=CHANNELS
        )


def test_verification_reuses_only_checked_artifacts_and_rechecks_hashes(monkeypatch):
    old = copy.deepcopy(dict(MAMBA_26_LINK_ACTION))
    new = {
        **old,
        "version": "9.0",
        "build": "0",
        "build_string": "0",
        "build_number": 0,
        "fn": "libzlib-9.0-0.conda",
        "url": old["url"].rsplit("/", 1)[0] + "/libzlib-9.0-0.conda",
        "md5": "a" * 32,
        "sha256": "b" * 64,
    }
    calls = []

    def query(conda, platform, records, **kwargs):
        calls.append([r["url"] for r in records])
        return [old, new]  # A search includes versions not selected by this plan.

    monkeypatch.setattr(channel_metadata, "query_channel_records", query)
    cache = {}
    first = channel_metadata.verified_channel_records(
        "solver", "linux-64", [old], channels=CHANNELS, verified_cache=cache
    )
    assert first is not None
    first[old["name"]]["depends"].append("do-not-cache-caller-mutations")
    again = channel_metadata.verified_channel_records(
        "solver", "linux-64", [old], channels=CHANNELS, verified_cache=cache
    )
    assert again is not None
    assert again[old["name"]]["depends"] == old["depends"]
    assert len(calls) == 1
    channel_metadata.verified_channel_records(
        "solver", "linux-64", [new], channels=CHANNELS, verified_cache=cache
    )
    assert calls == [[old["url"]], [new["url"]]]
    with pytest.raises(MetadataConsistencyError, match="identity cannot be verified"):
        channel_metadata.verified_channel_records(
            "solver",
            "linux-64",
            [{**new, "sha256": "c" * 64}],
            channels=CHANNELS,
            verified_cache=cache,
        )
    assert len(calls) == 2  # Even an in-memory hit must reject contradictory hashes.


def test_missing_or_skipped_records_are_not_cached(monkeypatch):
    record = copy.deepcopy(dict(MAMBA_26_LINK_ACTION))
    replies = iter([None, [], [record]])
    monkeypatch.setattr(
        channel_metadata, "query_channel_records", lambda *args, **kwargs: next(replies)
    )
    cache = {}
    assert (
        channel_metadata.verified_channel_records(
            "solver", "linux-64", [record], channels=CHANNELS, verified_cache=cache
        )
        is None
    )
    assert cache == {}
    assert (
        channel_metadata.verified_channel_records(
            "solver",
            "linux-64",
            [record],
            channels=CHANNELS,
            allow_missing=True,
            verified_cache=cache,
        )
        == {}
    )
    assert cache == {}
    verified = channel_metadata.verified_channel_records(
        "solver", "linux-64", [record], channels=CHANNELS, verified_cache=cache
    )
    assert verified is not None
    assert verified[record["name"]] == record
