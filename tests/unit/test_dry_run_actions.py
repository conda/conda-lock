"""Tests for rich LINK synthesis, sparse LINK verification and metadata warnings."""

from __future__ import annotations

from pathlib import Path

import pytest

from conda_lock.solver.dry_run import (
    link_action_as_fetch,
)
from tests.support.fixtures import (
    CONDA_LINK_ACTION,
    MAMBA_26_LINK_ACTION,
    MICROMAMBA_1_5_LINK_ACTION,
)


TESTS_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# link_action_as_fetch
# ---------------------------------------------------------------------------


def test_link_action_as_fetch_uses_link_metadata():
    """Mamba-family solvers put every FetchAction field in LINK; reuse it directly."""
    fetch = link_action_as_fetch(MAMBA_26_LINK_ACTION)
    assert fetch is not None
    assert fetch["url"] == MAMBA_26_LINK_ACTION["url"]
    assert fetch["sha256"] == MAMBA_26_LINK_ACTION["sha256"]
    assert fetch["depends"] == MAMBA_26_LINK_ACTION["depends"]
    assert fetch["constrains"] == MAMBA_26_LINK_ACTION["constrains"]


def test_link_action_as_fetch_returns_none_for_sparse_link():
    """Older conda's LINK actions are sparse and need a disk lookup."""
    sparse = {
        "base_url": "https://conda.anaconda.org/conda-forge",
        "channel": "conda-forge",
        "dist_name": "zlib-1.3.2-h25fd6f3_2",
        "name": "zlib",
        "platform": "linux-64",
        "version": "1.3.2",
    }
    assert link_action_as_fetch(sparse) is None


def test_link_action_as_fetch_accepts_micromamba_1_5_link():
    """The rich-LINK fast path is not a mamba 2.6 feature: micromamba
    1.5.12 already emits the full repodata record in LINK (verified by
    capture; see ``tests/support/fixtures.py``)."""
    fetch = link_action_as_fetch(MICROMAMBA_1_5_LINK_ACTION)
    assert fetch is not None
    assert fetch["url"] == MICROMAMBA_1_5_LINK_ACTION["url"]
    assert fetch["sha256"] == MICROMAMBA_1_5_LINK_ACTION["sha256"]
    assert fetch["depends"] == MICROMAMBA_1_5_LINK_ACTION["depends"]


def test_link_action_as_fetch_rejects_real_conda_link():
    """conda (captured: 26.5.2) and the Python ``mamba`` 1.x CLI emit
    the sparse conda-meta LINK shape with no dependency or identity
    fields; the fast path must decline so the disk fallback runs."""
    assert link_action_as_fetch(CONDA_LINK_ACTION) is None


def test_link_action_as_fetch_requires_depends_field():
    """A LINK without ``depends`` would silently erase dependencies if we
    synthesized; reject it and force the disk fallback instead."""
    no_depends = {k: v for k, v in MAMBA_26_LINK_ACTION.items() if k != "depends"}
    assert link_action_as_fetch(no_depends) is None
    null_depends = {**MAMBA_26_LINK_ACTION, "depends": None}
    assert link_action_as_fetch(null_depends) is None
    # Present but not a list (e.g. a stray string) is equally unusable.
    non_list_depends = {**MAMBA_26_LINK_ACTION, "depends": "__glibc >=2.17"}
    assert link_action_as_fetch(non_list_depends) is None


@pytest.mark.parametrize(
    "missing", ["md5", "url", "fn", "subdir", "channel", "version", "name", "timestamp"]
)
def test_link_action_as_fetch_requires_identity_field(missing: str):
    """Identity-bearing fields are mandatory for synthesis."""
    partial = {k: v for k, v in MAMBA_26_LINK_ACTION.items() if k != missing}
    assert link_action_as_fetch(partial) is None


# ---------------------------------------------------------------------------
# reconstruct_fetch_actions_in_place integration
# ---------------------------------------------------------------------------
