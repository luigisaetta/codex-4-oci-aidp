# AI DP agent runtime facts and knowledge updates

## Problem and scope

On 2026-09-28 a diagnostic agent, `env_probe`, was deployed on the AI Compute
`aicomp02` with the `aidp-mcp` tools. It reported how the AI DP agent runtime
actually works. Several observed facts contradict the documentation, or the
assumptions built into this project and into `agents-4-ai-dp`:

* LangGraph is **not** 1.0.1 on the runtime;
* the entry file is renamed;
* files are shipped to an `app/` folder;
* `langchain-oci` is preinstalled.

This specification records the verified facts and brings the code,
documentation, harness, and skill references in line with them. It prepares
the ground for an agent-authoring skill, which is a separate, later
specification written after the order-agent recipe is validated.

Scope:

1. Record the verified runtime facts in one authoritative place.
2. Align `agents-4-ai-dp`:
   * local dependency pins;
   * `AGENTS.md`;
   * `README.md`;
   * the hello-world dependency file comment.
3. Make the local harness in `agents-4-ai-dp` reproduce the verified runtime
   layout, so that local tests catch layout mistakes.
4. Extend the `aidp-agent-deploy` skill reference with the new facts.
5. Two small MCP follow-ups observed during live use.
6. Record the first deployment through `deploy_aidp_agent` as live evidence
   in specification 013.

Non-goals:

* The agent-authoring skill.
* The order-processing agent. It follows the recipe
  `recipes/langgraph-order-agent.md`.
* Using `langchain-oci` on AI DP instead of `aidputils`. It is preinstalled,
  but its authentication on AI Compute is unverified.
* Removing or undeploying `env_probe`. It stays deployed as a diagnostic
  agent.

## Verified runtime facts

Source: `env_probe` deployed on `aicomp02`, `eu-frankfurt-1`, 2026-09-28. The
code is in `agents-4-ai-dp/agents/env_probe/`.

| Area | Fact |
| --- | --- |
| Python | 3.11.13 |
| Entry file | Copied and **renamed** to `<run-dir>/user_code.py`; `__file__` of the entry module points there |
| Agent folder | The uploaded folder, including subfolders, is shipped to `<run-dir>/app/` (for example `app/data/sample_catalog.json`). The platform adds `deploymentConfig.yaml` and a `<uuid>_requirements.txt` copy |
| Working directory | `<run-dir>` (the parent of `app/`) |
| `sys.path[0]` | `<run-dir>/app` → **sibling modules in the agent folder are importable** (for example `import llm_factory`) |
| Data files | Readable from `app/`. They are **not** next to `user_code.py`. Resolve them from a sibling module's `__file__`, or from `cwd / "app"` |
| Preinstalled packages | `langgraph` 1.2.4, `langchain` 1.3.9, `langchain-core` 1.4.6, **`langchain-oci` 0.3.1**, `langchain-community` 0.4.2, `pydantic` 2.13.5, `openai` 2.54.0, `oci` 2.184.1+preview |
| `aidputils` | Importable from `/agent/agent-runtime/…/site-packages`, with no distribution metadata (no version available) |
| LLM via `aidputils` | `init_oci_llm(OCIAIConf(model_provider="generic", model_id="openai.gpt-5.4", compartment_id=…, endpoint=…, model_args={}, guardrails_config={"policies": []}))` returns a `GenAIChatInvoker`. A plain call and `with_structured_output(<pydantic model>)` both work. Authentication is the platform identity (default `auth_type`); no keys are needed |
| Token usage | The `/chat` response reports only `totalTokens` for the LLM call (180 for the probe); input and output are 0 |
| Deploy timings | First deploy through `deploy_aidp_agent`: HTTP 201, 44 s. Redeploy: 46 s, `endpoint_stable=true` |

Verified locally on the same day:
* `langchain-oci` 0.3.2 with an API-key profile calls `openai.gpt-5.4` and
  `meta.llama-3.3-70b-instruct` correctly, including structured output;
* `cohere.command-a-03-2025` returned `None` for structured output;
* the documented pin of LangGraph 1.0.1 conflicts with the latest `langchain`
  (which requires LangGraph ≥ 1.2.11).

## Changes

### 1. `agents-4-ai-dp`: dependency pins and documentation

