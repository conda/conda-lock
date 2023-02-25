import typing

from .channel import _detect_used_env_var, _env_var_normalize


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
