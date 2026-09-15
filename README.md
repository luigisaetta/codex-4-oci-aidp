# codex-4-oci-aidp
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

## Features

* [Cluster lifecycle](cluster_lifecycle/README.md): discover, inspect, start and
  stop a Workbench cluster using Python, OCI authentication and `.env` settings.
  Black, Pylint and offline pytest checks pass; live OCI verification is pending.
