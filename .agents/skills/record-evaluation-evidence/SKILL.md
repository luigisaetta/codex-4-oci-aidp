---
name: record-evaluation-evidence
description: Use when recording an experiment or evaluation result about Codex or Astra on OCI AI DP.
---

# Evaluation evidence

For each experiment, record:

* The question being tested and the linked specification and acceptance
  criteria.
* The actual Codex/Astra model or tool identifiers and versions, where
  available; never infer an exact identifier from a display name.
* The relevant prompts or instructions, with secrets and private data removed.
* Local and remote environment details, dependencies, configuration, commands,
  and expected outputs needed to reproduce the experiment.
* What was automated, what required human intervention, what failed, and any
  platform or tooling limitations.
* Observed results, supporting sanitized logs or artifacts, and whether each
  conclusion was verified locally or on OCI AI DP.

Separate observations from assumptions. Do not generalize from a single
successful run or claim remote compatibility from mocked tests.
