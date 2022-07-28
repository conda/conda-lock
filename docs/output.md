# Output formats

## Unified lockfile

Conda lock's default output format is a unified multi-platform lockfile.

This is defined by a [pydantic](https://pydantic-docs.helpmanual.io/usage/models/) model
[here](https://github.com/conda-incubator/conda-lock/blob/main/conda_lock/src_parser/__init__.py#L126)

In order to explicitly use this format

```shell
conda-lock --kind lock
```

To install from one of these lockfiles

```bash
conda-lock install conda-lock.yml
```

For proper parsing the unified lockfile must have the proper `.conda-lock.yml` extension (e.g foo.conda-lock.yml)

### Render

The unified lockfile can be rendered into the various other lockfile formats.

Generate both formats using

```shell
conda-lock render --kind explicit --kind env
```

## Explicit lockfile

The legacy format that conda lock supports.  This was the default format prior to conda-lock 1.0.

This format is understood natively by both conda and mamba.  If your lock contains pip solved packages
these can only be installed by conda-lock

```bash
conda-lock --kind explicit --platform
```

To install from this lockfile you can aither use conda/mamba directly

```shell
conda create --name YOURENV --file conda-linux-64.lock
```

or

```shell
conda-lock install --name YOURENV conda-linux-64.lock
```

## Environment lockfile

This format is itself a conda [environment.yml][envyaml] that can be installed by `conda env create`.

This format does have the drawback that using it will invoke a new solve unlike the explicit format.

To install from this lockfile you can aither use conda/mamba directly

```shell
conda env create --name YOURENV --file conda-linux-64.lock.yml
```

or

```shell
conda-lock install --name YOURENV conda-linux-64.lock.yml
```

[envyaml]: https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html#create-env-file-manually
