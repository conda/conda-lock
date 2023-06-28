"""This is a test module to ensure that the various changes we've made over time don't
break the functionality of conda-lock.  This is a regression test suite."""

import textwrap

from pathlib import Path

import pytest

from conda_lock.conda_lock import run_lock


@pytest.mark.parametrize("platform", ["linux-64", "osx-64", "osx-arm64"])
def test_pr_436(
    mamba_exe: Path, monkeypatch: "pytest.MonkeyPatch", tmp_path: Path, platform: str
) -> None:
    """Ensure that we can lock this environment which requires more modern osx path selectors"""
    spec = textwrap.dedent(
        """
        channels:
        - conda-forge
        dependencies:
        - pip:
            - drjit
        """
    )
    (tmp_path / "environment.yml").write_text(spec)
    monkeypatch.chdir(tmp_path)
    run_lock([tmp_path / "environment.yml"], conda_exe=mamba_exe, platforms=[platform])
