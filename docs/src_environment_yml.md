# environment.yml

`conda-lock` has first class support for [environment.yml][envyaml]  files.

If no `--file` argument is specified `conda-lock` will look for an `environment.yml` file in the current directory.

## Basic example

```{.yaml title="environment.yml"}
channels:
  - conda-forge
dependencies:
  - python=3.9
  - pandas
```

## Extensions

conda-lock makes use of a number of non-standard extensions to the format in ordere to enable additional functionality.

### Platform specification

You may specify the platforms you wish to target by default directly in an [environment.yml][envyaml] using the (nonstandard) `platforms` key:

```{.yaml title="environment.yml"}
channels:
  - conda-forge
dependencies:
  - python=3.9
  - pandas
platforms:
  - osx-arm64
  - linux-64
```

If you specify target platforms on the command line with `-p`, these will
override the values in the environment specification. If neither `platforms` nor
`-p` are provided, `conda-lock` will fall back to a default set of platforms.

### Categories

You can may wish to split your dependencies into separate files for better
organization, e.g. a `environment.yml` for production dependencies and a
`dev-environment.yml` for development dependencies. You can assign all the
dependencies parsed from a single file to a category using the (nonstandard)
`category` key.

```{.yaml title=dev-environment.yml}
channels:
  - conda-forge
dependencies:
  - pytest
  - mypy=0.910
category: dev
```

The default category is `main`.

These can be used in a [compound specification](/compound_specification) as follows.

```sh
conda-lock --file environment.yml --file dev-environment.yml
```

[envyaml]: https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html#create-env-file-manually