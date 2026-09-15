# AGENTS.md

## Repository purpose

This project evaluates how, and to what extent, Codex and OpenAI Astra can help configure and develop on OCI AI Data Platform (OCI AI DP). Build clear, reproducible specifications, scripts, code, and experiments that a human can review, understand, and run.

macOS is a local development and testing environment. OCI AI DP is the target execution environment. Local success alone does not establish compatibility with the target platform.

Keep the repository focused on this evaluation. Use OCI APIs, OCI AI DP APIs, and the OCI command-line interface (OCI CLI) for remote setup where supported. Record unsupported operations and manual steps explicitly.

## Spec-driven workflow

Work spec-first for every feature or meaningful change, including infrastructure setup and experiments:

1. Read the relevant specification in `specs/` before implementation.
2. Create or update a concise specification when the requested behavior is not covered.
3. Describe the problem, scope, non-goals, assumptions, prerequisites, intended behavior, acceptance criteria, and verification approach before writing code.
4. For remote operations, identify the target resources, required permissions, configuration inputs, expected changes, and cleanup or recovery procedure.
5. Implement the specified behavior in small, reviewable steps. Update the specification when requirements change.
6. Record verification results and important implementation decisions in the specification or linked documentation.

Specifications are source-controlled project artifacts, not disposable planning notes. Keep them concise, testable, and in English. Routine implementation decisions within the authorized scope do not require a separate approval step.

## Language, clarity, and documentation

* Write all project documentation, comments, docstrings, notebook narrative cells, CLI help, log messages, and user-facing program output in English. Conversation with the user may follow the user's language.
* Prefer readable names, explicit control flow, small functions, and straightforward dependencies. Avoid clever abstractions that make human review harder.
* Explain responsibilities, non-obvious decisions, inputs, outputs, side effects, and failure conditions. Do not add comments that merely restate the code.
* Document runnable commands with prerequisites, safe placeholder values, expected results, and troubleshooting guidance.
* Keep documentation aligned with implementation. Distinguish planned, implemented, locally tested, and remotely verified behavior.
* Keep READMEs focused on setup and usage. Do not include reports that code passed Black, Pylint, pytest, or similar checks. Use tool badges in the main README, and record verification results in specifications or development documentation.
* Maintain a root `CHANGELOG.md` as implementation evolves. Record user-visible additions and significant changes under `Unreleased`, with each entry prefixed by its ISO 8601 date (`YYYY-MM-DD`).
* Verify service capabilities, API operations, CLI syntax, authentication options, and runtime constraints against authoritative documentation before relying on them. Link the relevant sources and record the verification date in the specification. Do not invent endpoints, SDK methods, CLI subcommands, or model identifiers.

## Agent working rules

* Inspect the repository, applicable instructions, specifications, and existing changes before editing.
* Preserve user changes. Prefer small, coherent changes over broad rewrites.
* Do not create commits unless explicitly asked.
* Add dependencies only when they serve a documented requirement.
* Complete work within the user's authorized scope. Ask for clarification only when missing information materially affects correctness or authorization.
* Never discard existing changes or perform destructive operations without explicit authorization.
* Never hard-code credentials, private endpoints, machine-specific paths, or personal data.
* Keep secrets, OCI configuration containing sensitive information, private keys, tokens, private datasets, generated artifacts, and caches out of version control. Use documented configuration inputs and safe examples.
* Do not print credentials or include them in logs, exceptions, command examples, or experiment reports.
* Make external data transfers explicit and keep them within the authorized scope.

## Python environment and dependencies

The Conda environment name is `codex-4-oci-aidp`. The user will create it separately; do not assume that it already exists or create it unless requested.

Use this environment for local Python development, notebooks, and checks. Activate it with `conda activate codex-4-oci-aidp`, or use `conda run -n codex-4-oci-aidp ...`. Do not silently fall back to globally installed packages. If the environment is unavailable, report which checks could not run.

Keep shared runtime dependencies in the repository root `requirements.txt` and development tools in the root `requirements-dev.txt`. Keep local settings in the root `.env` (ignored by Git), with safe placeholders in the root `.env.example`. Reuse these files across features instead of creating per-feature copies. Document the Python version, dependency constraints, OCI SDK and CLI versions, and target runtime used for verification. Do not assume Conda is available in OCI AI DP; provide setup instructions compatible with the selected remote runtime.

## Code, script, and notebook conventions

Every Python source file must begin with the following module header, using the actual modification date. An executable script may place its shebang before the header.

```python
"""
Author: L. Saetta
Date last modified: YYYY-MM-DD
License: MIT
Description: Brief description of this file's responsibilities.
"""
```

