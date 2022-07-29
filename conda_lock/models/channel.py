"""
Conda lock supports two kinds of credentials used for channels

## Token based

These are used by anaconda.org, Anaconda Enterprise and Quetz

To pass one of these channels specify them in your source with an environment variable

Make sure this environment variable is not expanded.

--channel 'http://host.com/t/$MY_REPO_TOKEN/channel'
# TODO: Detect environment variables that match a channel specified incorrectly.

## Simple Auth

For other channels (such as those self-managed) you may be using a more standard username/password auth

--channel 'http://$USER:$PASSWORD@host.com/channel'

# What gets stored

Since we can generally assume that these parts are both volatile AND secret conda-lock will not store
the raw version of a url.  If it encounters a channel url that looks as if it contains a credential portion
it will search the currently available environment variables for a match with that variable.  In the case
of a match that portion of the url will be replaced with a environment variable.

Conveniently since conda ALSO performs env var substitution the rendered output can contain env vars which
will be handled correctly by conda/mamba.

## PIP?

"""

import copy
import logging
import os
import re
import typing

from posixpath import expandvars
from typing import FrozenSet, List, Optional, cast
from urllib.parse import unquote, urlparse, urlunparse

from pydantic import BaseModel, Field


if typing.TYPE_CHECKING:
    from pydantic.typing import ReprArgs


logger = logging.getLogger(__name__)
token_pattern = re.compile(r"(.*)(/t/\$?\{?[a-zA-Z0-9-_]*\}?)(/.*)")


class CondaUrl(BaseModel):
    raw_url: str
    env_var_url: str

    token: Optional[str]
    token_env_var: Optional[str]

    user: Optional[str]
    user_env_var: Optional[str]

    password: Optional[str]
    password_env_var: Optional[str]

    @classmethod
    def from_string(cls, value: str) -> "CondaUrl":
        return env_var_normalize(value)

    def conda_token_replaced_url(self) -> str:
        """This is basically a crazy thing that conda does for the token replacement in the output"""
        # TODO: pass in env vars maybe?
        expanded_url = expandvars(self.env_var_url)
        if token_pattern.match(expanded_url):
            replaced = token_pattern.sub(r"\1\3", expanded_url, 1)
            p = urlparse(replaced)
            replaced = urlunparse(p._replace(path="/t/<TOKEN>" + p.path))
            return replaced
        return expanded_url


class ZeroValRepr(BaseModel):
    """Repr helper that hides falsely values"""

    def __repr_args__(self: BaseModel) -> "ReprArgs":
        return [(key, value) for key, value in self.__dict__.items() if value]


class Channel(ZeroValRepr, BaseModel):
    url: str
    used_env_vars: FrozenSet[str] = Field(default=frozenset())

    def __lt__(self, other: "Channel") -> bool:
        return tuple(self.dict().values()) < tuple(other.dict().values())

    class Config:
        frozen = True

    @classmethod
    def from_string(cls, value: str) -> "Channel":
        if "://" in value:
            # url like string
            return cls.from_conda_url(CondaUrl.from_string(value))
        else:
            # this is a simple url
            return Channel(url=value, used_env_vars=frozenset([]))

    @classmethod
    def from_conda_url(cls, value: CondaUrl) -> "Channel":
        env_vars = {value.user_env_var, value.token_env_var, value.password_env_var}
        if None in env_vars:
            env_vars.remove(None)

        return Channel(
            url=value.env_var_url,
            used_env_vars=frozenset(cast(FrozenSet[str], env_vars)),
        )

    def env_replaced_url(self) -> str:
        return expandvars(self.url)

    def conda_token_replaced_url(self) -> str:
        """This is basically a crazy thing that conda does for the token replacement in the output"""
        # TODO: pass in env vars maybe?
        expanded_url = expandvars(self.url)
        if token_pattern.match(expanded_url):
            replaced = token_pattern.sub(r"\1\3", expanded_url, 1)
            p = urlparse(replaced)
            replaced = urlunparse(p._replace(path="/t/<TOKEN>" + p.path))
            return replaced
        return expanded_url


def detect_used_env_var(
    value: str, preferred_env_var_suffix: List[str]
) -> Optional[str]:
    """Detects if the string exactly matches any current environment variable

    Preference is given to variables that end in the suffixes provided
    """

    if value.startswith("$"):
        return value.lstrip("$").strip("{}")
    for suffix in preferred_env_var_suffix + [""]:
        candidates = {v: k for k, v in os.environ.items() if k.upper().endswith(suffix)}
        # try first with a simple match
        key = candidates.get(value)
        if key:
            return key
        # try with unquote
        key = candidates.get(unquote(value))
        if key:
            return key
    return None


def env_var_normalize(url: str) -> CondaUrl:
    """
    Normalizes url by using env vars
    """
    res = urlparse(url)
    res_replaced = copy.copy(res)

    def make_netloc(
        username: Optional[str], password: Optional[str], host: str, port: Optional[int]
    ) -> str:
        if port:
            host_info = f"{host}:{port:d}"
        else:
            host_info = host

        if username:
            if password:
                user_info = f"{username}:{password}"
            else:
                user_info = username
        else:
            user_info = ""

        if user_info:
            return f"{user_info}@{host_info}"
        else:
            return host_info

    user_env_var: Optional[str] = None
    password_env_var: Optional[str] = None
    token_env_var: Optional[str] = None

    def get_or_raise(val: Optional[str]) -> str:
        if val is None:
            raise ValueError("Expected to be non Null")
        return val

    if res.username:
        user_env_var = detect_used_env_var(res.username, ["USERNAME", "USER"])
        if user_env_var:
            res_replaced = res_replaced._replace(
                netloc=make_netloc(
                    username=f"${user_env_var}",
                    password=res_replaced.password,
                    host=get_or_raise(res_replaced.hostname),
                    port=res_replaced.port,
                )
            )
    if res.password:
        password_env_var = detect_used_env_var(
            res.password, ["PASSWORD", "PASS", "TOKEN", "KEY"]
        )
        if password_env_var:
            res_replaced = res_replaced._replace(
                netloc=make_netloc(
                    username=res_replaced.username,
                    password=f"${password_env_var}",
                    host=get_or_raise(res_replaced.hostname),
                    port=res_replaced.port,
                )
            )

    _token_match = token_pattern.search(res.path)
    token = _token_match.groups()[1][3:] if _token_match else None
    if token:
        token_env_var = detect_used_env_var(
            token, ["TOKEN", "CRED", "PASSWORD", "PASS", "KEY"]
        )
        if not token_env_var:
            # maybe we should raise here if we have mismatched env vars
            logger.warning("token url detected without env var")
        else:
            new_path = token_pattern.sub(rf"\1/t/${token_env_var}\3", res_replaced.path)
            res_replaced = res_replaced._replace(path=new_path)

    return CondaUrl(
        raw_url=url,
        env_var_url=urlunparse(res_replaced),
        user=res.username,
        user_env_var=user_env_var,
        password=res.password,
        password_env_var=password_env_var,
        token=token,
        token_env_var=token_env_var,
    )
