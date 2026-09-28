---
name: aidp-agent-deploy
description: Deploy, redeploy, and smoke-test a code-first LangGraph agent on OCI AI DP through aidp-mcp. Not for notebooks or writing agent code.
---

# AI DP agent deploy

Use this workflow to deploy an existing code-first LangGraph agent through
`aidp-mcp`. Do not use notebooks, scripts, the SDK, the OCI CLI, or REST as a
fallback. If `aidp-mcp` is unavailable, stop and say so. Agent project paths
supplied to MCP upload tools must be absolute.

## Workflow

1. Run the agent project's local tests. Stop and report the failure if they do
   not pass.
2. Call `upload_aidp_agent_code` with `apply=false`, show the plan, obtain
   approval, then call it with `apply=true`. Require separate approval for
   `overwrite=true` when replacing remote files.
3. Call `ensure_aidp_agent` with `apply=false`, show the plan, obtain approval,
   then call it with `apply=true` when needed.
4. Call `get_cluster_status` for the selected AI Compute. If it is not
   `ACTIVE`, explain the cost and ask before `set_cluster_state`. Do not start
   compute without current-conversation approval and `confirm_action=true`.
5. Call `deploy_aidp_agent` with `apply=false`, show the plan, obtain approval,
   then call it with `apply=true`. This is mandatory after every code upload,
   even when no other agent definition changed.
6. Agree on a deterministic smoke-test message and expected answer. Obtain
   approval, then call `invoke_aidp_agent` with `confirm_invoke=true`. Compare
   the answer with the expected result. For agents that call a model, choose
   a message whose correct answer requires the model. Token counts and
   per-node spans are not reliable evidence on AI DP; see troubleshooting.
7. Report the endpoint, session ID, and trace summary. For any failure, read
   [troubleshooting](references/troubleshooting.md).

## Authorization and stopping

Every `apply=true`, `overwrite=true`, `confirm_invoke=true`, `confirm_action=true`,
or compute-start action needs explicit approval for that specific action in the
current conversation. Approval for one action does not authorize another.

Stop after one failed deployment or smoke test and report the result. Never
retry deployments automatically.
