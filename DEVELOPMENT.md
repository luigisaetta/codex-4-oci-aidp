# Development checks

From the repository root, with the project Conda environment active:

```bash
python -m black aidp_common cluster_lifecycle catalog_tree conftest.py
python -m black --check aidp_common cluster_lifecycle catalog_tree conftest.py
python -m pylint aidp_common aidp_common/tests/*.py cluster_lifecycle/*.py cluster_lifecycle/tests/*.py catalog_tree/*.py catalog_tree/tests/*.py conftest.py
python -m pytest -q
```

Tool settings live in the root [pyproject.toml](pyproject.toml). pytest runs
generated-client HTTP tests and parametrized configuration tests without credentials
or network. Tests cover actions, ETags, no-ops, transitions, polling,
OCI/AI DP SDK pagination, duplicate targets, compartment boundaries, dotenv
precedence, regional endpoints, path encoding and error redaction.

Catalog tests cover pagination at every level, fully qualified schema keys,
empty schemas/catalogs, ambiguous names, control-character escaping and failures
without partial output. Shared tests use a temporary generated API key to verify
signer construction and check session cleanup. The root fixture blocks network
traffic for every offline test.

Live verification is separate and opt-in: `python catalog_tree/catalog_tree.py
fine_tuning` reads the selected catalog through the root `.env`. It requires
existing resources and read permissions; it creates nothing and needs no cleanup.
