# Test v2 to v3 upgrade

I generated conda-lock.yml using v2.5.8 with the following command:

```bash
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml --lockfile conda-lock-v2.yml
```

When I run

```bash
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml --check-input-hash
```

I get the following output:

```
Spec hash already locked for ['linux-64']. Skipping solve.
```

Switching to v3, I run the following command:

```
conda-lock -f environment.yml -f test-dependencies.yml -f dev-dependencies.yml --lockfile conda-lock-v3.yml
```