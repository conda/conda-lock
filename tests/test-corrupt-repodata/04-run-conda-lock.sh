#!/bin/bash
# Script to run conda-lock with a specific pkgs directory inside a Docker container
#
# This script reproduces the corrupt repodata issue by:
# 1. Copying the read-only mounted pkgs directory to a writable location
# 2. Configuring micromamba to use the writable pkgs directory
# 3. Running an explicit install to populate missing package files (generates warnings)
# 4. Running conda-lock which will read from the now-populated but corrupt cache

set -e

# Setup editable install of conda-lock from mounted source
bash /setup-editable.sh

# Configure micromamba to use our custom writable pkgs directory first
echo "Configuring micromamba to use custom pkgs directory..."
micromamba config prepend pkgs_dirs ~/custom-pkgs-writeable/

# Verify the configuration
echo "Current pkgs_dirs configuration:"
micromamba config list pkgs_dirs
echo ""

# Warm the default package cache by performing an explicit install
# This ensures all package files are available without touching our custom metadata
echo "Warming default package cache with explicit install..."
micromamba create -n temp-populate -y -f /explicit.lock
echo ""

# Now copy the read-only mounted pkgs directory (metadata only) over the warmed cache
echo "Copying pkgs directory to writable location..."
cp -r /custom-pkgs-ro/. ~/custom-pkgs-writeable/

# Run conda-lock to generate lockfiles with both conda and mamba
echo "Running conda-lock with conda executable..."
micromamba run -n base conda-lock lock \
    --micromamba \
    --file=/workspace/dev-environment.yaml \
    --platform=linux-64 \
    --conda=/opt/conda/standalone_conda/conda.exe \
    --lockfile=/tmp/lockfile-conda.yml

echo ""
echo "Recopying pkgs directory to ensure corrupt metadata for second run..."
cp -r /custom-pkgs-ro/. ~/custom-pkgs-writeable/

echo ""
echo "Running conda-lock with mamba executable..."
micromamba run -n base conda-lock lock \
    --micromamba \
    --file=/workspace/dev-environment.yaml \
    --platform=linux-64 \
    --conda=/opt/conda/bin/mamba \
    --lockfile=/tmp/lockfile-mamba.yml

echo ""
echo "Lockfiles generated:"
echo "  /tmp/lockfile-conda.yml"
echo "  /tmp/lockfile-mamba.yml"

