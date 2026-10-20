"""Human-readable and complete JSON output for CLI results."""

from __future__ import annotations

import json

from .change_log import render_log
from .memory_cli import render_memory
from .metrics_render import render_metrics
from .stop_report import render_stop


def display(result: dict, complete_json: bool) -> None:
    if complete_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("status") == "LOG":
        print(render_log(result))
        return
    if result.get("status") == "METRICS":
        print(render_metrics(result))
        return
    rendered = render_memory(result)
    if rendered is not None:
        print(rendered)
        return
    print(result.get("status", "REPORT"))
    for key in ("change_id", "project", "next", "report", "pending", "accepted", "paths", "written"):
        if key in result and result[key] is not None:
            print(f"{key}: {result[key]}")
    if isinstance(result.get("stop"), dict):
        for line in render_stop(result["stop"]):
            print(line)
    for warning in result.get("warnings", []):
        print(f"warning: {warning}")
