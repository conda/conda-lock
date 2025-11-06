# Mitigations for repodata corruption

An upstream [regression in libmamba](https://github.com/mamba-org/mamba/issues/4052) causes repodata to be corrupted.
The affected mamba/micromamba versions are 2.1.1 through 2.3.2.
The regression was partially fixed in [mamba-org/mamba#4071](https://github.com/mamba-org/mamba/pull/4071) and released as version 2.3.3.

## The corruption pattern

When installing packages from an explicit lockfile (e.g., `micromamba install -f 01-explicit.lock`), affected versions write incorrect metadata to `repodata_record.json` files in the package cache.

### 2.1.1–2.3.2 (full corruption)

The following fields are corrupted in `repodata_record.json`:

- `depends` → `[]` (emptied)
- `constrains` → `[]` (emptied)
- `license` → `""` (emptied)
- `timestamp` → `0`
- `build_number` → `0`
- `track_features` → `""`

The corresponding `info/index.json` files remain correct; only `repodata_record.json` is corrupted.

### 2.3.3 (partial fix)

Upstream fixed how `depends` and `constrains` are populated: they are now copied from `info/index.json`. Other fields remain zeroed/emptied.

- `depends` → copied exactly from `index.json` (key omitted if absent)
- `constrains` → copied from `index.json`; empty lists are omitted
- `license` → `""`
- `timestamp` → `0`
- `build_number` → `0`
- `track_features` → `""`

This means 2.3.3 still differs from a good cache, but only in the four metadata fields above; dependency-related fields match `index.json`.

### Field-by-field behavior

| Field           | 2.1.0 (good) | 2.1.1–2.3.2 (bug) | 2.3.3 (partial fix)            |
|-----------------|---------------|--------------------|---------------------------------|
| depends         | correct       | []                 | from `index.json` (omit if none) |
| constrains      | correct       | []                 | from `index.json` (omit if empty) |
| license         | correct       | ""                 | ""                              |
| timestamp       | correct       | 0                  | 0                               |
| build_number    | correct       | 0                  | 0                               |
| track_features  | correct       | ""                 | ""                              |

See upstream fix PR `mamba-org/mamba#4071` for details.

## Reproducing the issue

This directory contains scripts to reproduce the corrupt repodata issue by running different micromamba versions in Docker.

### Generating the explicit lockfile

The `01-explicit.lock` file is generated from the main conda-lock environment:

```bash
cd tests/test-corrupt-repodata
python 01-generate-sample-explicit-lockfile.py
```

This will generate `01-explicit.lock` from `environments/conda-lock.yml`.

### Extracting repodata from different micromamba versions

Run the script with different versions to extract and compare repodata records:

```bash
python 02-reproduce-corrupt-repodata-via-upstream.py --version 2.1.0
python 02-reproduce-corrupt-repodata-via-upstream.py --version 2.1.1
python 02-reproduce-corrupt-repodata-via-upstream.py --version 2.3.3
```

This will create version-specific compressed archives (e.g., `2.1.0-pkgs.tar.gz`, `2.1.1-pkgs.tar.gz`, `2.3.3-pkgs.tar.gz`) containing the `index.json` and `repodata_record.json` files from each package's `info/` directory.

### Comparing results

The archives contain filtered package metadata. The scripts will automatically extract them to `{version}-pkgs/` directories when run (always fresh extraction). To manually compare:

```bash
rm -rf 2.1.0-pkgs 2.1.1-pkgs 2.3.3-pkgs
tar -xzf 2.1.0-pkgs.tar.gz
tar -xzf 2.1.1-pkgs.tar.gz
tar -xzf 2.3.3-pkgs.tar.gz
diff -r 2.1.0-pkgs/ 2.1.1-pkgs/
diff -r 2.1.0-pkgs/ 2.3.3-pkgs/
```

The corrupt versions will have different corruption patterns:

- **2.1.1–2.3.2**: Full corruption — `depends`/`constrains` emptied; `license`/`timestamp`/`build_number`/`track_features` zeroed/empty
- **2.3.3**: Partial fix — `depends`/`constrains` copied from `index.json` (empty constrains omitted); other fields still zeroed/empty

**Note:** Extracted directories are kept for inspection and ignored by git. The `.tar.gz` archives are the source of truth and should be committed to git.

### Simulating the corruption

To verify that the corruption pattern is well-understood, you can apply the same corruption to a good version:

```bash
# Test the 2.1.1 pattern (full corruption)
python 03-clobber-pkgs.py --corrupt-version=2.1.1 --pattern=2.1.1

# Test the 2.3.3 pattern (partial fix — depends/constrains from index.json)
python 03-clobber-pkgs.py --corrupt-version=2.3.3 --pattern=2.3.3
```

This script:

1. Extracts the input and corrupt pkgs archives (always fresh)
2. Copies the good pkgs to `clobbered-{version}-pkgs/`
3. Applies the specified corruption pattern to all `repodata_record.json` files:
   - **2.1.1**: Sets `depends`/`constrains` to `[]`, zeros the other fields
   - **2.3.3**: Reads `info/index.json` and sets `depends`/`constrains` to match (omits absent/empty), zeros the other fields
4. Runs `diff` to verify the clobbered files match the corrupt version exactly
5. Exits with status 0 if they match, 1 if they differ

If the corruption pattern is correctly understood, the diff should be empty and the script will report success.

The extracted directories are kept for inspection after the script completes.

## Testing conda-lock with different pkgs directories

To test how conda-lock behaves when reading from different package caches, use the fourth script:

```bash
# Generate lockfiles using the good 2.1.0 cache (tests both conda and mamba)
python 04-test-conda-lock-with-pkgs.py --pkgs-archive 2.1.0-pkgs.tar.gz

# Generate lockfiles using the corrupt 2.1.1 cache
python 04-test-conda-lock-with-pkgs.py --pkgs-archive 2.1.1-pkgs.tar.gz

# Generate lockfiles using the corrupt 2.3.3 cache
python 04-test-conda-lock-with-pkgs.py --pkgs-archive 2.3.3-pkgs.tar.gz
```

This will:

1. Build a Docker image with conda-lock installed from PyPI
2. Extract the specified `.tar.gz` archive (always fresh)
3. Mount the extracted pkgs directory as `/custom-pkgs-ro` (read-only)
4. Mount the explicit lockfile (`01-explicit.lock`) for populating the cache
5. Inside the container:
   - Setup conda-lock in editable mode from the mounted source
   - Configure micromamba to use `~/custom-pkgs-writeable/` as the first pkgs directory
   - Warm the default package cache with `micromamba create -n temp-populate -y -f /explicit.lock`
   - Copy the custom pkgs directory to `~/custom-pkgs-writeable/` (overwrites the warmed cache metadata)
   - Run conda-lock with `--conda=/opt/conda/standalone_conda/conda.exe` → `lockfile-{pkgs-dir}-lock-with-conda.yml`
   - Recopy the custom pkgs directory to ensure corrupt metadata for the second run
   - Run conda-lock with `--conda=/opt/conda/bin/mamba` → `lockfile-{pkgs-dir}-lock-with-mamba.yml`
6. Copy both lockfiles out of the container

**Why these steps are necessary:**

- The extracted archives only contain `index.json`/`repodata_record.json` (metadata), not the actual package files
- Configuring the custom pkgs directory first ensures micromamba will check there before the default cache
- Warming the default cache populates it with all package files (including metadata)
- Copying the custom pkgs directory **after** warming overwrites the good metadata with corrupt metadata
- This creates a hybrid cache: corrupt metadata from custom pkgs, but complete package files from the warmed default cache
- conda-lock reads the corrupt metadata but can still access the package files it needs
- Recopying before the second run ensures any metadata repairs from the first run don't affect the second

**Why test both conda and mamba:**

- Different conda executables may handle corrupt metadata differently
- This helps identify whether the corruption affects all solvers or just specific ones

Compare the generated lockfiles to see how corrupt metadata affects the output:

```bash
# Compare good vs corrupt cache (conda)
diff lockfile-2.1.0-pkgs-lock-with-conda.yml lockfile-2.1.1-pkgs-lock-with-conda.yml
diff lockfile-2.1.0-pkgs-lock-with-conda.yml lockfile-2.3.3-pkgs-lock-with-conda.yml

# Compare good vs corrupt cache (mamba)
diff lockfile-2.1.0-pkgs-lock-with-mamba.yml lockfile-2.1.1-pkgs-lock-with-mamba.yml
diff lockfile-2.1.0-pkgs-lock-with-mamba.yml lockfile-2.3.3-pkgs-lock-with-mamba.yml

# Compare conda vs mamba (same cache)
diff lockfile-2.1.0-pkgs-lock-with-conda.yml lockfile-2.1.0-pkgs-lock-with-mamba.yml
```

This allows us to verify whether corrupt `repodata_record.json` files lead to corrupt lockfile entries (missing dependencies, missing sha256, etc.).

## Testing the `--update` corruption path

The most reliable way to trigger corruption is through the `--update` code path, which reads from the cache when reconstructing unchanged packages:

```bash
# First, generate base lockfiles with good cache (if not already generated)
python 04-test-conda-lock-with-pkgs.py --pkgs-archive 2.1.0-pkgs.tar.gz

# Then test --update with both good and corrupt caches
python 05-test-update-with-corrupt-cache.py --update-package pytest
```

This will:

1. Use `lockfile-2.1.0-pkgs-lock-with-conda.yml` as the base lockfile
2. Run `conda-lock lock --update pytest` twice:
   - Once with `2.1.0-pkgs` (good cache)
   - Once with `2.1.1-pkgs` (corrupt cache)
3. Compare the results to detect corruption

The `--update` path triggers corruption because:

- It creates a fake environment from the existing lockfile
- Runs an update dry-run which may use cached packages
- Calls `_reconstruct_fetch_actions()` to read from cache
- Corrupt cache → corrupt lockfile entries

This is the primary way the bug manifests in the wild!
