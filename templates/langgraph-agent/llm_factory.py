"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Runtime-aware LLM factory for code-first LangGraph agents on OCI AI DP.

Copy this file, unchanged, into the agent folder, next to llm_config.json.

* On the laptop, set LOCAL=true: the factory returns langchain_oci
  ChatOCIGenAI, authenticated with the OCI API-key profile named in
  OCI_PROFILE (default "DEFAULT").
* On AI DP (LOCAL not set), the factory returns the model from aidputils
  init_oci_llm, authenticated with the platform identity. No keys are needed.

Usage in the agent code:

    from llm_factory import build_llm, call_model_safely

    # In the agent's setup(): create the model once, then pass it to the graph.
    llm = build_llm()

    # In a graph node: never call the model directly.
    result = call_model_safely(llm, MySchema, prompt)
    if result.error == "model_unavailable":
        ...  # answer "please try again later"
    elif result.error == "invalid_model_output":
        ...  # answer "request not understood"
    else:
        value = result.value  # an instance of MySchema

call_model_safely never raises: model failures and outputs that do not match
the schema become a result with an error code, and are logged. AI DP hides
exception details from callers, so an agent must answer instead of crashing.

Verified on 2026-09-28 with openai.gpt-5.4, locally (langchain-oci 0.3.2)
and on AI DP AI Compute. Plain calls and structured output both work.
Sibling imports and files next to this module work on AI DP, because the
agent folder is shipped to app/, which is first on sys.path.
"""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(__file__).resolve().parent / "llm_config.json"
REQUIRED_KEYS = ("compartment_id", "model_id", "endpoint")
MODEL_UNAVAILABLE = "model_unavailable"
INVALID_MODEL_OUTPUT = "invalid_model_output"

logger = logging.getLogger("llm_factory")


@dataclass(frozen=True)
class ModelCallResult:
    """Outcome of one model call: a validated value or an error code.

    Attributes:
        value: Instance of the requested schema, or None on error.
        error: None on success, MODEL_UNAVAILABLE when the call failed, or
            INVALID_MODEL_OUTPUT when the answer did not match the schema.
    """

    value: Any = None
    error: str | None = None

    @property
    def ok(self):
        """Return whether the call produced a validated value."""
        return self.error is None


def is_local():
    """Return whether the agent runs on the developer laptop (LOCAL=true)."""
    return os.environ.get("LOCAL", "").strip().lower() == "true"


def load_config(path=CONFIG_FILE):
    """Read the model configuration stored next to this module.

    Args:
        path: Location of the JSON configuration file.

    Returns:
        dict: Configuration with compartment_id, model_id and endpoint.

    Raises:
        FileNotFoundError: The configuration file is missing.
        ValueError: A required key is missing or empty.
    """
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_KEYS if not config.get(key)]
    if missing:
        raise ValueError(f"llm_config.json is missing: {', '.join(missing)}")
    return config


def build_llm(config=None):
    """Return a LangChain chat model for the current runtime.

    Args:
        config: Optional configuration mapping; read from llm_config.json
            when omitted.

    Returns:
        A LangChain chat model supporting invoke() and
        with_structured_output().
    """
    config = config or load_config()
    if is_local():
        # pylint: disable=import-outside-toplevel,import-error
        from langchain_oci import ChatOCIGenAI

        return ChatOCIGenAI(
            model_id=config["model_id"],
            compartment_id=config["compartment_id"],
            service_endpoint=config["endpoint"],
            auth_type="API_KEY",
            auth_profile=os.environ.get("OCI_PROFILE", "DEFAULT"),
            model_kwargs=config.get("model_kwargs", {}),
        )
    # pylint: disable=import-outside-toplevel,import-error
    from aidputils.agents.toolkit.agent_helper import init_oci_llm
    from aidputils.agents.toolkit.configs import OCIAIConf

    return init_oci_llm(
        OCIAIConf(
            model_provider="generic",
            compartment_id=config["compartment_id"],
            endpoint=config["endpoint"],
            model_id=config["model_id"],
            model_args=config.get("model_kwargs", {}),
            guardrails_config={"policies": []},
        )
    )


def call_model_safely(llm, schema, prompt):
    """Ask the model for structured output without ever raising.

    Args:
        llm: Chat model returned by build_llm().
        schema: Pydantic model class describing the expected answer.
        prompt: Prompt text sent to the model.

    Returns:
        ModelCallResult: the validated value, or an error code.
        MODEL_UNAVAILABLE means the call itself failed (network,
        authentication, throttling). INVALID_MODEL_OUTPUT means the answer
        did not match the schema.
    """
    try:
        raw = llm.with_structured_output(schema).invoke(prompt)
    except ValueError as exc:
        # Pydantic ValidationError and LangChain OutputParserException are
        # ValueError subclasses: the model answered, but not in the schema.
        logger.warning("Model output rejected: %s", type(exc).__name__)
        return ModelCallResult(error=INVALID_MODEL_OUTPUT)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Model call failed: %s", type(exc).__name__)
        return ModelCallResult(error=MODEL_UNAVAILABLE)
    if isinstance(raw, schema):
        return ModelCallResult(value=raw)
    if isinstance(raw, dict):
        try:
            return ModelCallResult(value=schema.model_validate(raw))
        except ValueError as exc:
            logger.warning("Model output rejected: %s", type(exc).__name__)
    return ModelCallResult(error=INVALID_MODEL_OUTPUT)
