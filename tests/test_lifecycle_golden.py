"""Frozen public outputs reject status, verdict, budget and evidence drift."""
import json
from pathlib import Path
from pathlib import PureWindowsPath
import os
import tempfile
import unittest
from unittest.mock import patch

from tests.lifecycle_support import capture_case, normalize_case

FIXTURES = Path(__file__).parent / "fixtures" / "lifecycle"


class GoldenNormalizerTests(unittest.TestCase):
    def test_normalization_keeps_distinct_digests_and_shared_references(self):
        value = {"a": "a" * 64, "b": "b" * 64, "again": "a" * 64,
                 "path": "/case/report.json", "exitcode": 1, "executed": 9}
        result = normalize_case(value, Path("/case"))
        self.assertEqual(result["a"], result["again"])
        self.assertNotEqual(result["a"], result["b"])
        self.assertEqual(result["path"], "<PROJECT>/report.json")
        self.assertEqual((result["exitcode"], result["executed"]), (1, 9))

    def test_changed_verdict_counts_budget_wording_and_binding_stay_visible(self):
        original = {"status": "PASSED", "exitcode": 0, "executed": 5, "attempt": 2,
                    "next": "原文", "sha256": "a" * 64, "reference": "a" * 64}
        for key, value in (("status", "VERIFIED"), ("exitcode", 2), ("executed", 4),
                           ("attempt", 1), ("next", "changed"), ("reference", "b" * 64)):
            changed = {**original, key: value}
            with self.subTest(key=key):
                self.assertNotEqual(normalize_case(original, Path("/case")),
                                    normalize_case(changed, Path("/case")))

    def test_only_paths_clocks_and_measured_durations_are_normalized(self):
        value = {"at": "2026-10-22T10:20:30+00:00", "loop_ms": 87,
                 "attempt": 3, "failures": ["second", "first"], "counts": {"executed": None}}
        expected = {"at": "<TIME>", "loop_ms": "<DURATION>", "attempt": 3,
                    "failures": ["second", "first"], "counts": {"executed": None}}
        self.assertEqual(normalize_case(value, Path("/case")), expected)

    def test_cli_call_identifiers_and_runner_durations_are_metadata(self):
        value = {"log": "review OK 0.2s\n记录: calls/20261022T090251-review-8d097b",
                 "runner": "Ran 7 tests in 0.031s\nFAILED (failures=1)"}
        expected = {"log": "review OK <DURATION>s\n记录: calls/<CALL-1>",
                    "runner": "Ran 7 tests in <DURATION>s\nFAILED (failures=1)"}
        self.assertEqual(normalize_case(value, Path("/case")), expected)

    def test_daily_change_ids_keep_type_and_reference_identity(self):
        before = {"id": "FEAT-20261022-001", "link": "FEAT-20261022-001",
                  "base": "BASE-20261022-001"}
        later = {"id": "FEAT-20261023-001", "link": "FEAT-20261023-001",
                 "base": "BASE-20261023-001"}
        normalized = normalize_case(before, Path("/case"))
        self.assertEqual(normalized, normalize_case(later, Path("/case")))
        self.assertNotEqual(normalized["id"], normalized["base"])

    def test_recorded_windows_paths_quotes_and_json_transport_match_posix(self):
        posix = {"report": "/case/report.json", "next": "storyloop run /case",
                 "error": json.dumps({"report": "/case/report.json"}),
                 "literal": r"pattern=\w+\s; say 'yes'"}
        windows = {"report": r"C:\case\report.json", "next": r"storyloop run 'C:\case'",
                   "error": json.dumps({"report": r"C:\case\report.json"}),
                   "literal": posix["literal"]}
        self.assertEqual(normalize_case(posix, Path("/case")),
                         normalize_case(windows, PureWindowsPath(r"C:\case")))

    def test_candidate_paths_keep_suffixes_and_shared_identity_on_windows(self):
        posix = {"cwd": "/tmp/storyloop-smoke-abc", "argv": ["/tmp/storyloop-smoke-abc/test.py"],
                 "diagnostic": json.dumps({"cwd": "/tmp/storyloop-smoke-abc"})}
        windows = {"cwd": r"C:\Temp\storyloop-smoke-xyz",
                   "argv": [r"C:\Temp\storyloop-smoke-xyz\test.py"],
                   "diagnostic": json.dumps({"cwd": r"C:\Temp\storyloop-smoke-xyz"})}
        self.assertEqual(normalize_case(posix, Path("/case")),
                         normalize_case(windows, PureWindowsPath(r"C:\case")))

    def test_git_fixture_branch_is_independent_of_the_callers_default(self):
        from tests.lifecycle_support import fixture_environment
        from tests.test_baseline_behavior import git
        config = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "init.defaultBranch",
                  "GIT_CONFIG_VALUE_0": "main"}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, config):
            with fixture_environment():
                git(Path(folder), "init", "-q")
            self.assertEqual(git(Path(folder), "branch", "--show-current"), "master")

    def test_windows_paths_with_spaces_and_json_newlines_preserve_diagnostics(self):
        posix = {"file": "/case/some dir/report.json",
                 "error": json.dumps({"text": "/case/test.py\nFAILED"}),
                 "command": r"storyloop run /case --pattern \w+\s"}
        windows = {"file": r"C:\case\some dir\report.json",
                   "error": json.dumps({"text": "C:\\case\\test.py\r\nFAILED"}),
                   "command": r"storyloop run 'C:\case' --pattern \w+\s"}
        self.assertEqual(normalize_case(posix, Path("/case")),
                         normalize_case(windows, PureWindowsPath(r"C:\case")))

    def test_windows_evidence_paths_keep_slashes_after_change_id_tokenization(self):
        value = {'evidence_path': r'C:\case\.storyloop\evidence\FEAT-20261022-001\acceptance-result.json',
                 'diagnostic': json.dumps({'report': r'C:\case\reports\FEAT-20261022-001\report.json'}),
                 'literal': r'pattern=\w+\s'}
        actual = normalize_case(value, PureWindowsPath(r'C:\case'))
        self.assertEqual(actual['evidence_path'],
                         '<PROJECT>/.storyloop/evidence/<FEAT-1>/acceptance-result.json')
        self.assertEqual(json.loads(actual['diagnostic'])['report'],
                         '<PROJECT>/reports/<FEAT-1>/report.json')
        self.assertEqual(actual['literal'], r'pattern=\w+\s')

    def test_windows_path_does_not_normalize_following_diagnostic_backslashes(self):
        value = {"diagnostic": r"C:\case\FEAT-20261022-001\test.py: pattern=\w+\s"}
        result = normalize_case(value, PureWindowsPath(r"C:\case"))
        self.assertEqual(result["diagnostic"], r"<PROJECT>/<FEAT-1>/test.py: pattern=\w+\s")
        changed = {"diagnostic": value["diagnostic"].replace(r"\w+\s", "/w+/s")}
        self.assertNotEqual(result, normalize_case(changed, PureWindowsPath(r"C:\case")))

    def test_fixture_openspec_result_does_not_depend_on_host_installation(self):
        import shutil
        from storyloop.openspec_docs import validate_with_cli
        from tests.lifecycle_support import fixture_environment
        original = shutil.which
        def without_openspec(name, *args, **kwargs):
            return None if name == 'openspec' else original(name, *args, **kwargs)
        with patch('shutil.which', side_effect=without_openspec), fixture_environment():
            for kind, identifier in (('change', 'FEAT-fixture'), ('spec', 'orders')):
                with self.subTest(kind=kind):
                    self.assertEqual(validate_with_cli(Path('.'), kind, identifier),
                                     {'status': 'valid', 'kind': kind, 'id': identifier})


class LifecycleGoldenTests(unittest.TestCase):
    def test_reports_run_states_cli_and_git_match_the_pre_refactor_main(self):
        for name in ("success", "manual", "boundary", "repair", "supersede", "routed", "cost"):
            with self.subTest(case=name):
                expected = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(capture_case(name), expected)
