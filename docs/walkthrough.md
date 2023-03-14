
# Table of Contents
- [How to Use This Guide](#how-to-use-this-guide)
  - [Gotchas](#gotchas)
- [Goals for the Environment](#goals-for-the-environment)
  - [Q&A](#qa)
- [Your Platform Setup](#your-platform-setup)
  - [Global Dependencies](#global-dependencies)
- [Workflow 1: Locking a New Project](#workflow-1-locking-a-new-project)
- [Workflow 2: Locking an Existing Project (TBD)](#workflow-2-locking-an-existing-project)
- [Reproduce](#reproduce)
  - [With Docker in Mind](#with-docker-in-mind)
- [Upgrade](#upgrade)
  - [Upgrade an existing package](#upgrade-an-existing-package)
  - [Adding a New Package via `mamba`](#adding-a-new-package-via-mamba)
  - [Adding a New Package via `pip`](#adding-a-new-package-via-pip)
  - [A Quick Note on Downgrading](#a-quick-note-on-downgrading)
- [Debugging](#debugging)
  - [`conda-lock` giving vague `AssertionError`](#conda-lock-giving-vague-assertionerror)
- [Terms](#terms)
- [References and Resources](#references-and-resources)
- [Versions](#versions)

# How to Use This Guide
This is a guide on how to produce a reproducible and upgradeable environment for Python applications. A great deal of inspiration came from a pythonspeed.com article about this very topic.<sup>[4](#references-and-resources)</sup> While the article is a wonderful resource, I needed more so I developed this guide.

You can use the table of contents to get a quick look at topics. Or you can skip ahead to [Your Platform Setup](#your-platform-setup) which is the beginning of the walkthrough.

Ultimately, this guide is just that, a *guide*. The goal was to make the concepts easy to follow so that you can apply it to your use case. You may use a different platform than this guide but hopefully still benefit from the content.

## Gotchas
* I use `pipx` for parts of this guide, you may choose not to.
* My platform may not be your platform so keep an eye out for incompatible commands.

# Goals for the Environment
**Reproducible**: The environment can be reproduced across platforms.   
**Upgradeable**: Packages in the environment can be easily changed (e.g., version, new package, etc.)

## Q&A

### Why do you suggest using  `mamba`  AND  `pip`?
Sometimes a package installed via `mamba` breaks my environment. I have found using `pip` as the *exception to the rule* has resolved most of these issues.

### Where should I install  `conda-lock`?
Using `pipx` to install `conda-lock` allows you to have peace of mind about running the package outside of your `mamba` environment (even your `mamba` "base" environment). Alternatively, you could install it in your `mamba` "base," but I ran into issues that way and this walkthrough will not cover that angle.

### How can I find the version of my conda dependency?
A command I like to use is,
```bash
(base) $ mamba list | grep PACKAGE_NAME
```
This should show you the package with the version.

# Your Platform Setup
"Your *platform*" is the platform being used by you from a global perspective. Your environment is going to be the place we initialize the process of standardizing the environment for the goals listed above. By the end of this guide, you should be able to share the environment to another platform.

## Global Dependencies
- [pipx](https://pypa.github.io/pipx/) — "...help you install and run end-user applications written in Python"
-   [mamba (via mambaforge)](https://github.com/conda-forge/miniforge#mambaforge)  — "Drop-in, faster conda..."
-   [pip](https://pypi.org/project/pip/)  — "...the package installer for Python"
-   [conda-lock](https://github.com/conda-incubator/conda-lock)  — "...a lightweight library that can be used to generate fully reproducible lock files for conda environments."
-   [conda-forge](https://conda-forge.org/)  — "A community-led collection of recipes, build infrastructure and distributions for the conda package manager."

### Global Install Instructions
Find more detailed guides to installing these tools on their websites [below](#references-and-resources).
* Install `pipx`
* Install `conda-lock` via `pipx` or install `'conda-lock[pip_support]'` if you expect the need for `pip`
* Install `mamba`

# Workflow 1: Locking a New Project
In an ideal world, you can do environment management workflow before you write one line of code.

If you have mamba installed properly, your terminal should open like this,
```bash
(base) $
```
1. Change directories to your project. For the tutorial, the project is called `env-management-demo`.
```bash
(base) $ cd env-management-demo
```
2. Create a new `mamba` environment for your project
```bash
(base) $ mamba create -n env-management-demo -c conda-forge python
```
3. Activate your environment. Note that my environment name is the same as my project name. I do this, but you don't have to. This is indicated by how `(base)` changes to `(env-management-demo)`.
```bash
(base) $ mamba activate env-management-demo
(env-management-demo) $
```
4. Before any other packages are installed, generate a `mamba` `environment.yml` file. This step will generate a file that will need some editing in a moment.
```bash
(env-management-demo) $ mamba env export --from-history > environment.yml
```
5. `environment.yml` will populate with your current environment parameters and some configurations. It may look different from mine but your channels must match your actual `mamba` channels. I have noted which lines to remove.

(Optional) Check your channels
```bash
(env-management-demo) $ mamba config --show channels
```

The following shows a before and after of edits. You will see a `# REMOVE` next to changes.
Before...
```yml
# environment.yml
name: env-management-demo # REMOVE
channels:
  - conda-forge
dependencies:
  - python
prefix: /Users/uname/miniconda3/envs/env-management-demo # REMOVE
```
After...
```yml
# environment.yml
channels:
  - conda-forge
dependencies:
  - python
```
6. Now add the platforms you want to support with this `conda-forge` non-standard format.
```yml
# environment.yml
channels:
  - conda-forge
dependencies:
  - python
platforms: # This is non-standard and recommended by conda-lock.
  - linux-64
  - osx-64
  - win-64
  - osx-arm64  # For Apple Silicon, e.g. M1/M2
```
7. From now on, you will add packages manually to this document under the `dependencies` sequence key unless you have to, for some reason, download a package using pip. Here is an example of that process:
```bash
(env-management-demo) $ mamba install -c conda-forge pandas
...
```
Then add it to the `environment.yml` (indicated by `# NEW`.)
```yml
# environment.yml
channels:
  - conda-forge
dependencies:
  - python
  - pandas # NEW
platforms:
  - linux-64
  - osx-64
  - win-64
  - osx-arm64
```
8. Once you are at a checkpoint in your code and ready to share it, you should generate a lockfile `conda-lock.yml` using the `conda-lock` command.
```bash
(env-management-demo) $ conda-lock -f environment.yml
Locking dependencies for ['linux-64', 'osx-64', 'osx-arm64', 'win-64']...
...
```
9. Now that your environment is "locked," you can test this by creating a new environment from the `conda-lock.yml` file.
```bash
(env-management-demo) $ mamba deactivate
(base) $ 
(base) $ conda-lock install -n env-locked
INFO:root:Downloading and Extracting Packages
INFO:root:numpy-1.24.1
INFO:root:pandas-1.5.3
...
(base) $ mamba activate env-locked
(env-locked) $
```
10. After testing this environment on your code, you can share the `conda-lock.yml` file with other developers and have them run the code with the same `conda-lock` command.

# Workflow 2: Locking an Existing Project (TBD)
While this part of the tutorial is unfinished, you could get a pretty good idea of how to do this without this section. For now, you can continue to [Reproduce](#reproduce). Good luck!

# Reproduce
Once you have the environment locked into a `conda-lock.yml` multi-platform file, you will be able to reproduce the environment (i.e., create and activate it) with the following command as long as the file is in your current directory:
```bash
(base) $ conda-lock install -n NAME
(base) $ mamba activate NAME
(NAME) $
```
## With Docker in Mind
To minimize the size of the lockfile, you may want to render a single-platform lockfile for linux.
```bash
(base) $ conda-lock render -p linux-64
...
```
The default file result is a file named `conda-linux-64.lock`. This lockfile can then be copied into an image and used from within the image.
```
mamba create -n my-locked-env --file conda-linux-64.lock
```

# Updating the Environment
Currently the `--update` flag for the `conda-lock` command does not work as expected. For now, the way to update a package (e.g., upgrade, downgrade, remove, or add a package) is a workflow described like so:
1. Update your environment, for example, add the package `matplotlib` via `mamba`:
```bash
(env-management-demo) $ mamba install matplotlib
```
2. Manually add this to the `environment.yml` file:
```yml
# environment.yml
channels:
  - conda-forge
dependencies:
  - python
  - pandas
  - matplotlib # NEW
platforms:
  - linux-64
  - osx-64
  - win-64
  - osx-arm64
```
3. Rerun `conda-lock` which will resolve the entire environent:
```bash
(env-management-demo) $ conda-lock -f environment.yml
```

Note that it is not necessary to add the package to your environment as seen in step 1 above, but I would reccomend doing this and testing your code before persisitng this change to `conda-lock.yml`.

## Adding a New Package via `pip`
Sometimes `conda` does not have everything you need so you lean on `pip` for a dependency. `conda-lock` comes with `pip` support if you install it with the extra like so:
```
(base) $ pipx install 'conda-lock[pip_support]'
...
```
This allows you to add pip dependencies as needed.
1. Install your package within your `conda` env with `pip`.
```bash
(env-management-demo) $ pip install fastapi
...
```
2. Update the `environment.yml`.
```yml
# environment.yml
channels:
  - conda-forge
dependencies:
  - python
  - pandas
  - matplotlib=3.5
  - pip: # NEW
    - fastapi # NEW
platforms:
  - linux-64
  - osx-64
  - win-64
  - osx-arm64
```
3. Update with the `conda-lock` command.
```
(env-management-demo) $ mamba deactivate
(base) $ conda-lock --update fastapi
Locking dependencies for ['linux-64', 'osx-64', 'osx-arm64', 'win-64']...
...
```

# Debugging
Last Update: February 6, 2023
This section will be updated as issues are discovered. If issues are discovered, they should be checked against the `conda-lock` repo linked below so contributors can be made aware.

## `conda-lock` giving vague `AssertionError`
Sometimes an error from `conda-lock` is vague and hard to debug. I am logging some fixes I have discovered during development.
### Are your channels consistent?
Are the channels you have set in `mamba` and your `environment.yml` file consistent? If not, make sure they are by editing your `environment.yml` file to match. Once I have found that deleting the existing `conda-lock.yml` file and trying again will resolve this issue.

# Terms
platform
    : the OS being used for a task (e.g., MacOS, Windows, etc.)

# References and Resources
1. [pypa.github.io — Global Python package manager `pipx`](https://pypa.github.io/pipx/)
2. [docs.conda.io — Environment manager `conda` via miniconda](https://docs.conda.io/en/latest/miniconda.html)
3. [github.com — Cross-platform environment locker `conda-lock`](https://github.com/conda/conda-lock)
4. [pythonspeed.com — Reproducible and upgradable Conda environments with conda-lock](https://pythonspeed.com/articles/conda-dependency-management/)
5.  [github.com — Recommended direct dependency update path](https://github.com/conda-incubator/conda-lock/issues/248)
6.  [github.com — Optional groups of dependencies #7502](https://github.com/conda/conda/issues/7502)

# Versions
My local versions for reference.
```text
System Version: macOS 13.0 (22A380)
Kernel Version: Darwin 22.1.0
conda 22.11.1
mamba 1.1.0
pipx 1.1.0
conda-lock 1.4.0, installed using Python 3.11.0
zsh 5.8.1 (x86_64-apple-darwin22.0)
```