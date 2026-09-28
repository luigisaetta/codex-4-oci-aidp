# Recipe: build and deploy a LangGraph order agent on OCI AI DP

This recipe builds a code-first LangGraph agent that handles a product order
request and deploys it to OCI AI Data Platform with Codex and the `aidp-mcp`
tools. It is the reference procedure from which a future agent-authoring
skill will be derived, once it has been validated end to end.

The agent:

1. receives an English sentence, for example "Please ship a dozen A4
   notebooks";
2. uses an LLM to **extract** the product and the quantity (structured
   output);
3. **checks availability** in a simulated catalog and warehouse (JSON files
   shipped with the agent code);
4. **answers** positively or negatively, and explains why.

The runtime facts this recipe relies on were verified on 2026-09-28. See
`specs/014-agent-runtime-facts-and-knowledge.md`.

## Design rules

* **The LLM only extracts.** Matching, stock checks, and the answer are
  deterministic Python, so they are testable without a model.
* **One model factory, two runtimes.** `llm_factory.py` returns
  `langchain_oci.ChatOCIGenAI` (API-key profile) when `LOCAL=true`, and
  `aidputils` `init_oci_llm` otherwise. Import each library only in its own
  branch. Reuse `agents/env_probe/llm_factory.py`.
* **Never raise from `invoke()` for business outcomes.** AI DP hides exception
  details from callers. Answer "not understood", "unknown product", or "not
  enough stock" explicitly. Let only genuine bugs raise, so that they show up
  as `AIDP_USER_CODE_EXECUTION_ERROR`.
* **Resolve data files from a sibling module**, never from the entry file. On
  AI DP the entry file is renamed `user_code.py` and lives outside `app/`,
  where the other files are.
* **Configuration out of code.** `order_config.json` holds `compartment_id`,
  `model_id`, and `endpoint`. It is excluded from Git; `order_config.example.json`
  is committed.
* **Model:** `openai.gpt-5.4`, verified for structured output locally and on
  AI DP. `meta.llama-3.3-70b-instruct` also works locally. Avoid
  `cohere.command-a-03-2025` for structured output.

## Target layout (in `agents-4-ai-dp`)

```text
agents/order_agent/
  order_agent.py            entry file: thin class with setup() and async invoke()
  graph.py                  StateGraph: extract -> check_stock -> respond
  llm_factory.py            LOCAL=true -> langchain_oci, otherwise aidputils
  inventory.py              loads the JSON files via its own __file__, and does the stock check
  config.py                 loads order_config.json via its own __file__
  order_config.json         local configuration (excluded from Git)
  order_config.example.json
  data/catalog.json         products: sku, name, aliases, unit
  data/warehouse.json       stock per sku
  requirements.txt          no packages (everything needed is preinstalled)
tests/
  test_order_agent.py       offline tests with a fake structured LLM
```

Suggested data, chosen to make the three smoke-test outcomes deterministic:

| sku | name | aliases | stock |
| --- | --- | --- | --- |
| MOUSE-W01 | wireless mouse | wireless mice | 25 |
| CHG-USBC65 | USB-C charger | USB-C chargers, usb c charger | 5 |
| NB-A4-80 | A4 notebook | A4 notebooks | 100 |

## Prerequisites

* The AI Compute (for example `aicomp02`) is `ACTIVE`, and the AI DP AI
  Lakehouse is `AVAILABLE`.
* The `aidp-mcp` server is registered, and the skills are installed
  (`scripts/install_skills.sh`).
* `agents-4-ai-dp` is listed in `AIDP_ALLOWED_ROOTS`.
* The local environment uses the pins from `requirements-local.txt`, aligned
  with the runtime by spec 014.
* **Spec 014 is implemented**, in particular the runtime-faithful local
  harness.

## Steps (Codex prompts, one per step)

Run the prompts in a Codex session opened in `agents-4-ai-dp`. Review the
result of each step before moving on.

### Step 1: scaffold, data, and configuration

```text
Create agents/order_agent following recipes/langgraph-order-agent.md in
codex-4-oci-aidp (layout, design rules, suggested data). Copy llm_factory.py
from agents/env_probe. Create order_config.example.json and a local
order_config.json with the same values as agents/env_probe/probe_config.json
but model_id "openai.gpt-5.4"; add order_config.json to .gitignore.
requirements.txt must contain comments only. Do not write the graph yet.
```

**Check:**
* `inventory.py` and `config.py` resolve files through their own `__file__`;
* `order_config.json` is ignored by Git.

### Step 2: graph and deterministic logic

