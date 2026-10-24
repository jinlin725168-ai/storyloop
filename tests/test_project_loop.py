from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.lesson_support import cleanup_repo
from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project
from tests.routing_support import legacy_create_change as create_change
from storyloop.project_loop import LoopServices, run_change
from storyloop.lesson_store import lessons_digest
from tests.support import product
from tests.test_baseline_behavior import git, repository


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


def smoke_payload(change_id: str) -> dict:
    source = (
        "from pathlib import Path\n"
        "path = Path('modules/orders/service.py')\n"
        "raise SystemExit(0 if path.is_file() and 'VALUE = 1' in path.read_text() else 1)\n"
    )
    return {
        "files": [{"path": "flow.py", "content": source}],
        "manifest": {"version": 1, "tests": [{
            "id": "SMOKE-1", "criteria": ["AC-1"],
            "command": ["{python}", f"acceptance/changes/{change_id}/flow.py"],
            "timeout_seconds": 10,
        }]},
    }


class ProjectLoopTests(unittest.TestCase):
    def _approved(self, add_core: bool = False, add_lesson: bool = False):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name).resolve()
        repository(root)
        if add_core:
            (root / "core.py").write_text("STABLE = True\n", encoding="utf-8")
            git(root, "add", "core.py")
            git(root, "commit", "-q", "-m", "core")
        if add_lesson:
            from tests.lesson_support import sample, write_lesson
            write_lesson(root, sample(baseline_commit=git(root, "rev-parse", "HEAD")))
            git(root, "add", ".")
            git(root, "commit", "-q", "-m", "lesson")
        bootstrap_project(root, {"test_commands": []})
        state = create_change(root, "orders", product(), now=lambda: NOW)
        approve_change(root, state["change_id"], now=lambda: NOW)
        return temporary, root, state["change_id"]

    def setUp(self):
        blocker = patch('storyloop.agent_transport._codex', side_effect=AssertionError('Real CLI forbidden'))
        self.real_adapter = blocker.start()
        self.addCleanup(blocker.stop)

    def tearDown(self):
        self.real_adapter.assert_not_called()  # Retrospective intentionally catches failures.

    def test_external_loop_proves_red_retries_then_merges_locally(self):
        temporary, root, change_id = self._approved(add_lesson=True)
        attempts = []
        memory = {'lessons_used': [{'id': 'L-0001', 'how': '按经验处理'}], 'errata': []}

        def implement(project, context):
            attempts.append(context["attempt"])
            if len(attempts) == 2:
                target = project / "modules/orders/service.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "attempted", "questions": [], **memory}

        services = LoopServices(
            generate_smoke=lambda project, state: {**smoke_payload(change_id), **memory},
            implement=implement,
            review=lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [], "questions": [], **memory},
        )
        try:
            source = git(root, "branch", "--show-current")
            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(attempts, [1, 2])
            self.assertEqual(git(root, "branch", "--show-current"), source)
            parents = git(
                root, "rev-list", "--parents", "-n", "1", result["merge_commit"]
            ).split()
            self.assertEqual(len(parents), 3)
            self.assertEqual(git(root, "rev-parse", "HEAD"), result["integration_commit"])
            self.assertTrue((root / "modules/orders/service.py").is_file())
            from storyloop.project_loop import load_run_state
            saved = load_run_state(root, change_id)
            self.assertEqual(saved["lessons"]["retrospective"], "DONE", saved["lessons"])
            self.assertEqual(saved["lessons_digest"],
                             lessons_digest(root, saved["contract_commit"]))
        finally:
            temporary.cleanup()

    def test_preparation_isolates_invalid_lesson_root_and_preserves_diagnostics(self):
        from storyloop.project_preparation import _prepare_acceptance
        from storyloop.project_loop import load_run_state
        from storyloop.lesson_retrieval import lessons_payload
        from storyloop.call_log import open_call, record_lessons_skipped
        from storyloop.report_evidence import collect_evidence
        from storyloop.report_memory import memory_items
        from storyloop.report_markdown import render_presentation
        from storyloop.changes import load_change
        temporary, root, change_id = self._approved()
        self.addCleanup(temporary.cleanup)
        (root / 'openspec').mkdir(exist_ok=True)
        (root / 'openspec/lessons').write_text('not a directory', encoding='utf-8')
        git(root, 'add', 'openspec/lessons')
        git(root, 'commit', '-qm', 'invalid lesson root')
        state = {'change_id': change_id, 'status': 'PREPARING',
                 'source_head': git(root, 'rev-parse', 'HEAD')}
        services = LoopServices(generate_smoke=lambda p, c: smoke_payload(change_id),
                                implement=None, review=None)
        self.assertIsNone(_prepare_acceptance(root, load_change(root, change_id), services, state))
        saved = load_run_state(root, change_id)
        self.assertEqual(saved['status'], 'IMPLEMENTING')
        self.assertTrue(saved['red_evidence_sha256'])
        self.assertIsNone(saved['lessons_digest'])
        self.assertIn('LESSON_INVALID', saved['lessons_digest_error'])
        provided, reason = lessons_payload(root, 'implement', {'change_id': change_id})
        self.assertIsNone(provided)
        self.assertIn('LESSON_INVALID', reason)
        call = open_call(root, 'implement', change_id, 'offline', {})
        record_lessons_skipped(root, call, reason)
        evidence = collect_evidence(root, {'change_id': change_id}, {})
        items = memory_items(root, {'change_id': change_id}, {}, evidence)
        rendered = render_presentation({'presentation': {'tier': 'heavy', 'sections': {
            '6': {'title': '记忆', 'items': items}}}})
        self.assertIn('LESSON_INVALID', rendered)
        self.assertNotIn('LESSONS_NOT_CONFIGURED', rendered)

    def test_snapshot_freezing_propagates_git_and_system_errors(self):
        from storyloop.project_preparation import _lesson_snapshot
        from storyloop.models import StoryLoopError
        errors = [StoryLoopError('git failed'), OSError('filesystem failed')]
        wrapped = StoryLoopError('invalid commit', code='LESSON_INVALID')
        wrapped.__cause__ = StoryLoopError('git resolution failed')
        errors.append(wrapped)
        for error in errors:
            with self.subTest(error=str(error)):
                with patch('storyloop.project_preparation.lessons_digest', side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        _lesson_snapshot(Path('.'), 'frozen')
                    self.assertIs(caught.exception, error)

    def test_a_manifest_echoed_among_the_generated_files_is_ignored(self):
        """Found in real acceptance: the acceptance agent listed manifest.json among its files; the
        repair path already ignored that echo, first generation refused it and blocked the run."""
        temporary, root, change_id = self._approved()

        def generate(project, state):
            payload = smoke_payload(change_id)
            payload["files"].append({"path": "manifest.json", "content": "{}"})
            return payload

        def implement(project, context):
            target = project / "modules/orders/service.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("VALUE = 1\n", encoding="utf-8")
            return {"summary": "done", "questions": []}

        services = LoopServices(
            generate_smoke=generate, implement=implement,
            review=lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []})
        try:
            self.assertEqual(run_change(root, change_id, services)["status"], "PASSED")
        finally:
            temporary.cleanup()

    def test_base_change_pauses_on_feature_branch_and_creates_proposal(self):
        temporary, root, change_id = self._approved(add_core=True)

        def implement(project, context):
            (project / "core.py").write_text("STABLE = False\n", encoding="utf-8")
            return {"summary": "needs base", "questions": []}

        services = LoopServices(
            generate_smoke=lambda project, state: smoke_payload(change_id),
            implement=implement,
            review=lambda project, context: {"verdict": "pass", "summary": "ok", "findings": [], "questions": []},
        )
        try:
            result = run_change(root, change_id, services)
            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertTrue(git(root, "branch", "--show-current").startswith("feature/"))
            self.assertTrue(Path(result["base_proposal"]).is_file())
        finally:
            temporary.cleanup()


class RetrospectiveFixtureTests(unittest.TestCase):
    def test_explicit_adapters_are_preserved(self):
        from storyloop.storage import load_json, write_json
        from tests.routing_support import local_retrospective_adapter
        from tests.lesson_support import initialized_project
        root = initialized_project()
        self.addCleanup(cleanup_repo, root)
        path = root / '.storyloop/config.json'
        original = load_json(path)
        for explicit in ({'adapter': 'manual'}, {'adapter': 'command', 'agent_command': ['test-fixture']},
                         {'adapters': {'chosen': {'kind': 'codex'}}}, {'roles': {'default_adapter': 'chosen'}}):
            with self.subTest(explicit=explicit):
                config = {**original, **explicit}
                write_json(path, config)
                local_retrospective_adapter(root)
                self.assertEqual(load_json(path), config)


if __name__ == "__main__":
    unittest.main()
