# codex-4-oci-aidp

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Black](https://img.shields.io/badge/code%20style-black-000000)
![Pylint](https://img.shields.io/badge/lint-pylint-1674B1)
![pytest](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)

Specifications and scripts for evaluating OCI AI Data Platform configuration and
development using Codex and OpenAI Astra.

## Shared configuration and dependencies

Keep local settings in the root `.env` (excluded from Git), using
[.env.example](.env.example) as the template. All features share the root
[requirements.txt](requirements.txt) for runtime dependencies and
[requirements-dev.txt](requirements-dev.txt) for development tools.

```bash
conda activate codex-4-oci-aidp
mkdir -p .deps
curl -fL 'https://github.com/oracle-samples/aidataplatform-sdk/releases/download/v4.2.1/aidp-python-client-4.2.1.zip' \
  -o .deps/aidp-python-client-4.2.1.zip
echo '9cd99a1196b89e9a3354f8b9111ce59412efb23c4888e56126e3a2ca2364c3ba  .deps/aidp-python-client-4.2.1.zip' | shasum -a 256 -c -
unzip -o .deps/aidp-python-client-4.2.1.zip '*.whl' -d .deps
python -m pip install -r requirements-dev.txt
```

Run these commands from the repository root. Stop if the checksum check fails.
Oracle distributes the Python SDK as a wheel inside the release ZIP, so download
and extract it before installing requirements. `.deps/` is ignored by Git.
For runtime-only installation, use `requirements.txt` in the final command.

## Dependencies

| Package | Version | Purpose |
| --- | --- | --- |
| [Oracle AI DP SDK](https://github.com/oracle-samples/aidataplatform-sdk) (`aidp-python-client`) | 4.2.1 | Workspace, cluster, catalog, schema and volume operations through generated Python clients |
| `oci` | 2.165.1 | OCI configuration, signing, compartment and AI DP instance discovery |
| `python-dotenv` | 1.2.3 | Shared `.env` settings |
| `fastmcp` | 3.4.5 | Local stdio MCP server framework for scoped AI DP notebook workflow tools |
| `black` | 26.5.1 | Development: code formatting |
| `pylint` | 4.0.8 | Development: static analysis |
| `pytest` | 9.1.1 | Development: automated tests |

AI DP SDK 4.2.1 requires `oci>=2.165.0,<2.166`; the project pins a compatible
version. The [Oracle release](https://github.com/oracle-samples/aidataplatform-sdk/releases/tag/v4.2.1)
provides the package and checksum. No direct `requests` dependency or OCI CLI
installation is needed. Transitive dependencies are resolved by pip.

See [DEVELOPMENT.md](DEVELOPMENT.md) for contributor commands.

## Features

* [Cluster lifecycle](cluster_lifecycle/README.md): discover, inspect, start and
  stop a Workbench cluster using Python, OCI authentication and `.env` settings.
* [Catalog tree](catalog_tree/README.md): list a catalog's visible schemas and
  volumes as a hierarchical tree.
* [AI DP MCP workflow server](specs/002-notebook-workspace-job-mcp.md): upload
  a local notebook, reconcile a single-task notebook job, submit a job run and
  inspect its status through Codex.

Both features reuse [aidp_common](aidp_common/README.md) for authentication,
connection settings, SDK initialization and resource discovery.
