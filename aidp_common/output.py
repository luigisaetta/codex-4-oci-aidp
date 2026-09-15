"""
Author: L. Saetta
Date last modified: 2026-09-15
License: MIT
Description: Shared execution banners and sanitized exception diagnostics.
"""

import os
import sys


def print_banner(
    operation,
    timestamp,
    elapsed=None,
    *,
    title="OCI AI DP cluster lifecycle",
    stream=None,
):
    """Print a UTC execution banner to the requested output stream."""
    phase = "START" if elapsed is None else "END"
    lines = [
        f"### {title} | {phase}",
        f"### Operation    : {operation}",
        f"### {phase.title() + ' time':13}: {timestamp.isoformat(timespec='seconds')}",
    ]
    if elapsed is not None:
        lines.append(f"### Elapsed time : {elapsed:.3f} seconds")
    border = "#" * 72
    print("\n".join([border, *lines, border]), flush=True, file=stream)


def report_unexpected_error(exc, stage):
    """Report exception type and code location without values, locals or bodies."""
    trace = exc.__traceback__
    while trace.tb_next:
        trace = trace.tb_next
    location = trace.tb_frame.f_code
    print(
        f"Error ({type(exc).__name__}) while {stage}. "
        f"Location: {os.path.basename(location.co_filename)}:"
        f"{trace.tb_lineno} ({location.co_name}). "
        "Exception details omitted to protect credentials and response data.",
        file=sys.stderr,
    )
