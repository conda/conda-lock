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

# Run conda-lock to generate the lockfile
# It will now read from the populated cache which has corrupt repodata_record.json files
echo "Running conda-lock..."
micromamba run -n base conda-lock lock \
    --micromamba \
    -f /workspace/dev-environment.yaml \
    -p linux-64 \
    --lockfile /tmp/lockfile.yml

echo ""
echo "Lockfile generated at /tmp/lockfile.yml"

