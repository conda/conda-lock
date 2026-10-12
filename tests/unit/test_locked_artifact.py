import json

from pathlib import Path

import pytest

from conda_lock.conda_solver import fake_conda_environment
from conda_lock.lockfile.v2prelim.models import HashModel, LockedDependency
from conda_lock.solver.repodata_cache import (
    record_matches_link,
    record_validation_error,
)


@pytest.mark.parametrize("subdir", ["linux-64", "noarch"])
def test_carried_artifact_preserves_subdir_and_decodes_epoch(subdir):
    filename = "epoch-package-1!2.0-0.conda"
    url = f"https://example.org/c/{subdir}/epoch-package-1%212.0-0.conda"
    dependency = LockedDependency(
        name="epoch-package",
        version="1!2.0",
        manager="conda",
        platform="linux-64",
        dependencies={"child": ">=1"},
        url=url,
        hash=HashModel(md5="0" * 32),
        categories={"dev"},
    )
    fetch = dependency.to_fetch_action()
    assert fetch["url"] == url
    assert fetch["fn"] == filename
    assert fetch["subdir"] == subdir
    assert fetch["channel"] == f"https://example.org/c/{subdir}"
    assert record_validation_error(fetch) is None
    with fake_conda_environment([dependency], platform="linux-64") as prefix:
        path = Path(prefix) / "conda-meta/epoch-package-1!2.0-0.json"
        record = json.loads(path.read_text())
        assert record["fn"] == filename
        assert record["url"] == url
        assert record["subdir"] == subdir
        assert record_matches_link(fetch, record) == (True, None)
