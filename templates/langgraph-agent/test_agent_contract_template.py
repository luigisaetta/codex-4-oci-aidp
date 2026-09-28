"""
Author: L. Saetta
Date last modified: 2026-09-28
License: MIT
Description: Standard contract tests every LangGraph agent for OCI AI DP must pass.

Copy this file to the agent project's tests/ folder, for example as
tests/test_order_agent_contract.py, and set the three constants below.
Do not weaken the tests: they encode rules learned on the real platform.

The tests load the agent the way AI DP runs it: the agent folder is copied
to app/ (first on sys.path), and the entry file is copied to user_code.py in
the parent folder, which is also the working directory. The model returned
by llm_factory.build_llm() is replaced with fakes, so no network is used.

Rules checked:
* the model is created once, in setup(), not once per message;
* a failing model does not crash the agent: it answers instead;
* a model answer that does not match the schema does not crash the agent;
* the agent works with the AI DP file layout;
* requirements.txt does not pin packages preinstalled on AI DP.
"""

import asyncio
import importlib.util
import inspect
from pathlib import Path
import shutil
import sys

import pytest

# --- Set these three values for your agent --------------------------------
AGENT_DIR = Path(__file__).resolve().parents[1] / "agents" / "CHANGE_ME"
ENTRY_FILE = "CHANGE_ME.py"
SAMPLE_MESSAGE = "CHANGE ME: a typical user message for this agent"
# ---------------------------------------------------------------------------

PREINSTALLED = ("langgraph", "langchain", "langchain-core", "langchain-oci", "pydantic")


class FakeModel:
    """Fake chat model with a scripted behavior: 'fail', 'reject', or 'garbage'.

    with_structured_output() returns the same fake, so plain and structured
    calls behave identically.
    """

    def __init__(self, behavior):
        """Store the behavior used by every call."""
        self.behavior = behavior

    def with_structured_output(self, schema):
        """Return this fake, ignoring the schema."""
        del schema
        return self

    def invoke(self, prompt):
        """Fail, reject the output, or return something that is not the schema."""
        del prompt
        if self.behavior == "fail":
            raise RuntimeError("simulated model outage")
        if self.behavior == "reject":
            raise ValueError("simulated schema validation failure")
        return "this is not the requested schema"


def _agent_module_names():
    """Return module names defined by the agent folder."""
    return {path.stem for path in AGENT_DIR.glob("*.py")} | {"user_code"}


def _load_agent_class(tmp_path, monkeypatch, behavior):
    """Load the agent in the AI DP layout with a fake model; count build_llm calls."""
    app_dir = tmp_path / "app"
    shutil.copytree(AGENT_DIR, app_dir, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(AGENT_DIR / ENTRY_FILE, tmp_path / "user_code.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(app_dir))
    for name in _agent_module_names():
        monkeypatch.delitem(sys.modules, name, raising=False)

    import llm_factory  # pylint: disable=import-outside-toplevel,import-error

    calls = {"build_llm": 0}

    def fake_build_llm(config=None):
        del config
        calls["build_llm"] += 1
        return FakeModel(behavior)

    monkeypatch.setattr(llm_factory, "build_llm", fake_build_llm)

    spec = importlib.util.spec_from_file_location(
        "user_code", tmp_path / "user_code.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "user_code", module)
    spec.loader.exec_module(module)
    classes = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if obj.__module__ == "user_code"
        and callable(getattr(obj, "setup", None))
        and inspect.iscoroutinefunction(getattr(obj, "invoke", None))
    ]
    assert len(classes) == 1, "The entry file must define exactly one agent class."
    return classes[0], calls


def _answer_text(result):
    """Return the text of the last message of an invoke() result."""
    last = result["messages"][-1]
    content = last.get("content") if isinstance(last, dict) else last.content
    return content if isinstance(content, str) else str(content)


@pytest.mark.parametrize("behavior", ["fail", "reject", "garbage"])
def test_agent_answers_when_the_model_misbehaves(tmp_path, monkeypatch, behavior):
    """A failing or malformed model must produce an answer, not an exception."""
    agent_class, _ = _load_agent_class(tmp_path, monkeypatch, behavior)
    agent = agent_class()
    agent.setup()

    result = asyncio.run(agent.invoke(SAMPLE_MESSAGE))

    assert _answer_text(result).strip()


def test_model_is_created_once_in_setup(tmp_path, monkeypatch):
    """build_llm() is called once, however many messages are processed."""
    agent_class, calls = _load_agent_class(tmp_path, monkeypatch, "garbage")
    agent = agent_class()
    agent.setup()

    for _ in range(3):
        asyncio.run(agent.invoke(SAMPLE_MESSAGE))

    assert calls["build_llm"] == 1


def test_requirements_do_not_pin_preinstalled_packages():
    """Packages preinstalled on AI DP must not appear in requirements.txt."""
    lines = (AGENT_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
    packages = [
        line.split("=")[0].split("<")[0].split(">")[0].strip().lower()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not set(packages) & set(PREINSTALLED)
