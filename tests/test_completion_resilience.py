"""Completion survives UI assets in the tree and a crash right after the implementation commit."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.manual_acceptance import record_manual_acceptance
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.smoke_runner import candidate_digest, git_visible_digest, implementation_digest
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload
from tests.test_product_spec import rich_product

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _services(change_id: str) -> LoopServices:
    def implement(project, context):
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": [], "facts": [
            {"statement": "implementation fact", "location": "modules/orders/service.py:1"}]}

    return LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id), implement=implement,
                        review=lambda p, c: {"verdict": "pass", "summary": "ok", "findings": [], "questions": [],
                                             "facts": [{"statement": "review fact",
                                                        "location": "modules/orders/service.py:1"}]})


def _approved(root: Path, with_ui_asset: bool) -> str:
    repository(root)
    if with_ui_asset:
        (root / "acceptance/ui/cap").mkdir(parents=True)
        (root / "acceptance/ui/cap/menu.spec.ts").write_text("// asset\n", encoding="utf-8")
        git(root, "add", "."); git(root, "commit", "-q", "-m", "ui asset")
    bootstrap_project(root, {"test_commands": []})
    value = rich_product(); value["questions"] = []
    state = create_change(root, "orders", value, now=lambda: NOW)
    approve_change(root, state["change_id"], now=lambda: NOW)
    return state["change_id"]


class CompletionResilienceTests(unittest.TestCase):
    def test_report_collection_sees_final_implement_and_review_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = _approved(root, with_ui_asset=False)
            done = run_change(root, change_id, _services(change_id))
            report = json.loads(Path(done["report"]).read_text(encoding="utf-8"))
            process = report["presentation"]["sections"]["4"]["items"]
            facts = [item["data"]["statement"] for item in process if item["id"].startswith("facts:")]
            self.assertEqual(facts, ["implementation fact", "review fact"])

    def test_completion_resumes_after_a_crash_following_the_implementation_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            value = rich_product(); value["questions"] = []
            value["stories"][0]["acceptance"].append({"id": "AC-2", "given": "g", "when": "a human looks",
                                                      "then": "it looks right", "kind": "manual"})
            state = create_change(root, "orders", value, now=lambda: NOW)
            change_id = state["change_id"]
            approve_change(root, change_id, now=lambda: NOW)
            paused = run_change(root, change_id, _services(change_id))
            self.assertEqual(paused["status"], "MANUAL_ACCEPTANCE_REQUIRED", paused)
            record_manual_acceptance(root, change_id, ["AC-2"], "checked", now=lambda: NOW)
            with patch("storyloop.project_loop_finish.generate_change_report", side_effect=RuntimeError("disk full")), \
                    self.assertRaises(RuntimeError):
                run_change(root, change_id, _services(change_id))
            subject = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=root, capture_output=True,
                                     text=True).stdout.strip()
            self.assertEqual(subject, f"feat({change_id}): implement approved product change")
            self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                                            text=True).stdout.strip(), "")
            # the run never recorded the commit; resuming must adopt it instead of demanding new changes
            state = load_run_state(root, change_id)
            self.assertNotIn("implementation_commit", state)
            done = run_change(root, change_id, _services(change_id))
            self.assertEqual(done["status"], "PASSED", done)
            self.assertTrue(load_run_state(root, change_id)["implementation_commit"].startswith(
                subprocess.run(["git", "rev-parse", f"{done['merge_commit']}^2^"], cwd=root, capture_output=True,
                               text=True).stdout.strip()[:7]))

    def test_generated_documents_do_not_change_the_implementation_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})
            value = rich_product(); value["questions"] = []
            value["stories"][0]["acceptance"].append({"id": "AC-2", "given": "g", "when": "a human looks",
                                                      "then": "it looks right", "kind": "manual"})
            state = create_change(root, "orders", value, now=lambda: NOW)
            change_id = state["change_id"]
            approve_change(root, change_id, now=lambda: NOW)
            paused = run_change(root, change_id, _services(change_id))
            self.assertEqual(paused["status"], "MANUAL_ACCEPTANCE_REQUIRED", paused)
            before = implementation_digest(root)
            # a crashed completion leaves engine outputs behind; they are not part of what the human accepted
            (root / "openspec/changes" / change_id / "design.md").write_text("## Context\nleftover\n", encoding="utf-8")
            (root / "acceptance/ui/cap").mkdir(parents=True)
            (root / "acceptance/ui/cap/x.spec.ts").write_text("// leftover\n", encoding="utf-8")
            self.assertEqual(implementation_digest(root), before)
            self.assertNotEqual(git_visible_digest(root), before)
            record_manual_acceptance(root, change_id, ["AC-2"], "checked", now=lambda: NOW)
            done = run_change(root, change_id, _services(change_id))
            self.assertEqual(done["status"], "PASSED", done)

    def test_superseding_several_stories_of_one_change_commits_its_traceability_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root, {"test_commands": []})

            def two_story_product():
                value = rich_product(); value["questions"] = []
                second = dict(value["stories"][0]); second.update({"id": "ST-2", "title": "Cancel a room", "depends_on": [],
                    "acceptance": [{"id": "AC-9", "given": "g", "when": "w", "then": "t", "kind": "automated"}]})
                value["stories"].append(second)
                return value

            def run_one(marker: str) -> str:
                state = create_change(root, "orders", two_story_product(), now=lambda: NOW)
                change_id = state["change_id"]
                approve_change(root, change_id, now=lambda: NOW)

                def implement(project, context):
                    target = project / "modules/orders" / f"{marker}.py"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("VALUE = 1\n", encoding="utf-8")
                    return {"summary": "done", "questions": []}

                def payload(project, s):
                    value = smoke_payload(change_id)
                    value["files"][0]["content"] = ("from pathlib import Path\n"
                        f"raise SystemExit(0 if Path('modules/orders/{marker}.py').is_file() else 1)\n")
                    value["manifest"]["tests"][0]["criteria"] = ["AC-1", "AC-9"]
                    return value

                services = LoopServices(generate_smoke=payload, implement=implement,
                                        review=lambda p, c: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []})
                result = run_change(root, change_id, services)
                self.assertEqual(result["status"], "PASSED", result)
                return change_id

            first = run_one("one")
            second = run_one("two")
            state = load_run_state(root, second)
            self.assertEqual({item["story_id"] for item in state["superseded"]}, {"ST-1", "ST-2"})
            self.assertEqual({item["change_id"] for item in state["superseded"]}, {first})


if __name__ == "__main__":
    unittest.main()


class CompletionCrashMatrixTests(unittest.TestCase):
    def test_all_delivery_windows_preserve_roles_commits_and_merge_topology(self):
        from tests.lifecycle_cases import approved_case, observed_services
        for window in ('implementation', 'verified', 'merge', 'merged', 'receipt', 'finalizing'):
            with self.subTest(window=window), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                change_id = approved_case(root, 'manual')
                calls = []
                services = observed_services(change_id, 'manual', calls)
                self.assertEqual(run_change(root, change_id, services)['status'], 'MANUAL_ACCEPTANCE_REQUIRED')
                record_manual_acceptance(root, change_id, ['AC-2'], 'checked')
                before_calls = list(calls)
                with self._crash(window), self.assertRaisesRegex(RuntimeError, 'delivery crash'):
                    run_change(root, change_id, services)
                existing = self._delivery_commits(root, change_id)
                done = run_change(root, change_id, services)
                self.assertEqual(done['status'], 'PASSED')
                self.assertEqual(calls, before_calls, 'recovery must not ask implement/review again')
                final = self._delivery_commits(root, change_id)
                for kind, commits in final.items():
                    self.assertEqual(len(commits), 1, (window, kind, commits))
                    if existing[kind]: self.assertEqual(commits, existing[kind])
                parents = git(root, 'rev-list', '--parents', '-n', '1', done['merge_commit']).split()
                self.assertEqual(len(parents), 3)
                self.assertEqual(git(root, 'rev-parse', done['merge_commit']+'^2'), done['report_commit'])

    def _crash(self, window):
        import storyloop.project_loop_finish as finish
        if window == 'implementation':
            return patch.object(finish, 'generate_change_report', side_effect=RuntimeError('delivery crash'))
        if window in {'verified', 'merged', 'finalizing'}:
            original = finish.save_run
            def save(project, state):
                original(project, state)
                if state['status'] == window.upper(): raise RuntimeError('delivery crash')
            return patch.object(finish, 'save_run', side_effect=save)
        name = 'finish_feature' if window == 'merge' else 'ensure_merge_receipt'
        original = getattr(finish, name)
        def deliver(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('delivery crash')
        return patch.object(finish, name, side_effect=deliver)

    def _delivery_commits(self, root, identifier):
        subjects = {'implementation': f'feat({identifier}): implement approved product change',
                    'report': f'docs({identifier}): add acceptance evidence report',
                    'receipt': f'docs({identifier}): record verified merge'}
        rows = [line.split('\t', 1) for line in git(root, 'log', '--format=%H%x09%s').splitlines()]
        return {kind: [sha for sha, text in rows if text == subject] for kind, subject in subjects.items()}
