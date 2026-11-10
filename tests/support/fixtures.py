"""Solver LINK fixtures; full captures are in tests/test-mamba-fixtures.

MappingProxyType protects top-level keys only. Copy before replacing fields,
and deep-copy before changing nested lists.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


# Fields exactly as a real ``mamba 2.6.0`` dryrun emits them in
# ``LINK``. Captured by running ``mamba create --dry-run --json
# libzlib`` against a populated ``CONDA_PKGS_DIRS``.
#
MAMBA_26_LINK_ACTION: Mapping[str, Any] = MappingProxyType(
    {
        "build": "h25fd6f3_2",
        "build_number": 2,
        "build_string": "h25fd6f3_2",
        "channel": "conda-forge",
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
MICROMAMBA_1_5_LINK_ACTION: Mapping[str, Any] = MappingProxyType(
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
CONDA_LINK_ACTION: Mapping[str, Any] = MappingProxyType(
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
