"""Public metrics text and saved Markdown intervention formatting."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

from tests.metrics_support import metrics_project, write_change, write_run


HEADING = "## 人工介入（按变更）"


def run_metrics(*args: object) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, "-m", "storyloop", "metrics", *(str(arg) for arg in args)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True,
        text=True, encoding="utf-8", timeout=30, check=False,
    )
    return result.returncode, result.stdout, result.stderr


def event(change_id: str, kind: str, **extra: object) -> dict:
    return {"kind": kind, "change_id": change_id,
            "at": "2026-10-10T01:00:00+00:00", "stop": "REVIEW",
            "source": "test", **extra}


def intervention_project() -> Path:
    project = metrics_project()
    write_run(project, "CHANGE-B", intervention_events=[])
    events = [event("CHANGE-A", "replan"), event("CHANGE-A", "overrule"),
              event("CHANGE-A", "design_approval"),
              event("CHANGE-A", "overrule"),
              event("CHANGE-A", "acceptance_repair", automatic=True)]
    write_run(project, "CHANGE-A", intervention_events=events)
    return project


def section(output: str) -> list[str]:
    lines = output.splitlines()
    start = lines.index(HEADING)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
               len(lines))
    return [line for line in lines[start:end] if line]


class MetricsInterventionTextTests(unittest.TestCase):
    def test_summary_and_sorted_change_lines(self):
        project = intervention_project()
        code, output, errors = run_metrics(project)

        self.assertEqual((code, errors), (0, ""))
        l1 = next(line for line in output.splitlines() if line.startswith("| L1 |"))
        self.assertEqual(l1.split("|")[2].strip(), "非例行 3，例行 1，自动修复 1")
        self.assertEqual(section(output), [
            HEADING,
            "- CHANGE-A：非例行 3（overrule×2、replan×1）；例行 1（design_approval×1）；自动修复 1",
            "- CHANGE-B：非例行 0；例行 0；自动修复 0",
        ])

    def test_unknown_and_empty_changes(self):
        project = metrics_project()
        write_change(project, "CHANGE-NULL")
        code, output, errors = run_metrics(project)
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(section(output), [HEADING, "- CHANGE-NULL：无数据"])

        code, empty, errors = run_metrics(metrics_project())
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(section(empty), [HEADING, "- 无"])
        l1 = next(line for line in empty.splitlines() if line.startswith("| L1 |"))
        self.assertEqual(l1.split("|")[2].strip(), "无数据（NO_SAMPLE）")

    def test_json_unchanged_and_saved_markdown_matches_text(self):
        project = intervention_project()
        code, output, errors = run_metrics(project)
        self.assertEqual((code, errors), (0, ""))

        code, structured, errors = run_metrics(project, "--json")
        self.assertEqual((code, errors), (0, ""))
        l1 = json.loads(structured)["metrics"]["L1"]
        self.assertEqual(l1["summary"]["nonroutine"], 3)
        self.assertEqual(l1["by_change"]["CHANGE-A"]["nonroutine_types"],
                         {"overrule": 2, "replan": 1})

        code, saved, errors = run_metrics(project, "--save", "--json")
        self.assertEqual((code, errors), (0, ""))
        report = next(path for path in json.loads(saved)["written"] if path.endswith(".md"))
        markdown = (project / report).read_text(encoding="utf-8")
        self.assertEqual(section(markdown), section(output))
        self.assertEqual(
            next(line for line in markdown.splitlines() if line.startswith("| L1 |")),
            next(line for line in output.splitlines() if line.startswith("| L1 |")),
        )


if __name__ == "__main__":
    unittest.main()
