# Recipe: build, test, and deploy an order agent on OCI AI DP with Codex

A numbered sequence of plain-language requests to give Codex in **one
interactive session**. You do not need to know LangChain or LangGraph. You
describe what the agent must do, and Codex writes, tests, and deploys it with
the `aidp-mcp` tools and the `aidp-agent-deploy` skill. For each step, the
recipe explains what happens and what result to expect.

**The agent handles a product order request:**

1. it receives an English sentence, for example "Please ship a dozen A4
   notebooks";
2. it understands **which product** and **how many units** are requested,
   using an LLM;
3. it checks availability in a simulated **catalog and warehouse**;
4. it answers positively or negatively, and explains why.

## Before you start

* The AI Compute (here `aicomp02`) is `ACTIVE`, and the AI DP AI Lakehouse is
  `AVAILABLE`.
* The `aidp-mcp` server is registered in Codex, and the skills are installed
  with `scripts/install_skills.sh`.
* `/Users/lsaetta/Progetti/agents-4-ai-dp` is listed in `AIDP_ALLOWED_ROOTS`.
* Open a **new Codex session** in `/Users/lsaetta/Progetti/agents-4-ai-dp`.
* When Codex shows a plan and asks for approval, read it and answer
  **"approved"**, or say what to change.

---

## 1. Prepare the local environment

**What it does:** it creates a local Python environment with the same library
versions as AI DP. Local tests are meaningful only if the versions match.

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
* `pip check` reports no problems;
* `requirements-local.txt` lists the AI DP versions;
* the existing tests pass.

## 2. Create the agent project

**What it does:** Codex creates the agent folder and copies two ready-made
pieces from the templates:
* the **model connector** (`llm_factory.py`), which works both on your laptop
  and on AI DP and never lets a model problem crash the agent;
* the **standard tests** that every agent must pass.

```text
Create a new agent called order_agent, following the "Rules for Codex" section
of /Users/lsaetta/Progetti/codex-4-oci-aidp/recipes/langgraph-order-agent.md.
Copy llm_factory.py and llm_config.example.json from
/Users/lsaetta/Progetti/codex-4-oci-aidp/templates/langgraph-agent into the
agent folder and use llm_factory.py as is. Create llm_config.json with the
compartment and endpoint of agents/env_probe/probe_config.json and the model
openai.gpt-5.4, and keep it out of Git. Copy test_agent_contract_template.py
from the same templates folder to tests/test_order_agent_contract.py and set
its three constants for this agent. Show me the folder when done.
```

**Expected result:**
* a new `agents/order_agent/` folder containing the model connector and its
  configuration;
* `llm_config.json` is excluded from Git;
* `tests/test_order_agent_contract.py` exists. It fails for now, because the
  agent does not exist yet.

## 3. Describe the agent's behavior

**What it does:** you describe the business behavior in plain words. Codex
turns it into the agent's workflow and code, following the rules in this
recipe.

```text
Build the order agent. It receives a customer message in English and must:
- understand which product and how many units the customer wants (use the
  model only for this);
- look the product up in our catalog and check the warehouse stock, using
  the data in the "Catalog and warehouse" table of the recipe;
- answer in English with one of five outcomes:
  1. confirmed: the product and quantity are available (mention the product
     code);
  2. not enough stock: say how many units are available;
  3. product not sold: we do not have it in the catalog;
  4. request not understood: it is not an order, or the quantity is missing
     or invalid;
  5. service unavailable: the model cannot be reached; ask to try again
     later.
Recognize product names tolerantly: ignore upper/lower case, hyphens and
extra spaces, accept singular and plural, and the other spellings listed in
the catalog ("USB C chargers" is the USB-C charger). The agent must always
answer, never crash.
```

**Expected result:** Codex explains how it organized the agent, then creates
the code. The model is prepared once when the agent starts, and it is always
called through the safe helper of the model connector:
* one step that understands the request;
* one step that checks the stock;
* one step that writes the answer.

The catalog and warehouse data ship with the agent.

## 4. Test it without the real model

**What it does:** it checks all the business rules quickly, without calling
the LLM or the cloud. It also simulates how files are arranged on AI DP, so
that deployment problems are caught on the laptop.

```text
Test the order agent without calling the real model. Cover all five answers,
product names written in different ways, and tricky quantities: numbers in
words, "a dozen", zero, negative, and missing. The standard contract tests
must pass unchanged. Also check that the agent still works with the file
arrangement used on AI DP. Run the code checks too.
```

**Expected result:**
* all tests pass, including the **standard contract tests**:
  * the model is created once;
  * a failing model gets an answer;
  * a malformed model answer gets an answer;
  * the AI DP file layout works;
  * `requirements.txt` is clean;
* the code checks are clean: Black, and Pylint 10.00/10 with no disabled
  checks.

## 5. Try it on the laptop with the real model

