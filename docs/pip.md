# pip support

conda-lock has experimental support to allow locking mixed conda/pip environments.

Declare `pip` in your conda dependencies if you need it. A `pip:` section in an
environment file also requests the installer automatically. Requesting only
Python does not request pip; a package that actually depends on pip still brings
it in as a dependency.

This changes previous Mamba behavior: conda-lock now disables Mamba's automatic
pip injection, as it already does for conda. Existing lockfiles retain their
packages when installed. Fresh locks and selective updates apply this policy
using the source specification and verified channel dependencies, even if you
switch solver versions. An older lockfile's inclusion of pip does not establish
that it was requested.

Mamba versions affected by [mamba#4422](https://github.com/mamba-org/mamba/issues/4422)
still inject pip for an explicit Python request despite this setting. Conda-lock
removes verified unrequested pip and dependencies needed exclusively by it. It
preserves shared dependencies and genuine pip requirements with their proper
categories. This normalization applies before pinning old packages during updates
and after a successful solve.

Retaining explicitly requested or genuinely required pip does not itself require
another channel query. Before omitting unrequested pip, or deciding whether a
Python-to-pip dependency is genuine, conda-lock verifies the plan against channel
metadata. Offline Mamba cannot perform that verification for remote channels;
see [offline verification limits](basic_usage.md#updating-packages).

If injected pip makes the solver itself fail, post-processing cannot repair that
failure. Use a Mamba version with the upstream fix or use conda for that solve.

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
