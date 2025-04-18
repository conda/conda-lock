"""
Conda lock supports two kinds of credentials used for channels:

## Token based

These are used by anaconda.org, Anaconda Enterprise, and Quetz.
To pass one of these channels, specify them in your source with an environment variable.
Make sure this environment variable is not expanded.

Example:
--channel 'http://host.com/t/$MY_REPO_TOKEN/channel'
# TODO: Detect environment variables that match a channel specified incorrectly.

## Simple Auth

For other channels (such as those self-managed), you may be using standard
username/password auth:

Example:
--channel 'http://$USER:$PASSWORD@host.com/channel'

## What gets stored

Since credential parts are both volatile and secret, conda-lock will not store
the raw version of a URL. If it encounters a channel URL that contains credentials,
it will search the available environment variables for a match. When found, that portion
of the URL will be replaced with an environment variable.

Since conda also performs env var substitution, the rendered output can contain env vars
which will be handled correctly by conda/mamba.
"""

import copy
import logging
import os
import re

from posixpath import expandvars
from typing import Any, List, Optional, Tuple
from urllib.parse import unquote, urlparse, urlunparse

from pydantic import BaseModel, ConfigDict

from conda_lock._vendor.conda.common.url import (
    mask_anaconda_token,
    split_anaconda_token,
)


logger = logging.getLogger(__name__)
token_pattern = re.compile(r"(.*)(/t/\$?\{?[a-zA-Z0-9-_]*\}?)(/.*)")


class _CondaUrl(BaseModel):
    """A high-level representation of a URL that may contain credentials.

    This is an intermediate representation that is used after parsing but before
    the URL is used to create a Channel object.
    """

    raw_url: str
    env_var_url: str
    token: Optional[str] = None
    token_env_var: Optional[str] = None
    user: Optional[str] = None
    user_env_var: Optional[str] = None
    password: Optional[str] = None
    password_env_var: Optional[str] = None


class Channel(BaseModel):
    model_config = ConfigDict(frozen=True)
    url: str
    used_env_vars: Tuple[str, ...]

    @classmethod
    def from_string(cls, value: str) -> "Channel":
        """The primary constructor for Channel.

        >>> Channel.from_string("conda-forge")
        Channel(url='conda-forge')

        Channels can be specified with a label
        >>> Channel.from_string("conda-forge/label/micromamba_prerelease")
        Channel(url='conda-forge/label/micromamba_prerelease')

        Channels can contain tokens, and these will be replaced with env vars
        >>> os.environ["MY_A_REPO_TOKEN"] = "tk-123-456"
        >>> Channel.from_string(
        ...     "https://host.com/t/tk-123-456/channel"
        ... )  # doctest: +NORMALIZE_WHITESPACE
        Channel(url='https://host.com/t/${MY_A_REPO_TOKEN}/channel',
            used_env_vars=('MY_A_REPO_TOKEN',))

        Channels can contain username/password credentials
        >>> os.environ["MY_A_USERNAME"] = "user"
        >>> os.environ["MY_A_PASSWORD"] = "pass"
        >>> Channel.from_string(
        ...     "https://user:pass@host.com/channel"
        ... )  # doctest: +NORMALIZE_WHITESPACE
        Channel(url='https://${MY_A_USERNAME}:${MY_A_PASSWORD}@host.com/channel',
            used_env_vars=('MY_A_PASSWORD', 'MY_A_USERNAME'))

        >>> del os.environ["MY_A_USERNAME"]
        >>> del os.environ["MY_A_PASSWORD"]
        >>> del os.environ["MY_A_REPO_TOKEN"]
        """
        if "://" in value:
            conda_url = _conda_url_from_string(value)
            channel = _channel_from_conda_url(conda_url)
            return channel
        return cls(url=value, used_env_vars=())

    def env_replaced_url(self) -> str:
        """Expand environment variables in the URL.

        Note that we only do POSIX-style expansion here in order to maintain
        consistency across platforms.

        >>> os.environ["MY_B_VAR"] = "value"
        >>> Channel.from_string(
        ...     "https://host.com/$MY_B_VAR/channel"
        ... ).env_replaced_url()
        'https://host.com/value/channel'
        >>> Channel.from_string(
        ...     "https://host.com/${MY_B_VAR}/channel"
        ... ).env_replaced_url()
        'https://host.com/value/channel'
        >>> del os.environ["MY_B_VAR"]
        """
        return expandvars(self.url)

    def conda_token_replaced_url(self) -> str:
        """Emulate conda's token replacement in the output URL.

        This is used to recognize URLs contained in explicit lockfiles created by
        conda. In these lockfiles, the token is censored with <TOKEN>.

        >>> Channel.from_string(
        ...     "https://host.com/t/asdfjkl/channel"
        ... ).conda_token_replaced_url()
        'https://host.com/t/<TOKEN>/channel'
        """
        expanded_url = self.env_replaced_url()
        return mask_anaconda_token(expanded_url)

    def mamba_v1_token_replaced_url(self) -> str:
        """Emulate mamba's v1 token replacement in the output URL.

        This is used to recognize URLs contained in explicit lockfiles created by
        mamba or micromamba. In these lockfiles, the token is censored with *****.

        >>> Channel.from_string(
        ...     "https://host.com/t/asdfjkl/channel"
        ... ).mamba_v1_token_replaced_url()
        'https://host.com/t/*****/channel'
        """
        expanded_url = self.env_replaced_url()
        _, token = split_anaconda_token(expanded_url)
        return expanded_url.replace(token, "*****", 1) if token else expanded_url

    def mamba_v2_token_replaced_url(self) -> str:
        """Emulate mamba's v2 token replacement in the output URL.

        This is used to recognize URLs contained in explicit lockfiles created by
        mamba or micromamba. In these lockfiles, the token is censored with **********.

        >>> Channel.from_string(
        ...     "https://host.com/t/asdfjkl/channel"
        ... ).mamba_v2_token_replaced_url()
        'https://host.com/t/**********/channel'
        """
        expanded_url = self.env_replaced_url()
        _, token = split_anaconda_token(expanded_url)
        return expanded_url.replace(token, "**********", 1) if token else expanded_url

    def __repr_args__(self) -> List[Tuple[str, Any]]:
        """Hide falsy values from repr.

        # Note how used_env_vars is not shown:
        >>> Channel.from_string("conda-forge")
        Channel(url='conda-forge')

        # It's only shown when it's non-empty:
        >>> Channel.from_string(
        ...     "https://host.com/t/$MY_REPO_TOKEN/channel"
        ... )  # doctest: +NORMALIZE_WHITESPACE
        Channel(url='https://host.com/t/${MY_REPO_TOKEN}/channel',
            used_env_vars=('MY_REPO_TOKEN',))
        """
        return [(key, value) for key, value in self.__dict__.items() if value]


