import json
from pathlib import Path
import sys
import tempfile
import unittest

from tests.lesson_support import cleanup_repo, git_blob
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.models import StoryLoopError
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import write_json
from storyloop.telemetry import ledger_entries
from tests.test_baseline_behavior import git, repository


class ProjectAgentTests(unittest.TestCase):
    def test_legacy_request_and_ledger_have_no_profile_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            config = load_project_config(root)
            config["adapter"] = "manual"
            write_json(root / ".storyloop/config.json", config)

            with self.assertRaisesRegex(StoryLoopError, "Manual input"):
                invoke_project_agent(root, "product", {"idea": "orders"})

            request = json.loads((root / ".storyloop/requests/project-product.json").read_text(encoding="utf-8"))
            self.assertEqual(set(request) - {"request_id", "instructions"},
                             {"protocol", "role", "project", "payload", "response_schema"})
            self.assertNotIn("role_profile", request)
            entry = ledger_entries(root, "pending")[0]
            self.assertEqual(set(entry) - {"at", "call", "wall_ms"}, {"role", "skills", "lessons_provided", "lessons_claimed", "lessons_unknown", "lessons_truncated"})
    def test_role_adapter_request_contains_profile_and_engine_ledger_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            script = root / "agent.py"
            script.write_text(
                "import json, sys\nrequest=json.load(sys.stdin)\n"
                "json.dump(request, open(sys.argv[1], 'w'))\n"
                "print(json.dumps({'verdict':'pass','summary':'ok','questions':[],'findings':[],'coverage':[], '_telemetry': {'tier_requested':'bad','adapter':'bad'}}))\n",
                encoding="utf-8")
            seen = root / "seen.json"
            config = load_project_config(root)
            config.update({"adapters": {"reviewer": {"kind": "command", "agent_command": [sys.executable, str(script), str(seen)]}},
                           "roles": {"default_adapter": "reviewer", "review": {"adapter": "reviewer"}}})
            write_json(root / ".storyloop/config.json", config)

            invoke_project_agent(root, "review", {"change": {"change_id": "FEAT-1"}})

            request = json.loads(seen.read_text(encoding="utf-8"))
            self.assertEqual(request["role_profile"]["tier"], "strong")
            entry = ledger_entries(root, "FEAT-1")[0]
            self.assertEqual(entry["tier_requested"], "strong")
            self.assertEqual(entry["adapter"], "reviewer")
    def test_implement_request_preserves_legacy_roots_under_plan_and_green_locks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "app").mkdir()
            (root / "app/cli.py").write_text("VALUE = 0\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests/test_cli.py").write_text("pass\n", encoding="utf-8")
            git(root, "add", "app", "tests")
            git(root, "commit", "-q", "-m", "app")
            bootstrap_project(root, {"module_roots": ["app"], "test_roots": ["tests"]})
            config = load_project_config(root)
            config["adapter"] = "manual"
            write_json(root / ".storyloop/config.json", config)

            with self.assertRaisesRegex(StoryLoopError, "Manual input"):
                invoke_project_agent(root, "implement", {
                    "change": {"change_id": "FEAT-1"},
                    "design_plan": {"forbidden_paths": ["tests"], "constraints": []},
                    "characterization": {
                        "status": "GREEN", "tests": ["tests/test_cli.py"], "locks": {},
                    },
                })
            request = json.loads(
                (root / ".storyloop/requests/project-implement.json").read_text(encoding="utf-8")
            )
            instructions = request["instructions"]
            self.assertIn("both existing and new files under the configured module roots and test roots",
                          instructions)
            self.assertIn(".storyloop/config.json", instructions)
            self.assertIn("integration path or foundation file", instructions)
            self.assertIn("existing non-test file outside its `touched` list", instructions)
            self.assertNotIn("Modify only new files", instructions)
            self.assertIn("structured approved plan is authoritative", instructions)
            self.assertIn("forbidden_paths", instructions)
            self.assertIn("GREEN characterization artifacts", instructions)
            self.assertIn("run the existing approved checks", instructions)
            self.assertIn("never invent a test or a failing result", instructions)

    def test_manual_agent_response_is_bound_to_exact_request_and_consumed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            config = load_project_config(root)
            config["adapter"] = "manual"
            write_json(root / ".storyloop/config.json", config)

            with self.assertRaisesRegex(StoryLoopError, "Manual input"):
                invoke_project_agent(root, "product", {"idea": "orders"})
            request_path = root / ".storyloop/requests/project-product.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response_path = root / ".storyloop/responses/project-product.json"
            write_json(response_path, {
                "request_id": request["request_id"],
                "result": {"version": 1, "goal": "g", "non_goals": [], "questions": [], "stories": []},
            })

            result = invoke_project_agent(root, "product", {"idea": "orders"})

            self.assertEqual(result["goal"], "g")
            self.assertFalse(response_path.exists())

    def test_unknown_role_fails_before_adapter_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            with self.assertRaisesRegex(StoryLoopError, "role"):
                invoke_project_agent(root, "owner", {})


