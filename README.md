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
python -m pip install -r requirements-dev.txt
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for contributor commands.

## Features

* [Cluster lifecycle](cluster_lifecycle/README.md): discover, inspect, start and
  stop a Workbench cluster using Python, OCI authentication and `.env` settings.

