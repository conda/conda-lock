import typing

import pytest

from conda_lock.models.channel import Channel, _detect_used_env_var, _env_var_normalize
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

    # These two urls are equivalent since we can pull the env vars out.
    x = _env_var_normalize("http://$USER:$PASSWORD@host/prefix/t/$TOKEN/suffix")
    y = _env_var_normalize(f"http://{user}:{passwd}@host/prefix/t/{token}/suffix")

    assert x.env_var_url == y.env_var_url

    replaced = y.conda_token_replaced_url()
    assert replaced == f"http://{user}:{passwd}@host/t/<TOKEN>/prefix/suffix"


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
    collections: typing.List[str],
    expected: typing.Union[typing.List[str], typing.Type[Exception]],
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
