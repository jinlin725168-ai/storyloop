"""End-user metrics command, rendering, and save-boundary tests."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from storyloop.cli import main
from storyloop.metrics import build_metrics, save_metrics
from storyloop.metrics_render import render_metrics
from storyloop.models import StoryLoopError
from tests.metrics_support import metrics_project, write_change, write_report, write_run


def run_cli(*args: object) -> tuple[int, str, str]:
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main([str(arg) for arg in args])
    return code, stdout.getvalue(), stderr.getvalue()


def project_with_records(count: int = 3) -> Path:
    project = metrics_project()
    for number in range(1, count + 1):
        change_id = f"CHANGE-{number}"
        write_change(project, change_id)
        write_run(project, change_id, failures=[], agent_failures=[])
        write_report(project, change_id, failures=[], overrules=[])
    return project


def tree_digest(project: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in project.rglob("*") if item.is_file()):
        digest.update(path.relative_to(project).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def git(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def git_project_with_records() -> Path:
    project = project_with_records()
    git(project, "init", "-q")
    git(project, "config", "user.name", "Metrics Test")
    git(project, "config", "user.email", "metrics@example.invalid")
    git(project, "add", ".")
    git(project, "commit", "-qm", "fixture")
    return project


class MetricsCommandTests(unittest.TestCase):
    def test_empty_project_renders_without_error(self):
        result = build_metrics(metrics_project(), None, None)
        self.assertEqual(result["changes"], 0)
        self.assertIn("无数据", render_metrics(result))
        self.assertEqual(set(result["metrics"]), {
            "M1", "M1b", "M2", "M3", "M4", "M5", "M6",
            "M7", "M8", "M9", "M10", "M11", "M12",
        })

    def test_invalid_project_errors_are_in_chinese(self):
        missing = metrics_project() / "不存在"
        message = "metrics 项目必须是已存在的目录"
        operations = (
            ("build", lambda: build_metrics(missing, None, None)),
            ("save", lambda: save_metrics(
                missing, {"generated_at": "2026-10-20T00:00:00+00:00"})),
        )
        for name, operation in operations:
            with self.subTest(name=name), self.assertRaisesRegex(StoryLoopError, message):
                operation()

    def test_cli_json_and_text(self):
        project = project_with_records(3)
        code, text, error = run_cli("metrics", project)
        self.assertEqual((code, error), (0, ""))
        self.assertTrue(text.rstrip().endswith("以上建议由人决定是否采纳"))

        code, output, error = run_cli("metrics", project, "--json")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(json.loads(output)["status"], "METRICS")

    def test_metrics_is_read_only(self):
        project = project_with_records(3)
        before = tree_digest(project)

        code, _, error = run_cli("metrics", project)

        self.assertEqual((code, error), (0, ""))
        self.assertEqual(tree_digest(project), before)

    def test_save_writes_two_files_and_does_not_commit_or_clean_dirty_tree(self):
        project = git_project_with_records()
        dirty = project / "notes-未提交.txt"
        dirty.write_text("保持原样\n", encoding="utf-8")
        head = git(project, "rev-parse", "HEAD")

        code, output, error = run_cli("metrics", project, "--save", "--json")

        self.assertEqual((code, error), (0, ""))
        result = json.loads(output)
        paths = result["written"]
        self.assertEqual(sorted(Path(path).suffix for path in paths), [".json", ".md"])
        self.assertEqual(git(project, "rev-parse", "HEAD"), head)
        self.assertEqual(dirty.read_text(encoding="utf-8"), "保持原样\n")
        self.assertEqual(
            {path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()
             and path.is_relative_to(project / "reports/storyloop/metrics")},
            set(paths),
        )

    def test_bad_since_is_rejected(self):
        with self.assertRaises(SystemExit):
            run_cli("metrics", metrics_project(), "--since", "yesterday")

    def test_since_requires_dashed_year_month_day(self):
        with self.assertRaises(SystemExit):
            run_cli("metrics", metrics_project(), "--since", "20261020")

    def test_since_filters_by_approval_date(self):
        project = metrics_project()
        write_change(project, "OLD", approved_at="2026-09-30T23:59:59+00:00")
        write_change(project, "NEW", approved_at="2026-10-01T00:00:00+00:00")

        result = build_metrics(project, date(2026, 10, 1), None)

        self.assertEqual(result["changes"], 1)
        self.assertEqual(result["since"], "2026-10-01")

    def test_unknown_threshold_is_an_actionable_error(self):
        project = metrics_project()
        path = project / ".storyloop/config.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"metrics":{"thresholds":{"R99":{"threshold":1}}}}', encoding="utf-8")

        with self.assertRaisesRegex(StoryLoopError, "R99"):
            build_metrics(project, None, None)

    def test_save_refuses_symlink_parent_without_writing_outside(self):
        project = project_with_records()
        outside = Path(tempfile.mkdtemp(prefix="storyloop-metrics-outside-"))
        metrics_folder = project / "reports/storyloop/metrics"
        metrics_folder.parent.mkdir(parents=True, exist_ok=True)
        metrics_folder.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(StoryLoopError):
            save_metrics(project, build_metrics(project, None, None))
        self.assertEqual(list(outside.iterdir()), [])

    def test_render_includes_stats_and_all_rule_evidence(self):
        result = {
            "status": "METRICS", "generated_at": "2026-10-20T12:00:00+00:00",
            "since": None, "changes": 12,
            "metrics": {"M1": {"id": "M1", "value": 0.25, "numerator": 3,
                                  "denominator": 12, "n": 12, "changes": ["C1"],
                                  "missing": []}},
            "suggestions": [{"rule": "R3", "value": 0.4, "threshold": 0.2, "n": 12,
                             "evidence": ["C1", "C2"], "more": 2, "group": "light",
                             "rule_ids": ["L1-A"], "layers": ["acceptance", "smoke"],
                             "tiers": ["standard", "strong"],
                             "rules_unavailable": "UPGRADE_RULE_EVIDENCE_NOT_RECORDED",
                             "text": "考虑调整"}],
            "deferred": [], "manual_deferred": [], "missing": [], "discrepancies": [],
        }

        text = render_metrics(result)

        for expected in ("3/12", "n=12", "R3", "0.4", "0.2", "C1", "C2", "另有 2 个",
                         "light", "L1-A", "acceptance", "smoke", "standard", "strong"):
            self.assertIn(expected, text)
        self.assertIn("UPGRADE_RULE_EVIDENCE_NOT_RECORDED", text)
        self.assertEqual(text.splitlines()[-1], "以上建议由人决定是否采纳")

    def test_render_separates_insufficient_and_unavailable_rules(self):
        result = {
            "status": "METRICS", "generated_at": "2026-10-20T12:00:00+00:00",
            "since": None, "changes": 0, "metrics": {},
            "suggestions": [
                {"rule": "R1", "insufficient": "2/10"},
                {"rule": "R7", "unavailable": "CI_LAYER_EVIDENCE_NOT_RECORDED"},
            ],
            "deferred": [], "manual_deferred": [], "missing": [], "discrepancies": [],
        }

        text = render_metrics(result)

        insufficient = text.index("## 样本不足")
        unavailable = text.index("## 规则证据不可用")
        self.assertLess(insufficient, unavailable)
        self.assertIn("R1: 2/10", text[insufficient:unavailable])
        self.assertIn("R7: CI_LAYER_EVIDENCE_NOT_RECORDED", text[unavailable:])

    def test_render_escapes_record_derived_markdown(self):
        result = {
            "status": "METRICS", "generated_at": "2026-10-20T12:00:00+00:00",
            "since": None, "changes": 1, "metrics": {},
            "suggestions": [{"rule": "R11", "value": 0.7, "threshold": 0.5, "n": 5,
                             "evidence": [], "more": 0, "group": "review|evil\n# forged",
                             "tiers": ["strong|x"], "text": "检查默认档次"}],
            "deferred": [], "manual_deferred": [], "missing": [], "discrepancies": [],
        }

        text = render_metrics(result)

        self.assertNotIn("\n# forged", text)
        self.assertIn(r"review\|evil # forged", text)
        self.assertIn(r"strong\|x", text)

    def test_help_lists_metrics_flags(self):
        stdout = StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as caught:
            main(["metrics", "--help"])
        self.assertEqual(caught.exception.code, 0)
        output = stdout.getvalue()
        for flag in ("--since", "--ci-results", "--save", "--json"):
            self.assertIn(flag, output)


if __name__ == "__main__":
    unittest.main()