def _detect_used_env_var(
    value: str, preferred_env_var_suffix: List[str]
) -> Optional[str]:
    """Detect if the string exactly matches any current environment variable.

    Preference is given to variables that end in the provided suffixes.
    """
    if value.startswith("$"):
        return value.lstrip("$").strip("{}")

    for suffix in [*preferred_env_var_suffix, ""]:
        candidates = {v: k for k, v in os.environ.items() if k.upper().endswith(suffix)}
        # Try first with a simple match
        if key := candidates.get(value):
            return key
        # Try with unquote
        if key := candidates.get(unquote(value)):
            return key
    return None


def _conda_url_from_string(url: str) -> _CondaUrl:
    """Normalize URL by using environment variables."""
    res = urlparse(url)

    def make_netloc(
        username: Optional[str], password: Optional[str], host: str, port: Optional[int]
    ) -> str:
        host_info = f"{host}:{port}" if port else host
        if not username:
            return host_info

        user_info = f"{username}:{password}" if password else username
        return f"{user_info}@{host_info}"

    user_env_var: Optional[str] = None
    password_env_var: Optional[str] = None
    token_env_var: Optional[str] = None

    def get_or_raise(val: Optional[str]) -> str:
        if val is None:
            raise ValueError("Expected to be non-null")
        return val

    res_replaced = copy.copy(res)

    if res.username:
        user_env_var = _detect_used_env_var(res.username, ["USERNAME", "USER"])
        if user_env_var:
            res_replaced = res_replaced._replace(
                netloc=make_netloc(
                    username=f"${{{user_env_var}}}",
                    password=res_replaced.password,
                    host=get_or_raise(res_replaced.hostname),
                    port=res_replaced.port,
                )
            )

    if res.password:
        password_env_var = _detect_used_env_var(
            res.password, ["PASSWORD", "PASS", "TOKEN", "KEY"]
        )
        if password_env_var:
            res_replaced = res_replaced._replace(
                netloc=make_netloc(
                    username=res_replaced.username,
                    password=f"${{{password_env_var}}}",
                    host=get_or_raise(res_replaced.hostname),
                    port=res_replaced.port,
                )
            )

    _token_match = token_pattern.search(res.path)
    token = _token_match.groups()[1][3:] if _token_match else None
    if token:
        token_env_var = _detect_used_env_var(
            token, ["TOKEN", "CRED", "PASSWORD", "PASS", "KEY"]
        )
        if not token_env_var:
            # Maybe we should raise here if we have mismatched env vars
            logger.warning("Token URL detected without env var")
        else:
            new_path = token_pattern.sub(
                rf"\1/t/${{{token_env_var}}}\3", res_replaced.path
            )
            res_replaced = res_replaced._replace(path=new_path)

    return _CondaUrl(
        raw_url=url,
        env_var_url=urlunparse(res_replaced),
        user=res.username,
        user_env_var=user_env_var,
        password=res.password,
        password_env_var=password_env_var,
        token=token,
        token_env_var=token_env_var,
    )