if __name__ == "__main__":
    unittest.main()


class LessonInjectionTests(unittest.TestCase):
    def invoke(self, lessons=None, raw=None, extra=None, fail=False):
        from tests.lesson_support import git_repo
        root = git_repo(lessons=lessons, raw_lessons=raw)
        self.addCleanup(cleanup_repo, root)
        bootstrap_project(root)
        script = root / '.storyloop/replay.json'
        write_json(script, {'calls': [{'role': 'implement', 'result': {
            'summary': 'ok', 'questions': [], **(extra or {})},
            'telemetry': {'lessons_provided': ['spoof']}}]})
        config = load_project_config(root)
        adapter = Path(__file__).resolve().parents[1] / 'storyloop/testing/mock_adapter.py'
        config.update(adapter='command', agent_command=[sys.executable, str(adapter), '--script', str(script)])
        write_json(root / '.storyloop/config.json', config)
        if fail:
            with self.assertRaises(StoryLoopError):
                invoke_project_agent(root, 'implement', {})
            result = None
        else:
            result = invoke_project_agent(root, 'implement', {})
        entry = ledger_entries(root, 'pending')[-1]
        folder = root / '.storyloop/state/telemetry/calls' / entry['call']
        meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
        request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
        return root, result, entry, meta, request

    def test_claims_and_frozen_context_without_profile(self):
        import hashlib
        from tests.lesson_support import sample
        root, result, entry, meta, request = self.invoke([sample()], extra={'lessons_used': [
            {'id': 'L-0001', 'how': '按经验处理'}, {'id': 'L-0404', 'how': '未知'}]})
        self.assertEqual(entry['lessons_provided'], ['L-0001'])
        self.assertEqual(entry['lessons_claimed'], ['L-0001'])
        self.assertEqual(entry['lessons_unknown'], ['L-0404'])
        path = 'openspec/lessons/L-0001.md'
        self.assertEqual(meta['context']['lessons'], [{'path': path, 'sha256': hashlib.sha256(git_blob(root, path)).hexdigest()}])

    def test_absent_and_invalid(self):
        *_, request = self.invoke()
        self.assertNotIn('lessons', request['payload'])
        _, _, entry, meta, request = self.invoke(raw={'L-0001.md': 'garbage'})
        self.assertNotIn('lessons', request['payload'])
        self.assertIn('LESSON_INVALID', meta['lessons_skipped'])

    def test_complete_evidence_bounded_projection(self):
        from tests.lesson_support import sample
        entries = [sample(id=f'L-{i:04d}', tier='core', body='x') for i in range(1, 24)]
        claims = [{'id': item['id'], 'how': 'checked'} for item in entries]
        claims += [{'id': f'L-{i:04d}', 'how': 'unknown'} for i in range(101, 124)]
        _, _, entry, meta, request = self.invoke(entries, extra={'lessons_used': claims})
        self.assertEqual(len(request['payload']['lessons']['core']), 23)
        self.assertEqual(len(entry['lessons_provided']), 20)
        self.assertTrue(entry['lessons_truncated'])
        for key in ('lessons_provided', 'lessons_claimed', 'lessons_unknown'):
            self.assertEqual(len(meta[key]), 23)
            self.assertEqual(len(entry[key]), 20)

    def test_failed_call_has_provided_but_no_claims(self):
        from tests.lesson_support import sample
        _, _, entry, meta, _ = self.invoke([sample()], extra={'summary': 7}, fail=True)
        self.assertTrue(entry['failed'])
        self.assertEqual(entry['lessons_provided'], ['L-0001'])
        self.assertEqual(entry['lessons_claimed'], [])
        self.assertEqual(entry['lessons_unknown'], [])
        self.assertEqual(meta['status'], 'FAILED')
