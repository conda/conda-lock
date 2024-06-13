# Basic Usage

Consider the following source specification:

```{.yaml title="environment.yml"}
channels:
  - conda-forge
dependencies:
  - python=3.12
  - numpy
```

### Generating a Lockfile

Generate a multi-platform lockfile `conda-lock.yml`

```shell
conda-lock -f environment.yml -p osx-64 -p linux-64
```

### Creating an Environment

Create an environment from the lockfile

```shell
conda-lock install [-p {prefix}|-n {name}]
```

Alternatively, render a single-platform lockfile and use conda command directly

```shell
conda-lock render -p linux-64
conda create -n my-locked-env --file conda-linux-64.lock
```

### Updating Packages

Update the previous solution, using the latest version of numpy that is
compatible with the source specification. This command overrides the lockfile.

```shell
conda-lock --update numpy
```

### Adding new Packages

Add a new package to the environment

```{.yaml title="Updated environment.yml"}
channels:
  - conda-forge
dependencies:
  - python=3.12
  - numpy
  - pandas  # new
```

and regenerate the lockfile

```shell
conda-lock -f environment.yml -p osx-64 -p linux-64
```

Note that this updates existing packages.
