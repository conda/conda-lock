# conda-lock

Conda lock is a lightweight library that can be used to generate fully reproducible lock files for [conda][conda]
environments.

It does this by performing a conda solve for each platform you desire a lockfile for.

This also has the added benefit of acting as an external pre-solve for conda as the lockfiles it generates
results in the conda solver *not* being invoked when installing the packages from the generated lockfile.

## why?

Conda [`environment.yml`][envyaml] files are very useful for defining desired environments but there are times when we want to
be able to EXACTLY reproduce an environment by just installing and downloading the packages needed.

This is particularly handy in the context of a gitops style setup where you use conda to provision environments in
various places.

## installation

```
pip install conda-lock
conda install conda-lock
```

## Basic usage

```bash
# generate the lockfiles
conda-lock -f environment.yml -p osx-64 -p linux-64

# create an environment from the lockfile
conda-lock install [-p {prefix}|-n {name}] conda-linux-64.lock

# alternatively, use conda command directly
conda create -n my-locked-env --file conda-linux-64.lock
```

## Advance usage

#### File naming

By default conda-lock will name files as `"conda-{platform}.lock"`.

If you want to override that call conda-lock as follows.
```bash
conda-lock --filename-template "specific-{platform}.conda.lock"
```

#### Compound specification

Conda-lock will build a spec list from several files if requested.

```bash
conda-lock -f base.yml -f specific.yml -p linux-64 --filename-format "specific-{platform}.lock"
````

In this case all dependencies are combined, and the first non-empty value for `channels` is used as the final
specification.

This works for all supported file types.

#### channel overrides

You can override the channels that are used by conda-lock in case you need to override the ones specified in
an [environment.yml][envyaml]

```bash
conda-lock -c conda-forge -p linux-64
```

#### --dev-dependencies/--no-dev-dependencies

By default conda-lock will include dev dependencies in the specification of the lock (if the files that the lock
is being built from support them).  This can be disabled easily

```bash
conda-lock --no-dev-dependencies -f ./recipe/meta.yaml
```

#### --strip-auth and --auth-file

By default `conda-lock` will leave basic auth credentials for private conda channels in the lock file. If you wish to strip authentication from the file, provide the `--strip-auth` argument.

```
conda-lock --strip-auth -f environment.yml
```

In order to `conda-lock install` a lock file with its basic auth credentials stripped, you will need to create an authentication file in `.json` format like this:

```json
{
  "domain": "username:password",
  // ...
}
```

Then, you need to provide the path to the authentication file through the `--auth-file` argument.

```
conda-lock install --auth-file auth.json conda-linux-64.lock
```

## Supported file sources

Conda lock supports more than just [environment.yml][envyaml] specifications!

Additionally conda-lock supports [meta.yaml][metayaml] (conda-build)
and `pyproject.toml` (
[flit](https://flit.readthedocs.io/en/latest/) and [poetry](https://python-poetry.org)
based).  These do come with some gotchas but are generally good enough for the 90% use-case.

### meta.yaml

Conda-lock will attempt to make an educated guess at the desired environment spec in a meta.yaml.  This is
not guaranteed to work for complex recipes with many selectors and outputs.  For multi-output recipes, conda-lock
will fuse all the dependencies together.  If that doesn't work for your case fall back to specifying the specification
as an [environment.yml][envyaml]

Since a meta.yaml doesn't contain channel information we make use of the following extra key to retrieve channels

```yaml
# meta.yaml

extra:
  channels:
    - conda-forge
    - defaults
```

### pyproject.toml configuration

Since pyproject.toml files are commonly used by python packages it can be desirable to create a lock
file directly from those dependencies to single-source a package's dependencies.  This makes use of some
conda-forge infrastructure ([pypi-mapping][mapping]) to do a lookup of the PyPI package name to a corresponding
conda package name (e.g. `docker` -> `docker-py`).  In cases where there exists no lookup for the package it assumes that
the PyPI name, and the conda name are the same.

#### channels

```toml
# pyproject.toml

[tool.conda-lock]
channels = [
    'conda-forge', 'defaults'
]
```

#### extra dependencies

Since in a `pyproject.toml` all the definitions are python dependencies if you need
to specify some non-python dependencies as well this can be accomplished by adding
the following sections to the `pyproject.toml`

```toml
# pyproject.toml

[tool.conda-lock.dependencies]
sqlite = ">=3.34"
```


## Dockerfile example

In order to use conda-lock in a docker-style context you want to add the lockfile to the
docker container.  In order to refresh the lock file just run `conda-lock` again.

Given aa file tree like
```
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