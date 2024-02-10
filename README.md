# conda-lock

[![GitHub deployments](https://img.shields.io/github/deployments/conda/conda-lock/github-pages?label=docs&style=for-the-badge)](https://conda.github.io/conda-lock/)
[![PyPI](https://img.shields.io/pypi/v/conda-lock?style=for-the-badge)](https://pypi.org/project/conda-lock/)
[![Conda](https://img.shields.io/conda/v/conda-forge/conda-lock?style=for-the-badge)](https://github.com/conda-forge/conda-lock-feedstock)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?style=for-the-badge)](https://results.pre-commit.ci/latest/github/conda/conda-lock/main)
[![codecov](https://img.shields.io/codecov/c/github/conda/conda-lock/main?style=for-the-badge)](https://codecov.io/gh/conda/conda-lock)

Conda lock is a lightweight library that can be used to generate fully reproducible lock files for [conda][conda]
environments.

It does this by performing a conda solve for each platform you desire a lockfile for.

This also has the added benefit of acting as an external pre-solve for conda as the lockfiles it generates
results in the conda solver *not* being invoked when installing the packages from the generated lockfile.

## Why?

Conda [`environment.yml`][envyaml] files are very useful for defining desired environments but there are times when we want to
be able to EXACTLY reproduce an environment by just installing and downloading the packages needed.

This is particularly handy in the context of a gitops style setup where you use conda to provision environments in
various places.

## Installation

Use *one* of the following commands:

```bash
pipx install conda-lock
condax install conda-lock
pip install conda-lock
conda install --channel=conda-forge --name=base conda-lock
mamba install --channel=conda-forge --name=base conda-lock
```

The first two options are recommended since they install conda-lock into an isolated environment. (Otherwise there is a risk of dependency conflicts.)

## Contributing

If you would like to contribute to conda-lock, please refer to the [Contributing Guide](CONTRIBUTING.md) for instructions on how to set up your development environment.

## Basic usage

```bash
# generate a multi-platform lockfile
conda-lock -f environment.yml -p osx-64 -p linux-64

# optionally, update the previous solution, using the latest version of
# pydantic that is compatible with the source specification
conda-lock --update pydantic

# create an environment from the lockfile
conda-lock install [-p {prefix}|-n {name}]

# alternatively, render a single-platform lockfile and use conda command directly
conda-lock render -p linux-64
conda create -n my-locked-env --file conda-linux-64.lock
```

### Pre 1.0 compatible usage (explicit per platform locks)

If you were making use of conda-lock before the 1.0 release that added unified lockfiles
you can still get that behaviour by making use of the `explicit` output kind.

```bash
conda-lock --kind explicit -f environment.yml
```

## Advanced usage

### File naming

By default, `conda-lock` store its output in `conda-lock.yml` in the current
working directory. This file will also be used by default for render, install,
and update operations. You can supply a different filename with e.g.

```bash
conda-lock --lockfile superspecial.conda-lock.yml
```

Rendered `explicit` and `env` lockfiles will be named as `"conda-{platform}.lock"` and `"conda-{platform}.lock.yml` respectively by default.

If you want to override that call conda-lock as follows.

```bash
conda-lock -k explicit --filename-template "specific-{platform}.conda.lock"
```

### Compound specification

Conda-lock will build a spec list from several files if requested.

```bash
conda-lock -f base.yml -f specific.yml -p linux-64 -k explicit --filename-template "specific-{platform}.lock"
````

In this case all dependencies are combined, and the ordered union of all `channels` is used as the final
specification.

This works for all supported file types.

#### channel overrides

You can override the channels that are used by conda-lock in case you need to override the ones specified in
an [environment.yml][envyaml]

```bash
conda-lock -c conda-forge -p linux-64
```

#### platform specification

You may specify the platforms you wish to target by default directly in an [environment.yml][envyaml] using the (nonstandard) `platforms` key:

```yaml
# environment.yml
channels:
  - conda-forge
dependencies:
  - python=3.9
  - pandas
platforms:
  - linux-64
  - osx-64
  - win-64
  - osx-arm64  # For Apple Silicon, e.g. M1/M2
  - linux-aarch64  # aka arm64, use for Docker on Apple Silicon
  - linux-ppc64le
```

If you specify target platforms on the command line with `-p`, these will
override the values in the environment specification. If neither `platforms` nor
`-p` are provided, `conda-lock` will fall back to a default set of platforms.

#### default category

You can may wish to split your dependencies into separate files for better
organization, e.g. a `environment.yml` for production dependencies and a
`dev-environment.yml` for development dependencies. You can assign all the
dependencies parsed from a single file to a category using the (nonstandard)
`category` key.

```yaml
# dev-environment.yml
channels:
  - conda-forge
dependencies:
  - pytest
  - mypy=0.910
category: dev
```

The default category is `main`.

### pip support

`conda-lock` can also lock the `dependencies.pip` section of
[environment.yml][envyaml], using a vendored copy of [Poetry's][poetry] dependency solver.

### private pip repositories

Right now `conda-lock` only supports [legacy](https://warehouse.pypa.io/api-reference/legacy.html) pypi repos with basic auth. Most self-hosted repositories like Nexus, Artifactory etc. use this. You can configure private pip repositories in a similar way to channels, for example:

```yaml
channels:
  - conda-forge
pip-repositories:
  - http://$PIP_USER:$PIP_PASSWORD@private-pypi.org/api/pypi/simple
dependencies:
  - python=3.11
  - requests=2.26
  - pip:
    - fake-private-package==1.0.0
```

See [the related docs for private channels](./docs/authenticated_channels.md#what_gets_stored) to understand the rules regarding environment variable substitution.

Alternatively, you can use the `poetry` configuration file format to configure private PyPi repositories. The configuration file should be named `config.toml` and have the following format:

```toml
[repositories.example]
url = "https://username:password@example.repo/simple"
```

The location of this file can be determined with `python -c 'from conda_lock._vendor.poetry.locations import CONFIG_DIR; print(CONFIG_DIR)'`

Private repositories will be used in addition to `pypi.org`. For projects using `pyproject.toml`, it is possible to [disable `pypi.org` entirely](#disabling-pypiorg).

### --dev-dependencies/--no-dev-dependencies

By default conda-lock will include dev dependencies in the specification of the lock (if the files that the lock
is being built from support them).  This can be disabled easily

```bash
conda-lock --no-dev-dependencies -f ./recipe/meta.yaml
```

### --check-input-hash

Under some situation you may want to run conda lock in some kind of automated way (eg as a precommit) and want to not
need to regenerate the lockfiles if the underlying input specification for that particular lock as not changed.

```bash
conda-lock --check-input-hash -p linux-64
```

When the input_hash of the input files, channels match those present in a given lockfile, that lockfile will not be regenerated.

### --strip-auth, --auth and --auth-file

By default `conda-lock` will leave basic auth credentials for private conda channels in the lock file. If you wish to strip authentication from the file, provide the `--strip-auth` argument.

```shell
conda-lock --strip-auth -f environment.yml
```

In order to `conda-lock install` a lock file with its basic auth credentials stripped, you will need to create an authentication file in `.json` format like this:

```json
{
  "domain": "username:password"
}
```

If you have multiple channels that require different authentication within the same domain, you can additionally specify the channel like this:

```json
{
  "domain.org/channel1": "username1:password1",
  "domain.org/channel2": "username2:password2"
}
```

You can provide the authentication either as string through `--auth` or as a filepath through `--auth-file`.

```bash
conda-lock install --auth-file auth.json conda-linux-64.lock
```

### --virtual-package-spec

Conda makes use of [virtual packages](https://conda.io/projects/conda/en/latest/user-guide/tasks/manage-virtual.html) that are available at
runtime to gate dependency on system features.  Due to these not generally existing on your local execution platform conda-lock will inject
them into the solution environment with a reasonable guess at what a default system configuration should be.

If you want to override which virtual packages are injected you can create a file like

```yaml
# virtual-packages.yml
subdirs:
  linux-64:
    packages:
      __glibc: "2.17"
      __cuda: "11.4"
  win-64:
    packages:
      __cuda: "11.4"
```

conda-lock will automatically use a `virtual-packages.yml` it finds in the the current working directory.  Alternatively one can be specified
explicitly via the flag.

```bash
conda lock --virtual-package-spec virtual-packages-cuda.yml -p linux-64
```

#### Input hash stability

Virtual packages take part in the input hash so if you build an environment with a different set of virtual packages the input hash will change.
Additionally the default set of virtual packages may be augmented in future versions of conda-lock.  If you desire very stable input hashes
we recommend creating a `virtual-packages.yml` file to lock down the virtual packages considered.

#### ⚠️ in conjunction with micromamba

Micromamba does not presently support some of the overrides to remove all discovered virtual packages, consequently the set of virtual packages
available at solve time may be larger than those specified in your specification.

## Supported file sources

Conda lock supports more than just [environment.yml][envyaml] specifications!

Additionally conda-lock supports [meta.yaml][metayaml] (conda-build)
and `pyproject.toml` (
[flit](https://flit.readthedocs.io/en/latest/), [pdm](https://pdm.fming.dev) and
[poetry](https://python-poetry.org) based).  These do come with some gotchas but
are generally good enough for the 90% use-case.

### meta.yaml

Conda-lock will attempt to make an educated guess at the desired environment spec in a meta.yaml.  This is
not guaranteed to work for complex recipes with many selectors and outputs.  For multi-output recipes, conda-lock
will fuse all the dependencies together.  If that doesn't work for your case fall back to specifying the specification
as an [environment.yml][envyaml]

Since a meta.yaml doesn't contain channel information we make use of the following extra key to specify channels

```yaml
# meta.yaml

extra:
  channels:
    - conda-forge
    - defaults
```

### pyproject.toml

Since `pyproject.toml` files are commonly used by python packages it can be desirable to create a lock
file directly from those dependencies to single-source a package's dependencies.  This makes use of some
conda-forge infrastructure ([pypi-mapping][mapping]) to do a lookup of the PyPI package name to a corresponding
conda package name (e.g. `docker` -> `docker-py`).  In cases where there exists no lookup for the package it assumes that
the PyPI name, and the conda name are the same.

#### Channels

```toml
# pyproject.toml

[tool.conda-lock]
channels = [
    'conda-forge', 'defaults'
]
```

#### Platforms

Like in [environment.yml][envyaml], you can specify default platforms to target:

```toml
# pyproject.toml

[tool.conda-lock]
platforms = [
    'osx-arm64', 'linux-64'
]
```

#### Extras

If your pyproject.toml file contains optional dependencies/extras these can be referred to by using the `--extras` flag

```toml
# pyproject.toml

[tool.poetry.dependencies]
mandatory = "^1.0"
psycopg2 = { version = "^2.7", optional = true }
mysqlclient = { version = "^1.3", optional = true }

[tool.poetry.extras]
mysql = ["mysqlclient"]
pgsql = ["psycopg2"]

```

These can be referened as follows

```shell
conda-lock --extra mysql --extra pgsql -f pyproject.toml
```

When generating lockfiles that make use of extras it is recommended to make use of `--filename-template` covered [here](#file-naming).

##### Filtering extras

 By default conda-lock will attempt to solve for *ALL* extras/categories it discovers in sources.  This allows you to render explicit locks with subset of extras, without needing a new solve.

However this does make the assumption that your extras can all be installed in conjunction with each other.  If you want extras filtering
to happen at the solve stage use the flag `--filter-extras`

```sh
conda-lock --extra incompatiblea --filter-extras -f pyproject.toml
```

#### Extra conda dependencies

Since in a `pyproject.toml` all the definitions are python dependencies if you need
to specify some non-python dependencies as well this can be accomplished by adding
the following sections to the `pyproject.toml`

```toml
# pyproject.toml

[tool.conda-lock.dependencies]
sqlite = ">=3.34"
```

#### pip dependencies

If a dependency refers directly to a URL rather than a package name and version,
`conda-lock` will assume it is pip-installable, e.g.:

```toml
# pyproject.toml
[tool.poetry.dependencies]
python = "3.9"
pymage = {url = "https://github.com/MickaelRigault/pymage/archive/v1.0.tar.gz#sha256=11e99c4ea06b76ca7fb5b42d1d35d64139a4fa6f7f163a2f0f9cc3ea0b3c55eb"}
```

Similarly, if a dependency is explicitly marked with `source = "pypi"`, it will
be treated as a `pip` dependency, e.g.:

```toml
[tool.poetry.dependencies]
python = "3.9"
ampel-ztf = {version = "^0.8.0-alpha.2", source = "pypi"}
```

A dependency will also be treated as a `pip` dependency if explicitly marked with `source = "pypi"` in the `[tool.conda-lock.dependencies]` section, e.g.:

```toml
[tool.conda-lock.dependencies]
ampel-ztf = {source = "pypi"}
```

##### Defaulting non-conda dependency sources to PyPI

Alternatively, the above behavior is defaulted for all dependencies defined outside of `[tool.conda-lock.dependencies]`, i.e.:

- Default to `pip` dependencies for `[tool.poetry.dependencies]`, `[project.dependencies]`, etc.
- Default to `conda` dependencies for `[tool.conda-lock.dependencies]`

by explicitly providing  `default-non-conda-source = "pip"` in the `[tool.conda-lock]` section, e.g.:

```toml
[tool.conda-lock]
default-non-conda-source = "pip"
```

In all cases, the dependencies of `pip`-installable packages will also be
installed with `pip`, unless they were already requested by a `conda`
dependency.

#### Lock only conda-lock dependencies

To lock only dependencies specified under `[tool.conda-lock]` (i.e. skipping all dependencies specified elsewhere), explicitly provide `skip-non-conda-lock = true` in the `[tool.conda-lock]` section, e.g.:

```toml
[tool.conda-lock]
skip-non-conda-lock = true
```

#### Disabling pypi.org

When using private pip repos, it is possible to disable `pypi.org` entirely. This can be useful when using `conda-lock` behind a network proxy that does not allow access to `pypi.org`.

```toml
[tool.conda-lock]
allow-pypi-requests = false
```

## Dockerfile example

In order to use conda-lock in a docker-style context you want to add the lockfile to the
docker container.  In order to refresh the lock file just run `conda-lock` again.

Given a file tree like

```text
  Dockerfile
  environment.yaml
* conda-linux-64.lock
```

You want a dockerfile that is structured something similar to this

```Dockerfile
# Dockerfile

# Build container
FROM continuumio/miniconda:latest as conda

ADD conda-linux-64.lock /locks/conda-linux-64.lock
RUN conda create -p /opt/env --copy --file /locks/conda-linux-64.lock

# Primary container

FROM gcr.io/distroless/base-debian10

COPY --from=conda /opt/env /opt/env
```

[conda]: https://docs.conda.io/projects/conda
[metayaml]: https://docs.conda.io/projects/conda-build/en/latest/resources/define-metadata.html
[mapping]: https://github.com/regro/cf-graph-countyfair/blob/master/mappings/pypi/grayskull_pypi_mapping.yaml
[envyaml]: https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html#create-env-file-manually
[poetry]: https://python-poetry.org
