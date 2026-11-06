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

#### Installing with mamba or micromamba

Mamba 2.x and micromamba can create an environment directly from the unified
lockfile, without installing conda-lock on the target machine or running a Conda
solve:

```shell
mamba create -n my-locked-env -f conda-lock.yml
# Or use micromamba:
micromamba create -n my-locked-env -f conda-lock.yml
```

The filename must end in `-lock.yml` or `-lock.yaml`, and the lockfile must contain
the target platform. Only the `main` category is selected by default. To include
development dependencies, select both categories:

```shell
mamba create -n my-locked-env -f conda-lock.yml --category main --category dev
```

Repeat `--category` for any additional categories you need. The selected packages
include both Conda and pip entries. Pip entries are installed with pip inside
the new environment, so the lockfile must include Python and pip when it contains
pip packages.

Micromamba introduced unified-lockfile support in
[0.23.0](https://github.com/mamba-org/mamba/blob/main/CHANGELOG.md#20220421).
Mamba adopted micromamba's CLI in
[2.0](https://mamba.readthedocs.io/en/latest/developer_zone/changes-2.0.html).
Use a current release; see the upstream
[lockfile documentation](https://mamba.readthedocs.io/en/latest/user_guide/mamba.html#conda-lock-yaml-spec-files).

#### Installing a rendered lockfile

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
