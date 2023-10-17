# pyproject.toml

Since `pyproject.toml` files are commonly used by python packages it can be desirable to create a lock
file directly from those dependencies to single-source a package's dependencies.

This makes use of some conda-forge infrastructure ([pypi-mapping][mapping]) to do a lookup of the PyPI
package name to a corresponding conda package name (e.g. `docker` -> `docker-py`).  In cases where there
is no lookup for the package it assumes that the PyPI name, and the conda name are the same.

## Features

### dependency resolution

=== "poetry"

    ```{.toml title="pyproject.toml"}
    [tool.poetry.dependencies]
    requests = "^2.13.0"
    toml = ">=0.10"

    [tool.poetry.dev-dependencies]
    pytest = ">=5.1.0"

    [build-system]
    requires = ["poetry>=0.12"]
    build-backend = "poetry.masonry.api"
    ```

=== "pep621 (flit, pdm)"
    ```{.toml title="pyproject.toml"}
    [project]
    dependencies = [
        "requests ^2.13.0",
        "toml >=0.10",
    ]
    [project.optional-dependencies]
    test = [
        "pytest >=5.1.0",
    ]
    ```

This will create a conda-lock specification with

**main**

    requests ^2.13.0"
    toml >=0.10

**dev**

    pytest >=5.1.0

!!! note ""

    PDM also has support for
    [development dependencies not listed in distribution metadata](https://pdm.fming.dev/pyproject/tool-pdm/#development-dependencies).
    Any dependency found in that section will be added to the `dev` category.
    This behavior is experimental and may change in the future.

### pure pip dependencies

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

### Lock only conda-lock dependencies

To lock only dependencies specified under `[tool.conda-lock]` (i.e. skipping all dependencies specified elsewhere), explicitly provide `skip-non-conda-lock = true` in `[tool.conda-lock]` section, e.g.:
```toml
[tool.conda-lock]
skip-non-conda-lock = true
```

### Extras

If your pyproject.toml file contains optional dependencies/extras these can be referred to by using the `--extras` flag

=== "poetry"

    ```{.toml title="pyproject.toml"}

    [tool.poetry.dependencies]
    mandatory = "^1.0"
    psycopg2 = { version = "^2.7", optional = true }
    mysqlclient = { version = "^1.3", optional = true }

    [tool.poetry.extras]
    mysql = ["mysqlclient"]
    pgsql = ["psycopg2"]
    ```

=== "pep621 (flit, pdm)"

    ```{.toml title="pyproject.toml"}
    # pyproject.toml

    [project]
    dependencies = [
        "mandatory ^1.0",
    ]

    [project.optional-dependencies]
    mysql = ["mysqlclient ^1.3"]
    pgsql = ["psycopg2 ^2.7"]
    ```

These can be referenced as follows

```sh
conda-lock --extra mysql --extra pgsql -f pyproject.toml
```

When generating lockfiles that make use of extras it is recommended to make use of `--filename-template` covered [here](#file-naming).

!!! note ""

    By default conda-lock will attempt to solve for *ALL* extras it discovers in sources.  This allows you to render explicit locks with subsets
    of extras.

    However this does make the assumption that your extras can all be installed in conjunction with each other.  If you want extras filtering
    to happen at the solve stage use the flag `--filter-extras`

    ```sh
    conda-lock --extra incompatiblea --filter-extras -f pyproject.toml
    ```

## Poetry specific supported features

### Dependency groups

conda-lock can map [dependency groups](https://python-poetry.org/docs/master/managing-dependencies/#dependency-groups) to
categories similar to how extras are handled.

```{.toml title="pyproject.toml"}
[tool.poetry.group.docs.dependencies]
mkdocs = "*"
```

!!! note ""

    By default *ALL* dependency groups are included.  Depdency groups that have the same name as an extra are fused.
    These can be filtered out / included using the same flags as for extras.

    ```
    conda-lock --extra docs --filter-extras -f pyproject.toml
    ```

## Extensions

As the `pyproject.toml` format is not really designed for conda there are a few extensions we support in the
toml file.  All extensions live in the `tool.conda-lock` section.

### Channels

```{.toml title="pyproject.toml"}
[tool.conda-lock]
channels = [
    'conda-forge', 'defaults'
]
```

### Platforms

Like in [environment.yml](/src_environment_yml#platform-specification), you can specify default platforms to target:

```{.toml title="pyproject.toml"}
[tool.conda-lock]
platforms = [
    'osx-arm64', 'linux-64'
]
```

### Extra conda dependencies

Since in a `pyproject.toml` all the definitions are python dependencies if you need
to specify some non-python dependencies as well this can be accomplished by adding
the following sections to the `pyproject.toml`

```{.toml title="pyproject.toml"}
[tool.conda-lock.dependencies]
sqlite = ">=3.34"
```

### Force pypi dependencies

While it is with poetry, it is not possible to indicate a package's source in a `pyproject.toml` which follows PEP621.

In that case, it is possible to force resolving a dependency as a pip dependency by indicating it in the same `pyproject.toml` section.

This is useful in particular for packages that are not present on conda channels.

```{.toml title="pyproject.toml"}
[tool.conda-lock.dependencies]
numpy = {source = "pypi"}
```

[mapping]: https://github.com/regro/cf-graph-countyfair/blob/master/mappings/pypi/grayskull_pypi_mapping.yaml
