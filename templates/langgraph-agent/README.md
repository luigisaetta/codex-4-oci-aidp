# LangGraph agent templates

Reusable files for code-first LangGraph agents deployed to OCI AI Data
Platform. They encode rules learned on the real platform, so that every new
agent starts correct.

| File | Copy to | Purpose |
| --- | --- | --- |
| `llm_factory.py` | the agent folder, **unchanged** | `build_llm()` returns the chat model for the current runtime: `langchain_oci` with your OCI API-key profile when `LOCAL=true`, `aidputils` with the platform identity on AI DP. `call_model_safely()` asks for structured output and never raises |
| `llm_config.example.json` | the agent folder, as `llm_config.json` | Model configuration. Fill in your compartment OCID and keep `llm_config.json` out of Git |
| `test_agent_contract_template.py` | the project's `tests/`, as `test_<agent>_contract.py` | Standard tests every agent must pass. Set `AGENT_DIR`, `ENTRY_FILE`, and `SAMPLE_MESSAGE`; do not weaken the tests |

## Using the model in an agent

```python
from llm_factory import build_llm, call_model_safely

# setup(): create the model once and pass it to the graph.
llm = build_llm()

# In a graph node:
result = call_model_safely(llm, MySchema, prompt)
if result.error == "model_unavailable":
    ...  # answer: "please try again later"
elif result.error == "invalid_model_output":
    ...  # answer: "request not understood"
else:
    value = result.value  # a validated MySchema instance
```

## What the contract tests enforce

* **The model is created once, in `setup()`**, not for every message.
* **A failing model does not crash the agent.** Network, authentication, or
  throttling errors become an answer. On AI DP, a crash shows only a generic
  `AIDP_USER_CODE_EXECUTION_ERROR`.
* **A model answer that does not match the schema does not crash the agent.**
* **The agent works with the AI DP file layout.** The folder is shipped to
  `app/`, and the entry file is renamed `user_code.py` outside it.
* **`requirements.txt` does not pin packages preinstalled on AI DP.**

The template was validated on 2026-09-28:
* on a compliant sample agent, all 5 tests pass;
* on a first version of the order agent, 3 tests failed, one for each real
  defect found in its review.

See `specs/014-agent-runtime-facts-and-knowledge.md` for the runtime facts.
