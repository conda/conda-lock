# Test v2 to v3 upgrade

> [!NOTE]
> Obligatory reminder that `content_hash` is fundamentally flawed: <https://github.com/conda/conda-lock/issues/432#issuecomment-1637071282>.

The goal of this test is to prevent regressions to the stability of the input hash. A regression in v3.0.0 caused the content hash to change. The regression has been fixed in v3.0.3, and this version now produces content hashes identical with v2.5.8. Moreover, v3.0.3 now recognizes content hashes from v3.0.2.

With the respective versions, I generated lockfiles with the following commands:

```bash
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml -f pyproject.toml --lockfile conda-lock-v2.5.8.yml
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml -f pyproject.toml --lockfile conda-lock-v3.0.2.yml
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml -f pyproject.toml --lockfile conda-lock-v3.0.3.yml
```

With v3.0.3, running any of

```bash
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml -f pyproject.toml --lockfile conda-lock-v2.5.8.yml --check-input-hash
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml -f pyproject.toml --lockfile conda-lock-v3.0.2.yml --check-input-hash
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml -f pyproject.toml --lockfile conda-lock-v3.0.3.yml --check-input-hash
```

results in

```text
Spec hash already locked for ['linux-64', 'linux-aarch64', 'linux-ppc64le', 'osx-64', 'osx-arm64', 'win-64']. Skipping solve.
```

With v2.5.8, the following two solves are skipped:

```bash
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml -f pyproject.toml --lockfile conda-lock-v2.5.8.yml --check-input-hash
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml -f pyproject.toml --lockfile conda-lock-v3.0.3.yml --check-input-hash
```

Moreover, the input hashes produced by v2.5.8 and v3.0.3 are identical:

```yaml
  content_hash:
    linux-64: 4e3086c79ebb7044f221959819fbca22e3ad4144b2723482e48f2cffef1cb948
    linux-aarch64: 07b90c11b3b0bb858767afd42d753952d0f1c6df852771b0d5d2d3f495628cfa
    linux-ppc64le: 39107ca32794f20f9b443d2d44862b5d06184164df3f851f56901fd0d69483e9
    osx-64: d8bfcbde7a20bc50b27ca25139f0d18ee48d21905c7482722c120793713144b1
    osx-arm64: e5b0208328748fdbbf872160bf8e5aff48d3fd5f38fde26e12dcd72a32d5a0d7
    win-64: a67c2def7fa06f94d92df2f17e7c7c940efbb0998a92788a2c1c4feddd605579
```

With v3.0.2, due to the regression in input hash stability, solves are only skipped for other lockfiles produced by v3.0.0, v3.0.1, or v3.0.2. It's recommended to upgrade to v3.0.3.
