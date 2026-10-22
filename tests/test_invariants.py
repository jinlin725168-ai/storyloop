"""Invariant registry validation and fast guards for previously incomplete references."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from storyloop.manual_acceptance import pending_manual_criteria
from storyloop.models import StoryLoopError
from storyloop.oracle_counts import check_layers
from storyloop.routing_state import advance_tier
from storyloop.smoke_runner import _judge_red, run_commands
from tests.architecture_rules import (
    DECLARATION, PACKAGE, code_modules, forbidden_git_calls, load_declaration, resolve_test,
)
from tests.suites import CORE

DECL = load_declaration(DECLARATION)


class RegistryRuleTests(unittest.TestCase):
    def test_resolve_test_needs_exactly_one_testcase_method(self):
        source = ("import unittest\nclass A(unittest.TestCase):\n    def test_y(self): pass\n"
                  "class B:\n    def test_z(self): pass\n")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            path = root / "tests/test_x.py"
            path.write_text(source, encoding="utf-8")
            self.assertTrue(resolve_test("tests.test_x::test_y", root))
            for test in ["test_z", "test_missing"]:
                self.assertFalse(resolve_test(f"tests.test_x::{test}", root))
            self.assertFalse(resolve_test("tests.test_gone::test_y", root))
            path.write_text(source + "class C(unittest.TestCase):\n    def test_y(self): pass\n",
                            encoding="utf-8")
            self.assertFalse(resolve_test("tests.test_x::test_y", root))

    def test_resolve_test_recognizes_aliases_and_indirect_testcases_without_importing(self):
        source = ("from unittest import TestCase as Base\nimport unittest as unit\n"
                  "raise RuntimeError('AST only, never execute this file')\n"
                  "class Parent(Base): pass\nclass Child(Parent):\n    def test_child(self): pass\n"
                  "class Direct(unit.TestCase):\n    def test_direct(self): pass\n")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            (root / "tests/test_x.py").write_text(source, encoding="utf-8")
            self.assertTrue(resolve_test("tests.test_x::test_child", root))
            self.assertTrue(resolve_test("tests.test_x::test_direct", root))
            self.assertFalse(resolve_test("../escape::test_child", root))

    def test_forbidden_git_calls_go_red_on_injected_source(self):
        source = ('def f(root):\n    _git(root, "push", "origin")\n'
                  '    _git(root, "reset", "--hard")\n    label = "push notification"\n')
        self.assertEqual(forbidden_git_calls(source), ["push", "--hard"])

    def test_git_guard_ignores_labels_commit_messages_and_temporary_worktree_cleanup(self):
        source = ('label = "push"\nflag = "--force"\nunused = ["git", "rebase"]\n'
                  '_git(root, "commit", "-m", "push")\n'
                  '_git(root, "worktree", "remove", "--force", temporary)\n')
        self.assertEqual(forbidden_git_calls(source), [])

    def test_git_guard_finds_aliases_attributes_and_native_argv_with_global_options(self):
        source = ('from .baseline import _git as invoke\n'
                  'invoke(root, "update-ref", "HEAD", parent)\n'
                  'api._git(root, "rebase", "main")\n'
                  'subprocess.run(["git", "-C", root, "commit", "--amend"])\n'
                  'run_process(["git", "reset", "--hard"], root)\n')
        self.assertEqual(forbidden_git_calls(source), ["update-ref", "rebase", "--amend", "--hard"])

    def test_git_guard_checks_keyword_command_argv(self):
        source = ('run_process(command=["git", "push", "origin"], cwd=root, timeout=1)\n'
                  'run_process(command=["git", "reset", "--hard"], cwd=root, timeout=1)\n')
        self.assertEqual(forbidden_git_calls(source), ["push", "--hard"])

    def test_invalid_invariant_registrations_are_rejected(self):
        base = 'version=1\n[invariants.I1]\ntitle="规则"\n'
        cases = [(base + 'status="unknown"\n', "status"),
                 (base + 'status="guarded"\ntests=[]\n', "tests"),
                 (base + 'status="guarded"\ntests=[""]\n', "tests"),
                 (base + 'status="pending"\n', "stage"),
                 (base + 'status="pending"\nstage="D"\n', "stage")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "architecture.toml"
            for text, key in cases:
                with self.subTest(key=key, text=text):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, key):
                        load_declaration(path)


class InvariantGuardTests(unittest.TestCase):
    def test_i1_pass_claims_cannot_override_observed_exit_status(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_commands(Path(folder), [[sys.executable, "-c",
                'print(\'{"status":"PASSED","verdict":"pass"}\'); raise SystemExit(1)']], 5)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["results"][0]["returncode"], 1)
        self.assertIn('"status":"PASSED"', result["results"][0]["stdout"])

    def test_i4_environment_exit_and_timeout_cannot_prove_red(self):
        with tempfile.TemporaryDirectory() as folder:
            for script in ["raise SystemExit(2)", "import time; time.sleep(10)"]:
                with self.subTest(script=script):
                    result = run_commands(Path(folder), [[sys.executable, "-c", script]], 1)
                    row = {**result["results"][0], "criteria": ["AC-1"]}
                    self.assertEqual(row["status"], "ERROR")
                    with self.assertRaisesRegex(StoryLoopError, "environment error"):
                        _judge_red([row], {"tests": [{"id": row["id"]}]}, {})

    def test_i4_zero_execution_and_skipping_cannot_prove_red(self):
        change = {"product": {"stories": [{"acceptance": [{"id": "AC-1"}]}]}}
        manifest = {"tests": [{"id": "probe"}]}
        for status, message in [("FAIL", "environment error"), ("PASS", "already green")]:
            with self.subTest(status=status):
                row = {"id": "probe", "status": status, "criteria": ["AC-1"],
                       "counts": {"executed": 0, "skipped": 1}}
                with self.assertRaisesRegex(StoryLoopError, message):
                    _judge_red([row], manifest, change)

    def test_i6_a_byte_change_invalidates_manual_acceptance(self):
        before = hashlib.sha256(b"VALUE=1\n").hexdigest()
        after = hashlib.sha256(b"VALUE=2\n").hexdigest()
        change = {"product": {"stories": [{"acceptance": [{"id": "AC-M", "kind": "manual"}]}]}}
        records = [{"criterion_id": "AC-M", "candidate_sha256": before}]
        self.assertEqual(pending_manual_criteria(change, records, before), [])
        self.assertEqual(pending_manual_criteria(change, records, after), ["AC-M"])

    def test_i7_a_green_layer_that_executed_nothing_fails(self):
        layers = {"unit": {"status": "PASSED", "results": [{"id": "u", "counts": {"executed": 0}}]}}
        self.assertEqual(check_layers(layers, None)["unit"]["status"], "FAILED")

    def test_i7_fewer_tests_than_the_baseline_floor_fails(self):
        layers = {"unit": {"status": "PASSED", "results": [{"id": "u", "counts": {"executed": 3}}]}}
        checked = check_layers(layers, {"baseline": {"unit": [5]}})
        self.assertEqual(checked["unit"]["status"], "FAILED")
        self.assertIn("从 5 降到 3", checked["unit"]["oracle_problems"][0])

    def test_i9_the_engine_has_no_push_or_history_rewrite(self):
        hits = {name: found for name, path in code_modules(PACKAGE).items()
                if (found := forbidden_git_calls(path.read_text(encoding="utf-8")))}
        self.assertEqual(hits, {})

    def test_i11_a_lighter_decision_never_lowers_the_effective_tier(self):
        for initial, effective in [("light", "heavy"), ("heavy", "standard")]:
            with self.subTest(initial=initial, effective=effective):
                state = {"attempt": 1, "routing": {"initial_tier": initial, "effective_tier": effective,
                         "history": [], "budget_source": "tier", "limit": 4, "reasons": []}}
                advance_tier(state, {"tier": "light", "reasons": []}, "L3", "digest", {"max_attempts": 4})
                self.assertEqual(state["routing"]["effective_tier"], "heavy")
                self.assertGreaterEqual(state["routing"]["limit"], 4)


class InvariantRegistryTests(unittest.TestCase):
    def test_all_fifteen_invariants_are_registered(self):
        self.assertEqual(sorted(DECL.invariants, key=lambda k: int(k[1:])), [f"I{n}" for n in range(1, 16)])

    def test_pending_invariants_are_exactly_i12_i13_i15(self):
        pending = {k: v.stage for k, v in DECL.invariants.items() if v.status == "pending"}
        self.assertEqual(pending, {"I12": "B", "I13": "E", "I15": "T6"})

    def test_every_guard_exists_and_runs_in_core(self):
        for key, item in DECL.invariants.items():
            for test_id in item.tests:
                with self.subTest(invariant=key, test=test_id):
                    self.assertTrue(resolve_test(test_id), "找不到这个测试")
                    self.assertIn(test_id.split("::")[0], CORE, "守护测试必须在 core 套件里")
