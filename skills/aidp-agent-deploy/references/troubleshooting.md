# AI DP code-agent deployment troubleshooting

Use these verified observations after the workflow stops. Do not retry a
deployment automatically; first inspect the reported state and use the named
read-only tool where applicable.

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| AI Compute creation returns `InternalError` | The AI Lakehouse is stopped | Check that the AI Lakehouse is running before creating or starting compute. (Verified 2026-09-28.) |
| The endpoint serves old code | The code upload was not followed by a redeploy | Run `deploy_aidp_agent` plan and, after approval, apply the redeploy. (Verified 2026-09-28.) |
| `/chat` returns HTTP 400 `AIDP_USER_CODE_EXECUTION_ERROR` | Agent user code raised an error | Reproduce with the local contract tests. AI DP does not expose the exception text through sessions, traces, or runtime logs. (Verified 2026-09-28.) |
| A session is not continued | The continuation header was omitted | Pass `session_key` to `invoke_aidp_agent`; it is sent as `x-session-id`. (Verified 2026-09-28.) |
| `get_cluster_status` cannot find the expected compute | The cluster is not AI Compute or is not visible in the workspace | Check the exact name and that it is an AI Compute; AI Compute lookup uses its explicit cluster type. (Verified 2026-09-28.) |
| Deployment state is `FAILED` | Platform deployment failed | Call `list_aidp_async_operations` with `resource_type="AGENT"`, then report the bounded deployment error. (Verified 2026-09-28.) |

Additional verified behavior: redeployment recreates the deployment while the
endpoint URL is expected to remain stable, and requests can fail briefly while
redeployment is in progress. Agent runtime deployment events are written to the
workspace compute log in the AI DP log group, but user-code exception text is
not available there. (Verified 2026-09-28.)
