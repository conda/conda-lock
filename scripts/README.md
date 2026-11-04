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

Poetry 2.5.1 includes the Dulwich compatibility changes previously backported
in `poetry-dulwich-1.patch`. Keep `poetry-source-cache.patch` to avoid reusing
checkouts created before the Dulwich 1.2.20 parser fix.
