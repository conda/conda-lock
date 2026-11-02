# Support scripts

[vendoring](https://github.com/pradyunsg/vendoring) is a tool for vendoring dependencies into `conda_lock/_vendor`, namely Poetry and Conda.
We have to do some additional cleanup due to the fact that the packages we vendor have vendored dependencies of their own.
`rerun_vendoring.sh` makes that cleanup reproducible.
See the `[tool.vendoring]` section of `pyproject.toml` for details.

## Regenerating vendored sources

Use `vendoring==1.4.0` and `dos2unix`, then run `bash scripts/rerun_vendoring.sh`
from the repository root. The script fetches the versions in
`conda_lock/_vendor/vendor.txt`.