**What it does:** it runs the agent on your laptop against the real OCI
Generative AI model. It verifies that the model understands the requests
well before anything is deployed.

```text
Try the order agent on my laptop with the real model (local mode, my OCI
profile) with these messages and show each answer:
1. "I'd like to order 3 wireless mice, please."
2. "Can you send us twelve USB-C chargers by Friday?"
3. "Please ship a dozen A4 notebooks."
4. "Do you sell laptops? I need 2."
5. "What's the weather like?"
```

**Expected result:**

| # | Outcome |
| --- | --- |
| 1 | confirmed: 3 wireless mice (MOUSE-W01) |
| 2 | not enough stock: only 5 available |
| 3 | confirmed: 12 A4 notebooks (NB-A4-80), because "a dozen" means 12 |
| 4 | product not sold |
| 5 | request not understood |

## 6. Deploy to AI DP

**What it does:** a generic request that names neither a skill nor a tool.
Codex should use the `aidp-agent-deploy` skill. It checks that the tests pass,
then uploads the code, creates the agent, checks the compute, deploys, and
runs a first check. It asks for your approval before each change.

```text
Deploy the order agent to AI Data Platform as "order_agent" in
/Workspace/order_agent on compute aicomp02, and check that it works with the
message "Please ship a dozen A4 notebooks."
```

**Expected result**, one approval at a time:
1. **upload plan:** all files are new; after approval, every file is uploaded
   and verified;
2. **agent plan:** a new agent is created;
3. **deploy plan:** a first deployment; after approval it is ready in about
   45 seconds;
4. **check:** the answer confirms 12 A4 notebooks, and the token count is
   above 0, which proves that the model was called on AI DP.

If Codex does not follow this flow on its own, repeat the request starting
with `$aidp-agent-deploy`, and note it in the validation record.

## 7. Run the business scenarios on AI DP

**What it does:** it runs the same five messages against the deployed agent,
to confirm that AI DP behaves like the laptop.

```text
Send the five messages from the laptop test to the deployed order_agent and
compare every answer with the laptop result. For each one, show the answer,
the tokens used and the steps recorded in the trace.
```

**Expected result:**
* the same five outcomes as step 5;
* tokens above 0;
* each trace shows the agent's steps and their durations.

## 8. Change the data and update the agent

**What it does:** it simulates a business change. Updating a deployed agent
needs a new deployment after the upload, and the skill must do it on its own.

```text
We received new stock: we now have 20 USB-C chargers. Update the agent on AI
Data Platform and check that "Can you send us twelve USB-C chargers by
Friday?" is now confirmed.
```

**Expected result:**
1. the tests are re-run;
2. the upload plan shows **only the warehouse file** as changed;
3. the agent definition is unchanged;
4. Codex **redeploys**. The endpoint address stays the same;
5. the message is now **confirmed**: 12 USB-C chargers (CHG-USBC65).

## 9. Investigate a failure (optional)

**What it does:** it shows how Codex investigates when the deployed agent
fails. AI DP does not reveal error details to callers, so Codex has to
combine what the platform shows with a reproduction on the laptop.

```text
If any request to order_agent failed, investigate: show what AI DP returned,
the conversation and the trace, reproduce the problem on my laptop, and
propose a fix without applying it.
```

**Expected result:** a clear explanation, with evidence from AI DP and a
local reproduction, and a proposed fix.

## 10. Record the outcome

**What it does:** it keeps a record of what worked. This record is the basis
for a future skill that automates agent authoring.

```text
Fill in the "Validation record" section of
/Users/lsaetta/Progetti/codex-4-oci-aidp/recipes/langgraph-order-agent.md
with today's date, the model, the results of steps 5 to 8, whether the deploy
skill was used automatically in steps 6 and 8, and any deviation from the
recipe. Do not include OCIDs.
```

---

## Catalog and warehouse

The data is chosen so that every test message has a predictable outcome.

| Product code | Name | Other spellings | Stock |
| --- | --- | --- | --- |
| MOUSE-W01 | wireless mouse | wireless mice | 25 |
| CHG-USBC65 | USB-C charger | USB-C chargers, usb c charger | 5 |
| NB-A4-80 | A4 notebook | A4 notebooks | 100 |

## Rules for Codex

Technical rules that Codex applies when it builds the agent. They are based on
facts verified on AI DP on 2026-09-28; see
`specs/014-agent-runtime-facts-and-knowledge.md`.

* **Model access.** Use `templates/langgraph-agent/llm_factory.py` unchanged:
  `from llm_factory import build_llm, call_model_safely`.
  * It reads `llm_config.json` next to itself, uses `langchain_oci` when
    `LOCAL=true`, and `aidputils` on AI DP.
  * Do not import `langchain_oci` or `aidputils` anywhere else.
* **Create the model once.** Call `build_llm()` in the agent's `setup()` and
  pass the model into the graph builder. Tests pass a fake model the same
  way.
