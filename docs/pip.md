# pip support

conda-lock has experimental support to allow locking mixed conda/pip environments.

Declare `pip` in your conda dependencies if you need it. A `pip:` section in an
environment file also requests the installer automatically. A package that
actually depends on pip still brings it in as a dependency.

Conda and Mamba normally add pip when you request Python. Conda-lock configures
them to disable this automatic addition. Some solver versions ignore that
setting; if the solver still selects pip, conda-lock retains it and its
dependencies. Declare pip if you need it, so its availability does not depend on
the solver's injection behavior. Installing an existing lockfile keeps its
packages unchanged.

If Mamba reports a Python/pip conflict even though you did not request pip, try
the same command with `--conda conda --no-mamba --no-micromamba`. Conda-lock
cannot repair a solve that fails before returning a package plan.

## Usage with environment.yaml

`conda-lock` can lock the `dependencies.pip` section of
[environment.yml](https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html#create-env-file-manually), using a vendored copy of [Poetry's](https://python-poetry.org) dependency solver.

```{.yaml title="environment.yml"}
channels:
  - conda-forge
dependencies:
  - python >=3.9
  - requests
  - pip:
    - some_pip_only_library
```

If in this case `some_pip_only_library` depends on `requests` that dependency will be met by
conda and the version will be constrained to what the conda solver determines.

We recommend avoiding the `--kind=explicit` flag when there are `pip`
dependencies. Most tools (except for `conda-lock install`) do not recognize
`pip` dependencies from explicit lockfiles, so they may be silently ignored.
The default lockfile format explicitly supports pip dependencies.

## Usage with pyproject.toml

If a dependency refers directly to a URL rather than a package name and version,
`conda-lock` will assume it is pip-installable, e.g.:

```{.toml title="pyproject.toml"}
[tool.poetry.dependencies]
python = "3.9"
pymage = {url = "https://github.com/MickaelRigault/pymage/archive/v1.0.tar.gz#sha256=11e99c4ea06b76ca7fb5b42d1d35d64139a4fa6f7f163a2f0f9cc3ea0b3c55eb"}
```

Similarly, if a dependency is explicitly marked with `source = "pypi"`, it will
be treated as a `pip` dependency, e.g.:

```{.toml title="pyproject.toml"}
[tool.poetry.dependencies]
python = "3.9"
ampel-ztf = {version = "^0.8.0-alpha.2", source = "pypi"}
```

Alternatively, explicitly providing  `default-non-conda-source = "pip"` in the `[tool.conda-lock]` section will treat all non-conda dependencies -- all dependencies defined outside of `[tool.conda-lock.dependencies]` -- as `pip` dependencies, i.e.:
- Default to `pip` dependencies for `[tool.poetry.dependencies]`, `[project.dependencies]`, etc.
- Default to `conda` dependencies for `[tool.conda-lock.dependencies]`
```toml
[tool.conda-lock]
default-non-conda-source = "pip"
```

In all cases, the dependencies of `pip`-installable packages will also be
installed with `pip`, unless they were already requested by a `conda`
dependency.
