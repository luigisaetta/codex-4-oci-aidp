# Development checks

From the repository root, with the project Conda environment active:

```bash
python -m black cluster_lifecycle
python -m black --check cluster_lifecycle
python -m pylint cluster_lifecycle/cluster_lifecycle.py cluster_lifecycle/configuration.py cluster_lifecycle/tests
python -m pytest -q
```

Tool settings live in the root [pyproject.toml](pyproject.toml). pytest runs
generated-client HTTP tests and parametrized configuration tests without credentials
or network. Tests cover actions, ETags, no-ops, transitions, polling,
OCI/AI DP SDK pagination, duplicate targets, compartment boundaries, dotenv
precedence, regional endpoints, path encoding and error redaction.
