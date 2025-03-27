# Support scripts

Currently we have just one script: `rerun_vendoring.sh`.

[vendoring](https://github.com/pradyunsg/vendoring) is a tool for vendoring dependencies into `conda_lock/_vendor`, namely Poetry and Conda.
We have to do some additional cleanup due to the fact that the packages we vendor have vendored dependencies of their own.
This script makes that cleanup reproducible.
See the `[tool.vendoring]` section of `pyproject.toml` for details.
