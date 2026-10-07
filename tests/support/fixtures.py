"""Captured solver-output fixtures shared across test modules.

The LINK-action literals below are field-for-field captures from
real solver dryruns (the sibling ``tests/test-mamba-fixtures/``
JSON files hold the full payloads; the commit introducing them
documents the capture procedure). They live in a single module so
a schema change in ``LinkAction`` / ``FetchAction`` ripples
through one place instead of silently diverging across files.

The exported ``MAMBA_26_LINK_ACTION`` is wrapped in a
``MappingProxyType`` so a careless test mutation (for example,
``MAMBA_26_LINK_ACTION["depends"] = []`` to simulate corruption)
fails loudly at the assignment site instead of silently corrupting
fixture state for every later test in the same process. Tests
that need a mutated copy spread the mapping (``{**MAMBA_26_LINK_ACTION,
"depends": []}``) or pass it through ``dict(...)``; both produce
fresh dicts.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


# Fields exactly as a real ``mamba 2.6.0`` dryrun emits them in
# ``LINK``. Captured by running ``mamba create --dry-run --json
# libzlib`` against a populated ``CONDA_PKGS_DIRS``.
#
# Empirically the same key set (and, except for ``channel``, the same
# values) is emitted by every micromamba we have captured: 1.5.12,
# 2.0.8, 2.1.1, 2.5.0, 2.6.0, and 2.8.1. The rich-LINK shape is a
# mamba-2.x/micromamba family trait, not a 2.6.0 feature; see
# ``CONDA_LINK_ACTION`` below for the sparse shape that conda and the
# Python ``mamba`` 1.x CLI emit instead.
MAMBA_26_LINK_ACTION: Mapping = MappingProxyType(
    {
        "build": "h25fd6f3_2",
        "build_number": 2,
        "build_string": "h25fd6f3_2",
        "channel": "conda-forge",
        # Lists rather than tuples: the production fast path
        # checks ``isinstance(depends, list)`` to reject sparse
        # LINK actions, and tuples would silently fail that check. Inner-list mutation is technically still
        # possible -- the MappingProxy only locks the top level --
        # but the common footgun (``ACTION["depends"] = []`` to
        # simulate corruption) hits the read-only barrier.
        "constrains": ["zlib 1.3.2 *_2"],
        "depends": ["__glibc >=2.17,<3.0.a0"],
        "fn": "libzlib-1.3.2-h25fd6f3_2.conda",
        "license": "Zlib",
        "md5": "d87ff7921124eccd67248aa483c23fec",
        "name": "libzlib",
        "sha256": "55044c403570f0dc26e6364de4dc5368e5f3fc7ff103e867c487e2b5ab2bcda9",
        "size": 63629,
        "subdir": "linux-64",
        "timestamp": 1774072609,
        "track_features": "",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda",
        "version": "1.3.2",
    }
)


# Fields exactly as a real ``micromamba 1.5.12`` dryrun emits them in
# ``LINK``. Identical to ``MAMBA_26_LINK_ACTION`` except that 1.5.x
# fills ``channel`` with the full channel URL where 2.x uses the bare
# channel name. Kept as an independent literal (rather than a spread
# of the 2.6 fixture) so a schema drift in either capture fails a test
# instead of being silently masked.
MICROMAMBA_1_5_LINK_ACTION: Mapping = MappingProxyType(
    {
        "build": "h25fd6f3_2",
        "build_number": 2,
        "build_string": "h25fd6f3_2",
        "channel": "https://conda.anaconda.org/conda-forge/linux-64",
        "constrains": ["zlib 1.3.2 *_2"],
        "depends": ["__glibc >=2.17,<3.0.a0"],
        "fn": "libzlib-1.3.2-h25fd6f3_2.conda",
        "license": "Zlib",
        "md5": "d87ff7921124eccd67248aa483c23fec",
        "name": "libzlib",
        "sha256": "55044c403570f0dc26e6364de4dc5368e5f3fc7ff103e867c487e2b5ab2bcda9",
        "size": 63629,
        "subdir": "linux-64",
        "timestamp": 1774072609,
        "track_features": "",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.2-h25fd6f3_2.conda",
        "version": "1.3.2",
    }
)


# Fields exactly as a real ``conda 26.5.2`` dryrun emits them in
# ``LINK``: the sparse conda-meta shape, with no dependency or
# artifact-identity information. The Python ``mamba`` 1.x CLI
# (captured: mamba 1.5.12 over conda 24.11.3) emits the identical
# key set, since it renders its plan through conda's JSON printer.
# These solvers are why the ``repodata_record.json`` disk-fallback
# path exists.
CONDA_LINK_ACTION: Mapping = MappingProxyType(
    {
        "base_url": "https://conda.anaconda.org/conda-forge",
        "build_number": 2,
        "build_string": "h25fd6f3_2",
        "channel": "conda-forge",
        "dist_name": "libzlib-1.3.2-h25fd6f3_2",
        "name": "libzlib",
        "platform": "linux-64",
        "version": "1.3.2",
    }
)
