# Migration scripts to vendor Poetry

This directory contains scripts to vendor Poetry into conda-lock.

## Usage

To vendor Poetry, run the following command in an environment where conda-lock is installed:

```bash
pip install -r requirements.txt
migrate-code upgrade
```
