# Demo script: LangGraph agent lifecycle on OCI AI DP with Codex

A five-minute live demo for a management audience. Codex explores,
invokes, changes, tests, uploads, and diagnoses a code-first LangGraph agent
on OCI AI Data Platform through the `aidp-mcp` tools.

The demo uses the `hello_world_api` agent. Its code is in the companion
repository `agents-4-ai-dp` (`agents/hello_world/`).

Redeploying on AI Compute is **not** part of the demo, because there is no
guarded redeploy tool yet. The demo stops after a verified upload, and says
so explicitly.

## Preparation (about 5 minutes before)

1. Check that the AI Compute `aicomp02` is `ACTIVE` and that the AI DP
   Autonomous AI Lakehouse is `AVAILABLE`. A stopped Lakehouse breaks the AI
   features without a clear error.
2. Open a **new Codex session** in the `agents-4-ai-dp` project folder.
3. Warm-up invocation:
   `Use invoke_aidp_agent on hello_world_api with message "warm-up" and confirm_invoke=true.`
   The expected answer is `hello world - you said: warm-up`.
4. Keep `agents/hello_world/hello_agent.py` open in the editor, and the AI DP
   Workbench UI open on the `hello_world_api` agent.
5. Silence notifications and enlarge the font of the Codex panel.

---

## Step 1: Introduction (30 s)

**Say:**
> "This is Codex, a coding agent, working on a local LangGraph agent project.
> It is connected to OCI AI Data Platform through our MCP server: 20 typed
> tools, where every change in AI DP is planned first and needs my explicit
> approval. Let me show you the loop in five minutes."

**Command:** none. Show the Codex panel and the agent file.

**Comment on:**
* the laptop is for development and AI DP is for execution;
* the **safety checks are built into the tools**, not left to prompts.

---

## Step 2: Inspect the agent on AI DP (30 s)

**Say:**
> "First, Codex looks at the agent already deployed on AI DP, using read-only
> tools."

**Command:**
```text
Use get_aidp_agent on hello_world_api and summarize its state and deployment in three lines.
```

**Comment on:**
* the agent is `DEPLOYED` and its deployment is `ACTIVE`;
* the endpoint URL;
* "This agent was uploaded and created by Codex itself, through the tools."

---

## Step 3: Invoke it live (45 s)

**Say:**
> "Now we call the deployed agent. Invoking has a cost, so the tool refuses
> unless I explicitly confirm."

**Command:**
```text
Use invoke_aidp_agent on hello_world_api with message "Hello from the management demo" and confirm_invoke=true. I authorize this invocation. Show the answer, the session id, and the trace summary.
```

**Comment on:**
* the answer: `hello world - you said: Hello from the management demo`;
* the **trace**: AI DP instruments LangGraph automatically, with spans
  `agent_invoke` → `LangGraph.workflow` → `respond.task` and their durations
  in milliseconds.

---

## Step 4: Change the code and test it locally (1 min 15 s)

**Say:**
> "Now a code change, written by Codex and tested locally in seconds, before
> anything touches the platform."

**Command:**
```text
In agents/hello_world/hello_agent.py change RESPONSE_PREFIX to "Hello from OCI AI Data Platform - you said: ". Update the tests and README accordingly. Run the local tests and show me the result. Do not upload anything.
```

**Comment on:**
* the diff is small and readable;
* the **local tests pass in seconds**: "We test the agent's contract on the
  laptop; no cloud is needed for this step."

---

## Step 5: Upload with plan and approval (1 min)

**Say:**
> "Now Codex prepares the upload. First a plan: nothing is written until I
> approve."

**Command:**
```text
Plan the upload of /Users/lsaetta/Progetti/agents-4-ai-dp/agents/hello_world to /Workspace/hello_world_api with upload_aidp_agent_code. Show me the plan and wait for my approval.
```

**Comment on:** the plan shows **one file as `update`** and one as
`unchanged`, detected by comparing checksums. "Only what changed will be
uploaded."

Then say "approved" and type:
```text
Approved. Apply it with overwrite=true.
```

**Comment on:** the result shows `uploaded: 1, verified: 1`. Then say:
> "The new version is now on AI DP, verified file by file. Putting it live on
> the endpoint needs a redeploy: the API works — we verified it — and
> wrapping it into a guarded tool with plan and confirmation is our next
> step. Until then, the running endpoint still serves the previous version,
> as you'll see in the next test."

---

## Step 6: The error path (45 s)

**Say:**
> "What happens when the agent's code fails? The version currently deployed
> has a deliberate failure trigger."

**Command:**
```text
Use invoke_aidp_agent on hello_world_api with message "FAIL: demo" and confirm_invoke=true. I authorize this invocation. Explain what the platform returned.
```

**Comment on:**
* the platform returns HTTP 400 with `AIDP_USER_CODE_EXECUTION_ERROR`: the
  failure is detected and reported in a structured way;
* "The platform hides the exception details from the caller, which is right
  for security. Diagnosis today relies on local tests, like the one we just
  ran; exposing more detail to authorized users is feedback for the product
  team."

---

## Step 7: Guardrail and wrap-up (30 s)

**Command (optional):**
```text
Delete the hello_world_api agent from AI DP.
```

**Comment on:** Codex answers that **it has no tool to delete**: "The tools
never delete resources, by design."

**Say, to close:**
> "From laptop to platform: explore, invoke, change, test, upload, and
> diagnose, all in natural language and under control. Next: automated
> redeploy, agents that use OCI GenAI models, and skills that package this
> whole workflow."

---

## If something goes wrong

* **Codex picks the wrong tool**, for example its own built-in agent tools:
  say explicitly "use the aidp-mcp tool …".
* **Step 5 is slow:** skip the apply and comment on the plan only.
* **An invocation fails for network reasons:** show the warm-up output as a
  fallback.

## After the demo

1. Redeploy `hello_world_api`, so that the endpoint serves the uploaded code.
2. Write the specification for a guarded redeploy tool, based on the verified
   behavior:
   * a redeploy recreates the deployment: `time_created` changes, while
     `time_updated` and `deployment_version` stay empty;
   * the endpoint URL stays stable.
3. Stop `aicomp02` and the AI DP Lakehouse to avoid unnecessary cost.
