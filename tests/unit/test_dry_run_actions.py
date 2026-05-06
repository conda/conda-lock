"""Unit tests for ``conda_lock.solver.dry_run``.

Solver-output normalization concerns: rich-LINK to FETCH synthesis
(mamba 2.6.0 fast path) and disk-fallback reconstruction for sparse
LINKs (older mamba/conda).

The dict literals model real mamba/conda JSON output, which is
dynamically typed at the boundary -- pretending they were TypedDicts
at every call site would be a wall of casts without catching real
bugs. The relevant arg-type checks are disabled file-wide.
"""

# mypy: disable-error-code="arg-type,comparison-overlap"

from __future__ import annotations

import json

from pathlib import Path

import pytest

from conda_lock.solver.dry_run import link_action_as_fetch
from tests.support.fixtures import MAMBA_26_LINK_ACTION as _MAMBA_26_LINK_ACTION


TESTS_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# link_action_as_fetch
# ---------------------------------------------------------------------------


def test_link_action_as_fetch_uses_link_metadata():
    """Mamba 2.6.0 puts every FetchAction field in LINK; reuse it directly."""
    fetch = link_action_as_fetch(_MAMBA_26_LINK_ACTION)
    assert fetch is not None
    assert fetch["url"] == _MAMBA_26_LINK_ACTION["url"]
    assert fetch["sha256"] == _MAMBA_26_LINK_ACTION["sha256"]
    assert fetch["depends"] == _MAMBA_26_LINK_ACTION["depends"]
    assert fetch["constrains"] == _MAMBA_26_LINK_ACTION["constrains"]


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


def test_link_action_as_fetch_requires_depends_field():
    """A LINK without ``depends`` would silently erase dependencies if we
    synthesized; reject it and force the disk fallback instead."""
    no_depends = {k: v for k, v in _MAMBA_26_LINK_ACTION.items() if k != "depends"}
    assert link_action_as_fetch(no_depends) is None
    null_depends = {**_MAMBA_26_LINK_ACTION, "depends": None}
    assert link_action_as_fetch(null_depends) is None


@pytest.mark.parametrize(
    "missing", ["md5", "url", "fn", "subdir", "channel", "version", "name", "timestamp"]
)
def test_link_action_as_fetch_requires_identity_field(missing: str):
    """Identity-bearing fields are mandatory for synthesis."""
    partial = {k: v for k, v in _MAMBA_26_LINK_ACTION.items() if k != missing}
    assert link_action_as_fetch(partial) is None


def test_link_action_as_fetch_replays_real_mamba_2_6_0_dryrun():
    """Replay the captured mamba 2.6.0 LINK-only dryrun JSON: every LINK
    that mamba emits should synthesize a FETCH directly from its own
    fields, with nothing missing."""
    fixture = (
        TESTS_DIR / "test-mamba-fixtures" / "dryrun-mamba-2.6.0-linux-64-zlib.json"
    )
    dryrun = json.loads(fixture.read_text())
    assert len(dryrun["actions"]["LINK"]) >= 1
    assert dryrun["actions"].get("FETCH", []) == []
    for link in dryrun["actions"]["LINK"]:
        fetch = link_action_as_fetch(link)
        assert fetch is not None, link["name"]
        assert fetch["url"] == link["url"]
        assert fetch["sha256"] == link["sha256"]
        assert fetch["md5"] == link["md5"]
        assert fetch["depends"] == link["depends"]
        assert fetch["subdir"] == link["subdir"]
