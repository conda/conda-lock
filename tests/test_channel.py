import typing

import pytest

from conda_lock.models.channel import (
    Channel,
    _channel_from_conda_url,
    _conda_url_from_string,
    _detect_used_env_var,
)
from conda_lock.src_parser.aggregation import unify_package_sources


if typing.TYPE_CHECKING:
    from pytest import MonkeyPatch


def test__detect_used_env_var(monkeypatch: "MonkeyPatch") -> None:
    monkeypatch.setenv("AKEY", "a")
    monkeypatch.setenv("ATOKEN", "a")
    monkeypatch.setenv("A", "a")

    assert _detect_used_env_var("a", ["TOKEN", "KEY"]) == "ATOKEN"

    monkeypatch.delenv("ATOKEN")
    assert _detect_used_env_var("a", ["TOKEN", "KEY"]) == "AKEY"

    monkeypatch.delenv("AKEY")
    assert _detect_used_env_var("a", ["TOKEN", "KEY"]) == "A"

    monkeypatch.delenv("A")
    assert _detect_used_env_var("a", ["TOKEN", "KEY"]) is None


def test_url_auth_info(monkeypatch: "MonkeyPatch") -> None:
    user = "user123"
    passwd = "pass123"
    token = "tokTOK123"

    monkeypatch.setenv("TOKEN", token)
    monkeypatch.setenv("USER", user)
    monkeypatch.setenv("PASSWORD", passwd)

    # These three urls are equivalent since we can pull the env vars out.
    x = _conda_url_from_string(
        "http://${USER}:${PASSWORD}@host/prefix/t/${TOKEN}/suffix"
    )
    y = _conda_url_from_string("http://$USER:$PASSWORD@host/prefix/t/$TOKEN/suffix")
    z = _conda_url_from_string(f"http://{user}:{passwd}@host/prefix/t/{token}/suffix")

    env_var_url = "http://${USER}:${PASSWORD}@host/prefix/t/${TOKEN}/suffix"
    assert user not in env_var_url
    assert passwd not in env_var_url
    assert token not in env_var_url
    assert x.env_var_url == env_var_url
    assert y.env_var_url == env_var_url
    assert z.env_var_url == env_var_url

    conda_replaced = f"http://{user}:{passwd}@host/prefix/t/<TOKEN>/suffix"
    assert user in conda_replaced
    assert passwd in conda_replaced
    assert "<TOKEN>" in conda_replaced
    assert _channel_from_conda_url(x).conda_token_replaced_url() == conda_replaced
    assert _channel_from_conda_url(y).conda_token_replaced_url() == conda_replaced
    assert _channel_from_conda_url(z).conda_token_replaced_url() == conda_replaced
    mamba_v1_replaced = f"http://{user}:{passwd}@host/prefix/t/*****/suffix"
    assert user in mamba_v1_replaced
    assert passwd in mamba_v1_replaced
    assert "*****" in mamba_v1_replaced
    assert _channel_from_conda_url(x).mamba_v1_token_replaced_url() == mamba_v1_replaced
    assert _channel_from_conda_url(y).mamba_v1_token_replaced_url() == mamba_v1_replaced
    assert _channel_from_conda_url(z).mamba_v1_token_replaced_url() == mamba_v1_replaced
    mamba_v2_replaced = f"http://{user}:{passwd}@host/prefix/t/**********/suffix"
    assert user in mamba_v2_replaced
    assert passwd in mamba_v2_replaced
    assert "**********" in mamba_v2_replaced
    assert _channel_from_conda_url(x).mamba_v2_token_replaced_url() == mamba_v2_replaced
    assert _channel_from_conda_url(y).mamba_v2_token_replaced_url() == mamba_v2_replaced
    assert _channel_from_conda_url(z).mamba_v2_token_replaced_url() == mamba_v2_replaced


@pytest.mark.parametrize(
    "collections,expected",
    [
        (
            [
                ["three", "two", "one"],
                ["two", "one"],
            ],
            ["three", "two", "one"],
        ),
        (
            [
                ["three", "two", "one"],
                ["two", "one"],
                [],
            ],
            ["three", "two", "one"],
        ),
        (
            [
                ["three", "two", "one"],
                ["three", "one", "two"],
            ],
            ValueError,
        ),
    ],
)
def test_unify_package_sources(
    collections: list[str],
    expected: typing.Union[list[str], type[Exception]],
):
    channel_collections = [
        [Channel.from_string(name) for name in collection] for collection in collections
    ]
    if isinstance(expected, list):
        result = unify_package_sources(channel_collections)
        assert [channel.url for channel in result] == expected
    else:
        with pytest.raises(expected):
            unify_package_sources(channel_collections)
