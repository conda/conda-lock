# conda-lock

Conda lock is a lightweight library that can be used to generate fully reproducible lock files for conda environments.

It does this by performing multiple solves for conda targeting a set of platforms you desire lockfiles for.

This also has the added benefit of acting as an external presolve for conda as the lockfiles it generates
results in the conda solver *not* being invoked when installing the packages from the generated lockfile.

## installation

```
pip install conda-lock
```

## usage

```bash
# generate the lockfiles
conda-lock -f environment.yml -p osx-64 -p linux-64

# create an environment from the lockfile
conda create -n my-locked-env --file conda-linux-64.lock
```

