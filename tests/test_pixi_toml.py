import shlex

from pathlib import Path

import pytest

from click.testing import CliRunner

from conda_lock.conda_lock import main


GENERATE_PIXI_TOML_COMMAND = """
conda-lock render-lock-spec --kind=pixi.toml --stdout \
  --file=environments/dev-environment.yaml \
  --file=pyproject.toml \
  --pixi-project-name=conda-lock \
  --editable conda-lock=.
""".strip()


def test_generate_pixi_toml() -> None:
    """Ensure our generated pixi.toml agrees with the output of the command above.

    If this test fails, then you may need to update pixi.toml with the output
    of the above command.
    """
    tests_dir = Path(__file__).parent
    project_dir = tests_dir.parent
    expected = (project_dir / "pixi.toml").read_text()

    runner = CliRunner(mix_stderr=False)
    args = shlex.split(GENERATE_PIXI_TOML_COMMAND)[1:]
    with pytest.warns(UserWarning) as record:
        result = runner.invoke(main, args)
    assert result.exit_code == 0

    assert result.stdout == expected
    # There are currently python_version environment markers in pyproject.toml
    # that we drop with a warning since they can't be converted.
    warning1 = """Marker 'python_version < "3.10"' contains environment markers: {'python_version'}."""
    warning2 = """Marker 'python_version < "3.11"' contains environment markers: {'python_version'}."""
    # Pip deps with extras can't be converted to Conda deps.
    warning3 = """Extras not supported in Conda dep name='cachecontrol' manager='conda' category='main' extras=['filecache'] markers=None version='<0.15.0,>=0.14.0'"""
    for warning in [warning1, warning2, warning3]:
        assert any(warning in str(r.message) for r in record)
    assert len(set(str(r.message) for r in record)) == 3
