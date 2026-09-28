# LangGraph agent templates

Reusable files for code-first LangGraph agents deployed to OCI AI Data
Platform. Copy them into the agent folder **unchanged**.

| File | Purpose |
| --- | --- |
| `llm_factory.py` | Returns the chat model for the current runtime. With `LOCAL=true` it uses `langchain_oci` and your OCI API-key profile (`OCI_PROFILE`); on AI DP it uses `aidputils` with the platform identity |
| `llm_config.example.json` | Model configuration. Copy it to `llm_config.json` next to `llm_factory.py`, fill in your compartment OCID, and keep `llm_config.json` out of Git |

In the agent code:

```python
from llm_factory import build_llm

llm = build_llm()
```

Verified on 2026-09-28 with `openai.gpt-5.4`, locally and on AI DP AI Compute.
See `specs/014-agent-runtime-facts-and-knowledge.md` for the runtime facts
this template relies on.