def _channel_from_conda_url(conda_url: _CondaUrl) -> Channel:
    conda_url = conda_url
    env_vars_maybe_with_none = {
        conda_url.user_env_var,
        conda_url.token_env_var,
        conda_url.password_env_var,
    }
    env_vars = {v for v in env_vars_maybe_with_none if v is not None}
    return Channel(
        url=conda_url.env_var_url,
        used_env_vars=tuple(sorted(env_vars)),
    )


def normalize_url_with_placeholders(url: str, channels: List[Channel]) -> str:
    """Normalize URLs from lockfiles to have env var placeholders.

    The env vars are associated with channel objects, so we do the replacement for
    each channel object individually.

    Conda lockfiles with tokens are censored to look like this:
    >>> conda_url = (
    ...     "http://localhost:32817/t/<TOKEN>/get/proxy-channel"
    ...     "/linux-64/zlib-1.3.1-hb9d3cd8_2.conda#c9f075ab2f33b3bbee9e62d4ad0a6cd8"
    ... )

    Mamba v1 lockfiles are censored with ***** instead of <TOKEN>
    >>> mamba_v1_url = conda_url.replace("<TOKEN>", "*****")

    Mamba v2 lockfiles are censored with ********** instead of <TOKEN>
    >>> mamba_v2_url = conda_url.replace("<TOKEN>", "**********")

    Create a channel with a token stored in an env var
    >>> os.environ["MY_C_REPO_TOKEN"] = "some-token"
    >>> channel_url = "http://localhost:32817/t/some-token/get/proxy-channel"
    >>> channel = Channel.from_string(channel_url)
    >>> channel  # doctest: +NORMALIZE_WHITESPACE
    Channel(url='http://localhost:32817/t/${MY_C_REPO_TOKEN}/get/proxy-channel',
        used_env_vars=('MY_C_REPO_TOKEN',))

    The normalized URL should have the token replaced with the env var placeholder
    >>> expected_normalized_url = conda_url.replace("<TOKEN>", "${MY_C_REPO_TOKEN}")

    Check that the normalized URL is correct for both conda and mamba censorings
    >>> normalized_conda_url = normalize_url_with_placeholders(conda_url, [channel])
    >>> normalized_mamba_v1_url = normalize_url_with_placeholders(mamba_v1_url, [channel])
    >>> normalized_mamba_v2_url = normalize_url_with_placeholders(mamba_v2_url, [channel])
    >>> assert normalized_conda_url == expected_normalized_url, normalized_conda_url
    >>> assert normalized_mamba_v1_url == expected_normalized_url, normalized_mamba_v1_url
    >>> assert normalized_mamba_v2_url == expected_normalized_url, normalized_mamba_v2_url

    Normalization should also work similarly for basic auth
    >>> os.environ["MY_C_USERNAME"] = "user"
    >>> os.environ["MY_C_PASSWORD"] = "pass"
    >>> channel_url = "http://user:pass@localhost:32817/channel"
    >>> channel = Channel.from_string(channel_url)
    >>> channel  # doctest: +NORMALIZE_WHITESPACE
    Channel(url='http://${MY_C_USERNAME}:${MY_C_PASSWORD}@localhost:32817/channel',
        used_env_vars=('MY_C_PASSWORD', 'MY_C_USERNAME'))
    >>> normalize_url_with_placeholders(channel_url, channels=[channel])
    'http://${MY_C_USERNAME}:${MY_C_PASSWORD}@localhost:32817/channel'

    Clean up
    >>> del os.environ["MY_C_REPO_TOKEN"]
    >>> del os.environ["MY_C_USERNAME"]
    >>> del os.environ["MY_C_PASSWORD"]
    """
    for channel in channels:
        candidate1 = channel.conda_token_replaced_url()
        if url.startswith(candidate1):
            url = url.replace(candidate1, channel.url, 1)

        candidate2 = channel.mamba_v1_token_replaced_url()
        if url.startswith(candidate2):
            url = url.replace(candidate2, channel.url, 1)

        candidate3 = channel.mamba_v2_token_replaced_url()
        if url.startswith(candidate3):
            url = url.replace(candidate3, channel.url, 1)

        candidate4 = channel.env_replaced_url()
        if url.startswith(candidate4):
            url = url.replace(candidate4, channel.url, 1)
    return url
