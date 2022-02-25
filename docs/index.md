---
hide:
  - toc
---

# conda-lock

Conda lock is a lightweight library that can be used to generate fully reproducible lock files for conda environments.

It does this by performing multiple solves for conda/mamba targeting a set of platforms you desire lockfiles for.

This also has the added benefit of acting as an external presolve for conda as the lockfiles it generates
results in the conda solver **not** being invoked when installing the packages from the generated lockfile.

## Features

* Unified lockfile format
* Integrated pip support
* Support for a variety of source formats
  * conda environment.yml
  * conda meta.yaml
  * pyproject.toml (poetry, flit, pep 621)
* Solveless conda installation
