---
name: oci-remote-operations
description: Use when designing or implementing code or scripts that create, modify, or delete OCI or AI DP resources.
---

# Remote setup through APIs and OCI CLI

* Prefer documented OCI SDK/API operations, OCI AI DP APIs, and OCI CLI
  commands that expose the required functionality. Explain the selected
  interface in the specification.
* Parameterize region, compartment, resource identifiers, endpoints,
  authentication configuration, and other environment-specific values. Provide
  sanitized configuration examples.
* Document supported authentication methods for each execution environment and
  the required IAM permissions. Use the least privileges needed for the
  specified operation.
* Before changing remote resources, validate the target and inputs and show the
  intended changes. Provide a plan or dry-run mode for provisioning scripts
  where feasible, clearly stating what it can validate.
* Make setup repeatable: detect existing resources, reuse or update them
  deliberately, and avoid duplicates on reruns. Document operations that
  cannot be idempotent.
* Handle asynchronous operations, timeouts, partial failures, and service
  errors explicitly. Bound polling and retries; retry only when safe for the
  operation.
* Log useful progress and sanitized resource references so a human can
  investigate failures. Record resources created by a run to support recovery
  and cleanup.
* Document potential costs and cleanup steps for resource-creating experiments.
  Keep cleanup scoped to explicitly identified resources; never delete
  resources solely because their names match a broad pattern.
* Run remote mutations only within the user's authorized scope. Do not treat
  credentials being available as authorization to create, delete, or
  reconfigure resources.
