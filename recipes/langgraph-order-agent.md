# Recipe: build, test, and deploy a LangGraph order agent with Codex

A numbered sequence of requests to give Codex in **one interactive session**.
Codex builds a code-first LangGraph agent, tests it locally, and deploys it to
OCI AI Data Platform with the `aidp-mcp` tools and the `aidp-agent-deploy`
skill. For each step, the recipe explains what the step does and what result
to expect.

**The agent:**
1. It receives an English sentence, for example "Please ship a dozen A4
   notebooks".
2. It uses an LLM to extract the **product** and the **quantity**.
3. It checks availability in a simulated **catalog and warehouse**: JSON files
   shipped with the agent.
4. It answers positively or negatively, and explains why.

The runtime facts behind this recipe were verified on AI DP on 2026-09-28.
See `specs/014-agent-runtime-facts-and-knowledge.md`.

## Before you start

* The AI Compute (here `aicomp02`) is `ACTIVE`, and the AI DP AI Lakehouse is
  `AVAILABLE`.
* The `aidp-mcp` server is registered in Codex, and the skills are installed
  with `scripts/install_skills.sh`.
* `/Users/lsaetta/Progetti/agents-4-ai-dp` is listed in `AIDP_ALLOWED_ROOTS`.
* Open a **new Codex session** in `/Users/lsaetta/Progetti/agents-4-ai-dp`.
* When Codex shows a plan and asks for approval, read it and answer
  **"approved"**, or say what to change.

## Design rules (Codex reads these in step 1)

* **The LLM only extracts.** Product matching, the stock check, and the
  answer are deterministic Python.
* **One model factory, two runtimes.** `llm_factory.py` returns
  `langchain_oci.ChatOCIGenAI` (API-key profile) when `LOCAL=true`, and
  `aidputils` `init_oci_llm` otherwise. Import each library only in its own
  branch. Start from `agents/env_probe/llm_factory.py`, which is verified on
  AI DP.
* **Model:** `openai.gpt-5.4`, verified for structured output locally and on
  AI DP.
* **Business outcomes never raise.** The four outcomes are available,
  insufficient stock, unknown product, and not understood. Each one is an
  explicit answer. AI DP hides exception details, so only genuine bugs may
  raise.
* **Runtime layout.** On AI DP the entry file is renamed `user_code.py` and
  runs outside `app/`, which holds all the other files.
  * Keep the entry file thin.
  * Read data only from sibling modules, through their own `__file__`.
  * Sibling imports work: `app/` is first on `sys.path`.
* **Configuration out of code.** `order_config.json` holds `compartment_id`,
  `model_id`, and `endpoint`. It is excluded from Git;
  `order_config.example.json` is committed.
* **Dependencies:** the agent's `requirements.txt` lists no packages. LangGraph
  1.2.4, langchain 1.3.9, langchain-core 1.4.6, langchain-oci 0.3.1, and
  pydantic 2.13.5 are preinstalled on AI DP.

**Data**, chosen so that every test sentence has a deterministic outcome:

| sku | name | aliases | stock |
| --- | --- | --- | --- |
| MOUSE-W01 | wireless mouse | wireless mice | 25 |
| CHG-USBC65 | USB-C charger | USB-C chargers, usb c charger | 5 |
| NB-A4-80 | A4 notebook | A4 notebooks | 100 |

---

## 1. Align the local environment with the AI DP runtime

**What it does:** it creates a local Python environment with the same
LangGraph and LangChain versions as the AI DP runtime. Local tests are
meaningful only if the versions match.

```text
Create a Python 3.11 conda environment named agents-4-ai-dp. Install
langgraph==1.2.4, langchain==1.3.9, langchain-core==1.4.6,
langchain-oci==0.3.1, pydantic==2.13.5, pytest, black and pylint, with an
oci version compatible with langchain-oci. Run pip check. Update
requirements-local.txt with the resolved pins and fix any mention of
LangGraph 1.0.1 in AGENTS.md, README.md and agents/hello_world/requirements.txt.
Then run the existing tests.
```

**Expected result:**
* `pip check` reports no broken requirements;
* `requirements-local.txt` pins the runtime versions;
* the existing hello-world and contract tests pass.

