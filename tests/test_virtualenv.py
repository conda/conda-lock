import os
import sys

from pathlib import Path
from textwrap import dedent

import pytest

from conda_lock._vendor.poetry.repositories import RepositoryPool
from conda_lock._vendor.poetry.utils.isolated_build import isolated_builder


def test_isolated_build_with_virtualenv_21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Build metadata offline without seeding packages or deprecated options."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    for name in tuple(os.environ):
        if name.startswith("VIRTUALENV_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(
        "VIRTUALENV_CONFIG_FILE", str(tmp_path / "missing-virtualenv.ini")
    )
    monkeypatch.setenv("VIRTUALENV_APP_DATA", str(tmp_path / "app-data"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[build-system]\nrequires = []\nbuild-backend = "backend"\n'
        'backend-path = ["."]\n'
    )
    (project / "backend.py").write_text(
        dedent(
            """\
            import importlib.util
            from pathlib import Path

            def get_requires_for_build_wheel(config_settings=None):
                return []

            def prepare_metadata_for_build_wheel(directory, config_settings=None):
                for name in ("pip", "setuptools", "wheel"):
                    if importlib.util.find_spec(name) is not None:
                        raise RuntimeError(f"Unexpected seeded package: {name}")
                name = "fixture-1.0.dist-info"
                path = Path(directory) / name
                path.mkdir()
                (path / "METADATA").write_text(
                    "Metadata-Version: 2.1\\nName: fixture\\nVersion: 1.0\\n"
                )
                return name
            """
        )
    )

    with isolated_builder(
        project, python_executable=Path(sys.executable), pool=RepositoryPool()
    ) as builder:
        metadata = Path(builder.metadata_path(str(tmp_path / "metadata")))
        assert "Name: fixture" in (metadata / "METADATA").read_text()

    # Catch deprecated virtualenv options without rejecting unrelated warnings.
    for record in caplog.records:
        if record.name.startswith("virtualenv."):
            assert "DEPRECATION" not in record.getMessage()