* `requirements-local.txt`: pin the runtime versions (`langgraph==1.2.4`,
  `langchain==1.3.9`, `langchain-core==1.4.6`, `langchain-oci==0.3.1`,
  `pydantic==2.13.5`), plus the development tools.
  * Resolve `oci` to a released version compatible with `langchain-oci`
    0.3.1. The runtime's `+preview` build is not on PyPI.
  * Run `pip check`. Record the final resolved set, and any necessary
    deviation, in the README.
* `AGENTS.md`:
  * replace "The runtime provides LangGraph 1.0.1" with the verified package
    list and its date;
  * state that the list must be re-verified with `env_probe` after platform
    updates;
  * add the rules:
    * entry-file renaming;
    * `app/` layout;
    * data resolution from sibling modules;
    * never list preinstalled packages in an agent's `requirements.txt`.
* `README.md`: update the local-development section and the "Observed on AI
  DP" section. Document `env_probe`:
  * what it reports;
  * how to invoke it (`"environment report"`, and `"… with llm"` for the
    model test);
  * that `probe_config.json` is local and excluded from Git.
* `agents/hello_world/requirements.txt`: update the comment.

### 2. `agents-4-ai-dp`: runtime-faithful local harness

Change `local_harness` so that loading an entry file reproduces the verified
layout **by default**:

* copy the agent folder into a temporary `<run-dir>/app/`;
* copy the entry file to `<run-dir>/user_code.py`;
* set the working directory to `<run-dir>`;
* put `<run-dir>/app` first on `sys.path`, then import `user_code.py`.

Keep the previous in-place loading as an explicit option, for example
`layout="in-place"`, for debugging.

Tests:

* an entry file that imports a sibling module works;
* a data file resolved from a sibling module's `__file__` works;
* a data file resolved from the entry file's own `Path(__file__).parent`
  fails, exactly as on AI DP. This is a regression guard;
* the existing hello-world and env_probe checks pass.

`scripts/run_local.py` uses the runtime-faithful layout by default.

### 3. `aidp-agent-deploy` skill reference

In `skills/aidp-agent-deploy/references/troubleshooting.md`:

* add a "Runtime layout and packages" section with the table above and its
  date;
* add these rows to the symptom table:
  * a file not found on AI DP but found locally → resolve data from a sibling
    module or from `app/`;
  * behavior differs from local → compare the local pins with the runtime
    list, re-probe with `env_probe`;
  * `ModuleNotFoundError` for a package → the package is not preinstalled; add
    it to the agent's `requirements.txt` and redeploy (the runtime logs
    `pip.install` events).

Keep `SKILL.md` short: add a single pointer to the new section.

### 4. MCP follow-ups (`codex-4-oci-aidp`)

* `deploy_aidp_agent`: `async_operation` is `null` after a successful
  deployment. Link the newest `DEPLOY_AGENT` operation for the agent key,
  with a start time at or after the request, whether it succeeded or failed.
  Fall back to `null` only when none is found within the wait.
* `invoke_aidp_agent`: the usage keys are named `input_tokens`,
  `output_tokens`, and `total_tokens`. Keep them. Document in the docstring
  and README that AI DP currently reports only the total for LLM calls.

### 5. Evidence in specification 013

Record in spec 013 "Verification evidence":

* the first deployment of `env_probe` through `deploy_aidp_agent` (HTTP 201,
  44 s);
* its redeploy (46 s, stable endpoint);
* the hello_world_api redeploy that served the new code;
* the async-operation listing results.

## Acceptance and verification

* `agents-4-ai-dp`:
  * `pip check` is clean with the new pins;
  * all tests pass, including the new harness layout tests;
  * Black and Pylint pass;
  * the hello-world and env_probe local runs work with the runtime layout.
* `codex-4-oci-aidp`:
  * suite green, Pylint 10.00/10 with no disables;
  * skill tests pass, including tool-name references;
  * the MCP snapshot is updated only if a docstring changes.
* Live, optional: redeploy `env_probe` after the harness change. Check that
  the report still shows the same layout and versions.

## Execution plan

One commit per repository step. `agents-4-ai-dp` is not under Git: record the
changes in its README instead.

1. `agents-4-ai-dp`: dependency pins and documentation (change 1).
2. `agents-4-ai-dp`: runtime-faithful harness and tests (change 2).
3. `codex-4-oci-aidp`: skill reference update (change 3).
4. `codex-4-oci-aidp`: MCP follow-ups and spec 013 evidence (changes 4
   and 5).

## Verification evidence

Pending implementation.
