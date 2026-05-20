"""Shared test fixtures.

Real ``mamba 2.6.0`` dryrun payloads were copy-pasted across
multiple test modules during the architectural split. Promote
those literals here so a future schema change in
``LinkAction`` / ``FetchAction`` ripples through one place
instead of silently diverging across files.

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
MAMBA_26_LINK_ACTION: Mapping = MappingProxyType(
    {
        "build": "h25fd6f3_2",
        "build_number": 2,
        "build_string": "h25fd6f3_2",
        "channel": "conda-forge",
        # Lists rather than tuples: production code in
        # ``link_action_as_fetch`` does ``isinstance(depends, list)``
        # to reject sparse-LINK actions, and tuples would silently
        # fail that check. Inner-list mutation is technically still
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