* Use accurate Google-style docstrings for public functions and classes, including relevant arguments, return values, and exceptions.
* Keep configuration, OCI service interactions, application logic, and command-line entry points separate where practical.
* Reuse `aidp_common/` for connection settings, authentication, SDK initialization and shared discovery. Keep feature operations in their own folders; feature modules must not import each other.
* Provide actionable errors and meaningful exit codes. Validate required inputs before starting remote changes.
* Shell scripts must document their purpose, prerequisites, inputs, side effects, and usage. Quote variables, handle failures explicitly, and state the supported shell.
* Keep notebooks focused, restartable from a clean kernel, and free of credentials or sensitive outputs. Move reusable logic into Python modules.
* Prefer `specs/` for specifications, `src/` for reusable code, `scripts/` for entry points and setup automation, `tests/` for tests, and `docs/` for guides and experiment reports. Create directories only when needed.

## macOS and OCI AI DP portability

* Design code to run in the documented OCI AI DP runtime as well as locally where feasible.
* Avoid mandatory macOS-only libraries, Apple Silicon assumptions, hard-coded filesystem paths, and platform-specific shell utilities.
* Use portable path handling and configurable storage locations. Make operating system, architecture, Python version, filesystem, network, and compute requirements explicit.
* If an operation requires an OCI-only capability, isolate that dependency and provide local tests using mocks or fixtures. Report the capability as unavailable locally rather than silently changing behavior.
* Keep runtime-specific dependencies and configuration separate when necessary. Document any differences between local and remote execution.
* Treat remote verification as a distinct acceptance step whenever a change depends on OCI services or the AI DP runtime.

## Remote setup through APIs and OCI CLI

* Prefer documented OCI SDK/API operations, OCI AI DP APIs, and OCI CLI commands that expose the required functionality. Explain the selected interface in the specification.
* Parameterize region, compartment, resource identifiers, endpoints, authentication configuration, and other environment-specific values. Provide sanitized configuration examples.
* Document supported authentication methods for each execution environment and the required IAM permissions. Use the least privileges needed for the specified operation.
* Before changing remote resources, validate the target and inputs and show the intended changes. Provide a plan or dry-run mode for provisioning scripts where feasible, clearly stating what it can validate.
* Make setup repeatable: detect existing resources, reuse or update them deliberately, and avoid duplicates on reruns. Document operations that cannot be idempotent.
* Handle asynchronous operations, timeouts, partial failures, and service errors explicitly. Bound polling and retries; retry only when safe for the operation.
* Log useful progress and sanitized resource references so a human can investigate failures. Record resources created by a run to support recovery and cleanup.
* Document potential costs and cleanup steps for resource-creating experiments. Keep cleanup scoped to explicitly identified resources; never delete resources solely because their names match a broad pattern.
* Run remote mutations only within the user's authorized scope. Do not treat credentials being available as authorization to create, delete, or reconfigure resources.

## Evaluation evidence

For each experiment, record:

* The question being tested and the linked specification and acceptance criteria.
* The actual Codex/Astra model or tool identifiers and versions, where available; never infer an exact identifier from a display name.
* The relevant prompts or instructions, with secrets and private data removed.
* Local and remote environment details, dependencies, configuration, commands, and expected outputs needed to reproduce the experiment.
* What was automated, what required human intervention, what failed, and any platform or tooling limitations.
* Observed results, supporting sanitized logs or artifacts, and whether each conclusion was verified locally or on OCI AI DP.

Separate observations from assumptions. Do not generalize from a single successful run or claim remote compatibility from mocked tests.

## Testing and verification

Use proportionate automated tests for meaningful behavior with `pytest` for Python. Format all Python code with Black and run Pylint and pytest before considering a Python change complete. Keep tool configuration and development dependencies documented. Unit tests must run without live cloud access or credentials; mock service clients or use small local fixtures. Cover configuration validation, relevant failure paths, and rerun behavior where applicable.

Keep live OCI integration tests explicit and opt-in. Document their prerequisites, target resources, expected costs, and cleanup. Never provision resources or upload data as a hidden side effect of ordinary test collection or the default unit test suite.

Run the relevant checks configured in the repository before declaring work complete. For remote functionality, record remote verification separately. If the Conda environment, credentials, network access, service availability, or remote runtime prevents a check, state exactly what remains unverified.

## Definition of done

A change is complete when:

* Its scope and acceptance criteria are captured in a specification.
* Code and scripts are understandable, documented, and limited to the specified behavior.
* Setup instructions, configuration examples, and relevant documentation reflect the change.
* Local and OCI AI DP runtime assumptions are explicit.
* Required checks have passed; any blocked verification is clearly reported, and acceptance criteria depending on it remain pending.
* Remote changes, where applicable, have reproducible setup and scoped cleanup or recovery instructions.
* Evaluation conclusions are supported by recorded evidence.
* No credentials, private data, or generated artifacts have been added to version control.