```text
Implement graph.py and order_agent.py:
- extract node: build_llm(config).with_structured_output(Order) where Order is
  a pydantic model {product: str | None, quantity: int | None}; the prompt
  asks to return nulls when the sentence is not an order.
- check_stock node: match the product case-insensitively against catalog
  name and aliases (inventory.py), then compare the quantity with the
  warehouse stock.
- respond node: deterministic English answers for: available (confirm sku,
  product, quantity), insufficient stock (state available units), unknown
  product, not understood (missing product or quantity <= 0).
- invoke() never raises for these outcomes; build the LLM lazily in setup().
Keep the entry file thin: it only exposes the agent class.
```

**Check:** the entry file only defines the class; all logic lives in sibling
modules.

### Step 3: offline tests

```text
Add tests/test_order_agent.py using a fake LLM whose with_structured_output
returns predefined Order objects (no network). Cover the four outcomes, alias
matching, quantity parsing edge cases (zero, negative, None), and a contract
test through local_harness with the runtime-faithful layout. Run pytest,
Black and Pylint.
```

**Check:**
* all tests pass offline;
* the harness test uses the `app/` + `user_code.py` layout.

### Step 4: local integration with the real model

```text
Run the agent locally with LOCAL=true (OCI_PROFILE from my environment) through
scripts/run_local.py for these sentences and show the answers:
1. "I'd like to order 3 wireless mice, please."
2. "Can you send us twelve USB-C chargers by Friday?"
3. "Please ship a dozen A4 notebooks."
4. "Do you sell laptops? I need 2."
5. "What's the weather like?"
```

**Expected:**

| # | Outcome |
| --- | --- |
| 1 | available: 3 × MOUSE-W01 |
| 2 | insufficient stock: only 5 available |
| 3 | available: 12 × NB-A4-80 |
| 4 | unknown product |
| 5 | not understood |

### Step 5: deploy to AI DP

Either ask generically, which exercises the `aidp-agent-deploy` skill:

```text
Deploy the order agent to AI Data Platform as agent "order_agent" in
/Workspace/order_agent on compute aicomp02, and check that it works.
```

or run the tools explicitly, approving each plan:

```text
1. upload_aidp_agent_code local_dir=<absolute path>/agents/order_agent workspace_dir=/Workspace/order_agent (plan, then apply)
2. ensure_aidp_agent agent_name=order_agent workspace_dir=/Workspace/order_agent entry_file=order_agent.py dependencies_file=requirements.txt (plan, then apply)
3. deploy_aidp_agent agent_name=order_agent compute_name=aicomp02 (plan, then apply with wait=true)
```

**Check:**
* the upload lists all files, including `data/` and `order_config.json`;
* the deployment is `ACTIVE`, and the elapsed time is about 45 s.

### Step 6: smoke tests on AI DP

```text
Invoke order_agent with invoke_aidp_agent (confirm_invoke=true) for the five
sentences of step 4 and compare each answer with the expected outcome.
Report the session id, trace summary and total_tokens for each.
```

**Check:**
* same outcomes as step 4;
* `total_tokens` is greater than 0 (an LLM call happened);
* the trace shows the three graph nodes.

### Step 7: change, redeploy, verify

```text
Increase the stock of USB-C chargers to 20 in data/warehouse.json, run the
offline tests, upload (plan, approve, apply), redeploy with
deploy_aidp_agent, then re-run sentence 2 and confirm it is now available.
```

**Check:**
* the redeploy creates a new `time_created`, and the endpoint is stable;
* sentence 2 now succeeds.

## Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Works locally, file not found on AI DP | data resolved from the entry file | resolve from a sibling module's `__file__` |
| `AIDP_USER_CODE_EXECUTION_ERROR` | a bug raised in the code; details are hidden | reproduce with the runtime-faithful harness locally |
| Endpoint still answers with old behavior | no redeploy after the upload | `deploy_aidp_agent` (redeploy) |
| `total_tokens` is 0 | the extract node did not call the model | check the `LOCAL` handling and the factory branch |
| Structured output returns `None` | model not suitable | use `openai.gpt-5.4` or `meta.llama-3.3-70b-instruct` |
| AI Compute creation or startup fails with `InternalError` | AI Lakehouse stopped | start the Lakehouse |

## Cleanup

* Keep `order_agent` deployed while it is being evaluated. Undeploying is
  manual (UI); the tools never delete.
* Stop `aicomp02` and the AI Lakehouse when no tests are planned.

## Validation record

To be filled in after the first full run:
* the date;
* the model;
* the results of steps 4, 6, and 7;
* the deviations from this recipe.

This record is the input for the agent-authoring skill.
