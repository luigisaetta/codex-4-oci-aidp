# `test00` notebook example

## Problem

A minimal, safe notebook is needed to validate the local-content portion of
the AI DP notebook workflow before any remote upload or compute job is
authorized.

## Scope

Add `notebooks/test00/test00.ipynb`. The notebook imports Python's standard
library `platform` and prints a short runtime description. It has no network,
OCI, file-system, credential, or remote-service side effects.

## Non-goals

This change does not upload the notebook, create or modify an AI DP job, or
start a cluster or job run.

## Assumptions and prerequisites

The notebook requires a Python kernel. `platform` is part of the Python
standard library, so no package installation is required.

## Intended behavior and acceptance criteria

When its code cell runs, the notebook prints `Python runtime: ` followed by
the interpreter version. The file is valid, non-empty Jupyter notebook JSON
and is kept in a dedicated folder for later explicit upload.

## Verification approach

Parse the `.ipynb` file as JSON locally and confirm that the intended import
and print statements are present. Remote AI DP verification remains pending
explicit authorization.

## Implementation status

Implemented locally on 2026-09-15. Remote upload and one explicitly authorized
job execution are verified below.

On 2026-09-15, the initial local notebook was corrected before any further
upload: its cell sources contained literal backslash-`n` sequences instead of
JSON newline escapes, which made the Python code invalid. The corrected local
notebook parses as Jupyter JSON and its code cell compiles successfully.

## Remote verification evidence

Verified on 2026-09-15 against the AI DP instance and workspace selected by
the sanitized local configuration:

* A read-only upload plan resolved the destination
  `notebooks/test00/test00.ipynb` and reported `create`.
* One authorized `apply=true`, `overwrite=false` upload completed successfully
  at that destination. The uploaded local content had SHA-256
  `2961645e4239a02ab427a1b4fd8b285f0276244a3f19bd40b4a71e081cb70e95`.
* No workflow job, job run, cluster action, or notebook execution was
  requested or performed.

The initially uploaded copy was manually removed before a corrected upload.
On 2026-09-15, a new read-only plan again reported `create`, followed by one
successful `apply=true`, `overwrite=false` upload of the corrected notebook to
the same destination. Its SHA-256 is
`d72df0e3cfaf72448c1cbb4c689630b75136950e1d5ed49bf080419239b298b1`.
No workflow job, job run, cluster action, or notebook execution was requested
or performed during the corrected upload.

On 2026-09-16, read-only inspection of the most recent `test00_job` run
confirmed a successful execution. The job run started at
`2026-09-16T12:29:58.030+02:00` and ended at
`2026-09-16T12:30:18.762+02:00`; its single notebook task reported
`Successfully executed notebook: /notebooks/test00/test00.ipynb`. AI DP did
not expose a task-output key or error trace for that run, so the notebook's
printed Python runtime text was not retrievable through the task-output API.
