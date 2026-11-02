# Support scripts

[vendoring](https://github.com/pradyunsg/vendoring) is a tool for vendoring dependencies into `conda_lock/_vendor`, namely Poetry and Conda.
We have to do some additional cleanup due to the fact that the packages we vendor have vendored dependencies of their own.
`rerun_vendoring.sh` makes that cleanup reproducible.
See the `[tool.vendoring]` section of `pyproject.toml` for details.

## Regenerating vendored sources

Use `vendoring==1.4.0` and `dos2unix`, then run `bash scripts/rerun_vendoring.sh`
from the repository root. The script fetches the versions in
`conda_lock/_vendor/vendor.txt`, including their matching licenses.

Keep source changes in `scripts/vendor_patches/`, not in generated files under
`conda_lock/_vendor/`. Vendoring applies these patches to upstream sources before
rewriting imports. Regenerate and commit the resulting files separately.

`poetry-dulwich-1.patch` keeps Poetry 2.0.1 compatible with Dulwich 1.2.20. It
backports the Git backend changes from Poetry PRs
[#10492](https://github.com/python-poetry/poetry/pull/10492),
[#10674](https://github.com/python-poetry/poetry/pull/10674),
[#10719](https://github.com/python-poetry/poetry/pull/10719), and
[#10799](https://github.com/python-poetry/poetry/pull/10799).
Remove the patch when updating to a Poetry release containing these fixes.