* **Call the model only through `call_model_safely(llm, schema, prompt)`.**
  It never raises, and maps its error codes to answers:
  * `model_unavailable` → "service unavailable, try again later";
  * `invalid_model_output` → "request not understood".
* **Workflow.** A LangGraph `StateGraph` with three nodes:
  * `understand` extracts `{product, quantity}` with structured output,
    through a pydantic model with optional fields;
  * `check_stock` is deterministic: a case-insensitive match against names
    and aliases, then a stock comparison;
  * `respond` builds a deterministic English answer.

  The model is used only in `understand`.
* **Empty extractions.** The model may return the *string* `"null"`, an empty
  string, or quantity `0` for non-orders. Treat an empty or `"null"` product,
  and a quantity that is missing or ≤ 0, as "request not understood".
* **Never raise for business outcomes or model problems.** Return one of the
  five answers. Let only genuine bugs raise, because AI DP hides exception
  details and reports only `AIDP_USER_CODE_EXECUTION_ERROR`.
* **Tolerant matching** is a business requirement of this agent. Normalize
  case, hyphens, and spaces, accept singular and plural, then compare with
  names and aliases.
* **Runtime layout.** On AI DP the entry file is renamed `user_code.py` and
  runs outside `app/`, which holds all the other files; `app/` is first on
  `sys.path`. Therefore:
  * keep the entry file thin: only the agent class with `setup()` and
    `async invoke(user_query, **kwargs)`;
  * put the logic in sibling modules;
  * load the data files from a sibling module through its own `__file__`
    (for example `inventory.py` loading `data/catalog.json` and
    `data/warehouse.json`).
* **Standard contract tests.** Copy
  `templates/langgraph-agent/test_agent_contract_template.py` and set its
  three constants.
  * Do not weaken them.
  * They load the agent in the AI DP layout (`app/` + `user_code.py`) with
    fake models that fail, reject, or return garbage.
  * They check that `build_llm()` runs once and that `requirements.txt` is
    clean.
* **Business tests** use a fake model whose `with_structured_output(...)`
  returns predefined results. No network.
* **Code checks:** Black, and Pylint 10.00/10. Do not add Pylint disables in
  agent code.
* **Local run:** `LOCAL=true` and `OCI_PROFILE` from the user's environment;
  `scripts/run_local.py` needs the agent folder on `PYTHONPATH`.
* **Dependencies:** the agent's `requirements.txt` lists no packages. The
  needed libraries are preinstalled on AI DP (LangGraph 1.2.4, langchain
  1.3.9, langchain-core 1.4.6, langchain-oci 0.3.1, pydantic 2.13.5).
* **Deployment:** use the `aidp-mcp` tools through the `aidp-agent-deploy`
  skill. Every change needs the user's approval, and every code upload is
  followed by a redeploy.

## Troubleshooting

| Symptom | Likely cause | What to ask Codex |
| --- | --- | --- |
| Works on the laptop, fails on AI DP with "file not found" | data read from the wrong place | "Load the data files from inventory.py's own location, test the AI DP arrangement, and redeploy." |
| AI DP returns `AIDP_USER_CODE_EXECUTION_ERROR` | a bug in the code; details are hidden | "Reproduce this message on my laptop with the AI DP arrangement and fix it." |
| The deployed agent still behaves the old way | no new deployment after the upload | "Redeploy order_agent and check it again." |
| Token count is 0 | the model was not called | "Check that the agent uses llm_factory unchanged." |
| Every request answers "service unavailable" | the model call fails (configuration, region, or permissions) | "Run the agent on my laptop with the real model and show the model error logged by llm_factory." |
| The model never understands the request | model not suitable | "Use openai.gpt-5.4 in llm_config.json." |
| AI Compute errors with `InternalError` | the AI Lakehouse is stopped | Start the Lakehouse, then retry. |

## Cleanup

* The tools never delete. Keep `order_agent` deployed while it is being
  evaluated, and undeploy it manually from the UI when you are done.
* Stop the compute when no tests are planned:

  ```text
  Stop the AI Compute aicomp02.
  ```

  Stop the AI Lakehouse from the OCI console.

## Validation record

To be filled in by step 10.

### Lessons learned (2026-09-28, first run up to step 3)

The review of the first generated agent found these defects. They led to the
safe helper, the standard contract tests, and the "Rules for every agent" in
`agents-4-ai-dp/AGENTS.md`:

| Defect | Now prevented by |
| --- | --- |
| A failing model crashed the agent | `call_model_safely` + contract test `fail` |
| A model answer outside the schema crashed the agent | `call_model_safely` + contract test `reject` |
| The model was re-created for every message | the rule "create the model in `setup()`" + contract test `test_model_is_created_once_in_setup` |
| "USB C chargers" was not recognized | the tolerant-matching requirement in step 3 |
| Pylint below 10 | the code-check rule |