## 2. Scaffold the agent

**What it does:** Codex creates the agent folder, the simulated data, the
model factory, and the configuration, following the design rules. No logic
yet.

```text
Read the "Design rules" section of
/Users/lsaetta/Progetti/codex-4-oci-aidp/recipes/langgraph-order-agent.md and
scaffold agents/order_agent accordingly: a thin entry file order_agent.py,
graph.py, llm_factory.py copied from agents/env_probe, inventory.py and
config.py that load their JSON files through their own __file__,
data/catalog.json and data/warehouse.json with the data table of the recipe,
order_config.example.json, and a local order_config.json with the values of
agents/env_probe/probe_config.json but model_id "openai.gpt-5.4". Add
order_config.json to .gitignore. requirements.txt must contain comments only.
Show me the tree when done.
```

**Expected result:**
* the tree of `agents/order_agent/` contains the files above;
* `order_config.json` is ignored by Git;
* `inventory.py` and `config.py` use `Path(__file__).parent`.

## 3. Implement the graph

**What it does:** it creates the three-node LangGraph workflow:
* **`extract`** calls the LLM with structured output;
* **`check_stock`** runs the deterministic catalog and warehouse lookup;
* **`respond`** builds the deterministic English answer.

```text
Implement the order agent: the extract node uses
build_llm(config).with_structured_output(Order), where Order is a pydantic
model {product: str | None, quantity: int | None}, and returns nulls when the
sentence is not an order. check_stock matches the product case-insensitively
against names and aliases and compares the quantity with the stock. respond
returns clear English answers for the four outcomes (available, insufficient
stock with the units available, unknown product, not understood). invoke()
must not raise for these outcomes. Keep all logic out of the entry file.
```

**Expected result:**
* the entry file only exposes the agent class;
* the logic lives in `graph.py` and `inventory.py`.

## 4. Test offline

**What it does:** it tests all the logic without a model or a network, using
a fake LLM. It also reproduces the AI DP layout (`user_code.py` + `app/`), so
that file-location mistakes show up now and not after the deploy.

```text
Write offline tests for the order agent with a fake LLM whose
with_structured_output returns predefined Order objects. Cover the four
outcomes, alias matching, and quantity edge cases (0, negative, missing).
Add one test that reproduces the AI DP layout: copy agents/order_agent into a
temporary app/ folder, copy the entry file to user_code.py next to it, put app/
first on sys.path, set the working directory to the parent, and invoke the
agent. Run pytest, Black and Pylint.
```

**Expected result:**
* all tests pass offline, including the AI DP layout test;
* Black and Pylint are clean.

## 5. Try it locally with the real model

**What it does:** it runs the agent on the laptop with `LOCAL=true`, so that
the factory uses `langchain_oci` with your API-key profile. It verifies the
real extraction quality before anything is deployed.

```text
Run the order agent locally with LOCAL=true (use my OCI_PROFILE) through
scripts/run_local.py for these sentences and show each answer:
1. "I'd like to order 3 wireless mice, please."
2. "Can you send us twelve USB-C chargers by Friday?"
3. "Please ship a dozen A4 notebooks."
4. "Do you sell laptops? I need 2."
5. "What's the weather like?"
```

**Expected result:**

| # | Outcome |
| --- | --- |
| 1 | available: 3 × MOUSE-W01 |
| 2 | insufficient stock: only 5 available |
| 3 | available: 12 × NB-A4-80 ("a dozen" → 12) |
| 4 | unknown product |
| 5 | not understood |

## 6. Deploy to AI DP (uses the `aidp-agent-deploy` skill)

**What it does:** this is a generic request that names neither the skill nor
the tools. Codex should load `aidp-agent-deploy` and run its workflow. It
confirms that the local tests pass, then:

1. **upload:** it plans the upload and waits for your approval;
2. **agent definition:** it plans the creation of the CODE agent and waits;
3. **compute:** it checks that the AI Compute is ACTIVE;
4. **deploy:** it plans the deployment and waits;
5. **smoke test:** it invokes the agent after your confirmation.

```text
Deploy the order agent to AI Data Platform as agent "order_agent" in
/Workspace/order_agent on compute aicomp02, and check that it works with the
sentence "Please ship a dozen A4 notebooks."
```

