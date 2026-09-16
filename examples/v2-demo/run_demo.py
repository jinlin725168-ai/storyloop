"""Run the same governed demo once with each command adapter."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))

from storyloop.constitution import generate_constitutions
from storyloop.loop_config_io import load_loop_config, load_stage_payloads
from storyloop.run_artifacts import attempt_metrics
from storyloop.stage_coordinator import run_stage_graph


SOURCE = Path(__file__).resolve().parent


def run_one(adapter: str) -> dict:
    """Copy immutable inputs to an isolated project and return safe metrics."""
    with tempfile.TemporaryDirectory(prefix=f"storyloop-{adapter}-") as folder:
        project = Path(folder) / "project"
        shutil.copytree(SOURCE, project, ignore=shutil.ignore_patterns("__pycache__"))
        generate_constitutions(project)
        config = load_loop_config(project, "loop.json")
        payloads = load_stage_payloads(project, "payload.json", config)
        result = run_stage_graph(project, config, payloads,
                                 requested_adapter=adapter)
        return {"adapter": adapter, "status": result["status"],
                "final_judges": len(result["final"]),
                "metrics": attempt_metrics(project, config)}


def main() -> None:
    print(json.dumps({"protocol": "loop.demo.v2",
                      "runs": [run_one("agent-a"), run_one("agent-b")]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
