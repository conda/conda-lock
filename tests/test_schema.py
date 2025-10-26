from pathlib import Path

from conda_lock.lockfile.v1 import models as v1_models


def test_schema_is_up_to_date(tmp_path):
    original = (Path(v1_models.__file__).parent / v1_models.SCHEMA_FILENAME).read_text()
    v1_models.generate_json_schema(tmp_path)
    maybe_modified = (tmp_path / v1_models.SCHEMA_FILENAME).read_text()
    assert original == maybe_modified