**Expected result:**
* **upload plan:** all files are `create`, including `data/` and
  `order_config.json`; after approval, uploaded equals verified;
* **agent plan:** `create`; the result is a CODE agent in `DRAFT`;
* **deploy plan:** `deploy`; after approval the deployment is `ACTIVE` in
  about 45 s, with its endpoint URL;
* **smoke test:** the answer confirms 12 × NB-A4-80, and `total_tokens` is
  greater than 0, which proves that an LLM call happened.

If Codex does not pick the skill, repeat the request starting with
`$aidp-agent-deploy`. Note this in the validation record: it is useful skill
feedback.

## 7. Run the business scenarios on AI DP

**What it does:** it runs the same five sentences as step 5 against the
deployed endpoint. It confirms that the platform behaves like the laptop,
and shows what the platform observes.

```text
Invoke order_agent on AI DP with the five sentences from the local test,
one invocation each, and compare every answer with the local result. For each
one show the answer, total_tokens and the trace summary.
```

**Expected result:**
* the same five outcomes as step 5;
* `total_tokens` > 0 for sentences 1–4. Sentence 5 also calls the model;
* the trace shows the graph nodes (`extract`, `check_stock`, `respond`) with
  their durations.

## 8. Change the data and redeploy (uses the skill again)

**What it does:** it simulates a business change. The redeploy after the
upload is the step most often forgotten, and the skill must do it on its own.

```text
We received new stock: set the USB-C charger stock to 20. Update the agent on
AI Data Platform and check that "Can you send us twelve USB-C chargers by
Friday?" is now accepted.
```

**Expected result:**
1. the offline tests are re-run;
2. the upload plan shows **only `data/warehouse.json` as `update`**;
3. the agent definition is `unchanged`;
4. the deploy plan is **`redeploy`**. After approval, `time_created` is newer
   and `endpoint_stable` is `true`;
5. sentence 2 is now **available**: 12 × CHG-USBC65.

## 9. Diagnose a failure (optional)

**What it does:** it shows how Codex investigates a failing agent. The
platform hides exception details, so Codex has to combine the remote signals
with local reproduction.

```text
If any invocation of order_agent failed, investigate: show the error returned
by the platform, the session messages and the trace, then reproduce the case
locally with the AI DP layout test and propose a fix.
```

**Expected result:**
* a structured diagnosis: platform error code, session and trace evidence,
  and a local reproduction;
* a fix is proposed but not applied.

## 10. Record the outcome

**What it does:** it records what worked and what did not. This record is the
input for the future agent-authoring skill.

```text
Fill in the "Validation record" section of
/Users/lsaetta/Progetti/codex-4-oci-aidp/recipes/langgraph-order-agent.md
with today's date, the model, the results of steps 5 to 8, whether the skill
was selected automatically in steps 6 and 8, and any deviation from this
recipe. Do not include OCIDs.
```

---

## Troubleshooting

| Symptom | Likely cause | What to ask Codex |
| --- | --- | --- |
| Works locally, "file not found" on AI DP | data read from the entry file's folder | "Resolve the data files from inventory.py's own location and redeploy." |
| `AIDP_USER_CODE_EXECUTION_ERROR` | a bug raised in the code; details are hidden | "Reproduce this case with the AI DP layout test and fix it." |
| The endpoint answers with old behavior | no redeploy after the upload | "Redeploy order_agent and re-run the smoke test." |
| `total_tokens` is 0 | the model was not called | "Check the LOCAL handling in llm_factory.py." |
| Structured output returns `None` | the model is not suitable | "Switch the model to openai.gpt-5.4 in order_config.json." |
| AI Compute errors with `InternalError` | the AI Lakehouse is stopped | Start the Lakehouse, then retry. |

## Cleanup

* The tools never delete. Keep `order_agent` deployed while it is being
  evaluated, and undeploy it manually from the UI when you are done.
* Stop `aicomp02` and the AI Lakehouse when no tests are planned:

  ```text
  Stop the AI Compute aicomp02.
  ```

  The Lakehouse is stopped from the OCI console.

## Validation record

To be filled in by step 10.
