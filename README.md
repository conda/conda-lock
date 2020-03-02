# conda-lock

Conda lock is a lightweight library that can be used to generate fully reproducible lock files for conda environments.

It does this by performing multiple solves for conda targeting a set of platforms you desire lockfiles for.

This also has the added benefit of acting as an external presolve for conda as the lockfiles it generates
results in the conda solver *not* being invoked when installing the packages from the generated lockfile.

## why?

Conda environment.yaml files are very useful for defining desired environments but there are times when we want to
be able to EXACTLY reproduce an environment by just installing and downloading the packages needed.

This is particularly handy in the context of a gitops style setup where you use conda to provision environments in
various places

### Dockerfile example

In order to use conda-lock in a docker-style context you want to add the lockfile to the
docker container.  In order to refresh the lock file just run `conda-lock` again.
```
  Dockerfile
  environment.yaml
* conda-linux-64.lock
```

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

