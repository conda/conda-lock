"""Tests for cache paths, URL normalization and artifact identity checks."""

from __future__ import annotations

from pathlib import Path

from conda_lock.solver.repodata_cache import (
    hierarchical_cache_subpath,
    libmamba_strip_url_secrets,
    normalize_url_for_compare,
)
from tests.support.fixtures import MAMBA_26_LINK_ACTION


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_libmamba_strip_url_secrets_handles_no_scheme_credentials():
    """libmamba removes ``user:pass@`` and ``/t/<token>`` even on URLs
    without a scheme (see ``test_cpp.cpp`` ``URLs without scheme``)."""
    assert (
        libmamba_strip_url_secrets("root:secretpassword@myweb.com/test.repo")
        == "myweb.com/test.repo"
    )
    assert (
        libmamba_strip_url_secrets("myweb.com/t/my-12345-token/test.repo")
        == "myweb.com/test.repo"
    )
    # Don't get fooled by '@' that appears after the path separator.
    assert (
        libmamba_strip_url_secrets("myweb.com/path/with@in-it/foo")
        == "myweb.com/path/with@in-it/foo"
    )


def test_libmamba_strip_url_secrets_token_without_trailing_slash():
    """libmamba's token regex matches ``/t/<token>`` whether or not there's
    a trailing path component; the previous regex required a trailing
    slash, leaving terminal-token URLs untouched."""
    assert (
        libmamba_strip_url_secrets("https://repo.com/t/my-token") == "https://repo.com"
    )
    assert (
        libmamba_strip_url_secrets("https://repo.com/t/my-token/path")
        == "https://repo.com/path"
    )


def test_libmamba_strip_url_secrets_token_regex_is_intentionally_overreaching():
    """Pin the slightly weird-looking behavior so a future cleanup
    doesn't quietly diverge from libmamba's cache path derivation.

    libmamba's regex (``/t/([a-zA-Z0-9-_]{0,2}[a-zA-Z0-9-]*)``) treats
    *any* ``/t/<chars>`` path component as an auth token and removes it.
    A path like ``/not/t/pkg.conda`` therefore loses the ``/t/pkg``
    segment. We deliberately match that behavior so our cache lookup
    derives the same path mamba 2.6.0 wrote -- correctness with respect
    to the cache, not generic URL sanitization.
    """
    assert (
        libmamba_strip_url_secrets("https://repo.com/not/t/pkg.conda")
        == "https://repo.com/not.conda"
    )


def test_normalize_url_for_compare_force_https_strip_slash():
    """The normalized form drops scheme differences and trailing slashes."""
    assert normalize_url_for_compare(
        "http://conda.example.com/c/linux-64/foo.conda"
    ) == normalize_url_for_compare("https://conda.example.com/c/linux-64/foo.conda/")
    assert normalize_url_for_compare(
        "https://user:pw@conda.example.com/c/linux-64/foo.conda"
    ) == normalize_url_for_compare("https://conda.example.com/c/linux-64/foo.conda")


# ---------------------------------------------------------------------------
# hierarchical_cache_subpath
# ---------------------------------------------------------------------------


def test_hierarchical_cache_subpath_from_url():
    """The cache subpath is derived from the package URL, not a glob."""
    sub = hierarchical_cache_subpath(MAMBA_26_LINK_ACTION)
    assert sub == Path("https/conda.anaconda.org/conda-forge/linux-64")


def test_hierarchical_cache_subpath_from_base_url_and_platform():
    """Falls back to base_url + platform when the URL is absent."""
    sparse = {
        "base_url": "https://conda.anaconda.org/conda-forge",
        "platform": "linux-64",
    }
    assert hierarchical_cache_subpath(sparse) == Path(
        "https/conda.anaconda.org/conda-forge/linux-64"
    )


def test_hierarchical_cache_subpath_strips_duplicated_platform_suffix():
    """``base_url`` may already include the subdir; don't double it up."""
    sparse = {
        "base_url": "https://conda.anaconda.org/conda-forge/linux-64",
        "platform": "linux-64",
    }
    assert hierarchical_cache_subpath(sparse) == Path(
        "https/conda.anaconda.org/conda-forge/linux-64"
    )


def test_hierarchical_cache_subpath_normalizes_port_separator():
    """Per mamba 2.6.0, a port's ``:`` is escaped to ``_``."""
    link = {"url": "http://localhost:8000/mychannel/noarch/foo-1.0-bld.conda"}
    assert hierarchical_cache_subpath(link) == Path(
        "http/localhost_8000/mychannel/noarch"
    )


def test_hierarchical_cache_subpath_strips_credentials():
    """Mamba feeds ``remove_secrets_and_login_credentials`` *before* path
    normalization, so userinfo and ``/t/<token>/`` segments must not appear
    in the cache path. Otherwise the fallback misses authenticated channels
    even when mamba cached the package correctly."""
    with_userinfo = {
        "url": "https://user:secret@conda.example.com/private/linux-64/foo.conda"
    }
    assert hierarchical_cache_subpath(with_userinfo) == Path(
        "https/conda.example.com/private/linux-64"
    )
    with_token = {
        "url": "https://conda.anaconda.org/t/aa-bbb-ccc/private/linux-64/foo.conda"
    }
    assert hierarchical_cache_subpath(with_token) == Path(
        "https/conda.anaconda.org/private/linux-64"
    )


# ---------------------------------------------------------------------------
# record_matches_link
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# candidate_record_paths and get_repodata_record
# ---------------------------------------------------------------------------
