ARG MICROMAMBA_TAG=latest

FROM mambaorg/micromamba:${MICROMAMBA_TAG}

# Install Python and pip
RUN micromamba install -y -n base -c conda-forge python pip git mamba less nano conda-standalone

# Install conda-lock from PyPI as a base
# This will be replaced with an editable install at runtime if desired
RUN micromamba run -n base pip install conda-lock

WORKDIR /workspace
