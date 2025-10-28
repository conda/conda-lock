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

# Copy the read-only mounted pkgs directory to a writable location in the user's home
# The pkgs directory must be writable for micromamba to use it properly
echo "Copying pkgs directory to writable location..."
cp -r /custom-pkgs-ro/ ~/custom-pkgs-writeable/

# Configure micromamba to use our custom writable pkgs directory
# This prepends it to the pkgs_dirs list so it's searched first
echo "Configuring micromamba to use custom pkgs directory..."
micromamba config prepend pkgs_dirs ~/custom-pkgs-writeable/

# Verify the configuration
echo "Current pkgs_dirs configuration:"
micromamba config list pkgs_dirs
echo ""

# The pkgs directory only contains index.json and repodata_record.json files
# We need to populate the actual package files by doing an explicit install
# This will generate warnings about invalid package cache, which is expected
echo "Populating package cache with explicit install (warnings are expected and filtered out)..."
echo "This fills in missing package files from the incomplete cache..."
# Filter out warning lines and the blank line that follows each warning
micromamba create -n temp-populate -y -f /explicit.lock 2>&1 | awk '
  /warning.*Invalid package cache/ { skip_next=1; next }
  skip_next { skip_next=0; if (NF==0) next }
  { print }
'
echo ""

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

