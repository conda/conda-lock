import typing

from .channel import detect_used_env_var, env_var_normalize


if typing.TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_detect_used_env_var(monkeypatch: "MonkeyPatch") -> None:
    monkeypatch.setenv("AKEY", "a")
    monkeypatch.setenv("ATOKEN", "a")
    monkeypatch.setenv("A", "a")

    assert detect_used_env_var("a", ["TOKEN", "KEY"]) == "ATOKEN"

    monkeypatch.delenv("ATOKEN")
    assert detect_used_env_var("a", ["TOKEN", "KEY"]) == "AKEY"

    monkeypatch.delenv("AKEY")
    assert detect_used_env_var("a", ["TOKEN", "KEY"]) == "A"

    monkeypatch.delenv("A")
    assert detect_used_env_var("a", ["TOKEN", "KEY"]) is None


def test_url_auth_info(monkeypatch: "MonkeyPatch") -> None:
    user = "user123"
    passwd = "pass123"
    token = "tokTOK123"

    monkeypatch.setenv("TOKEN", token)
    monkeypatch.setenv("USER", user)
    monkeypatch.setenv("PASSWORD", passwd)

    # These two urls are equivalent since we can pull the env vars out.
    x = env_var_normalize("http://$USER:$PASSWORD@host/prefix/t/$TOKEN/suffix")
    y = env_var_normalize(f"http://{user}:{passwd}@host/prefix/t/{token}/suffix")

    assert x.env_var_url == y.env_var_url

    replaced = y.conda_token_replaced_url()
    assert replaced == f"http://{user}:{passwd}@host/t/<TOKEN>/prefix/suffix"
