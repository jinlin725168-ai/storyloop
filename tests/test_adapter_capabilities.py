import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from storyloop.adapter_capabilities import check, ensure_checked, probe
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.cli import dispatch, parser
from storyloop.models import StoryLoopError
from storyloop.storage import write_json
from tests.test_baseline_behavior import repository


class AdapterCapabilityTests(unittest.TestCase):
    def _project(self, root: Path, response: str):
        repository(root)
        bootstrap_project(root)
        script = root / "adapter.py"
        counter = root / "probes.txt"
        script.write_text(
            "import pathlib, sys\nrequest = sys.stdin.read()\n"
            "path=pathlib.Path(sys.argv[1]); path.write_text(path.read_text() + 'x' if path.exists() else 'x')\n"
            "print(" + repr(response) + ")\n", encoding="utf-8")
        config = load_project_config(root)
        config.update({"adapters": {"test": {"kind": "command", "agent_command": [sys.executable, str(script), str(counter)]}},
                       "roles": {"default_adapter": "test"}})
        write_json(root / ".storyloop/config.json", config)

    def test_probe_malformed_reply_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, "not json")
            result = probe(root, "test", load_project_config(root)["adapters"]["test"])
            self.assertEqual(result["known"], False)
            self.assertIn("invalid", result["reason"])

    def test_probe_partial_json_reply_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, json.dumps({"vendor": "test", "tiers": ["strong", "standard"]}))
            result = probe(root, "test", load_project_config(root)["adapters"]["test"])
            self.assertEqual(result["known"], False)
            self.assertIn("invalid", result["reason"])

    def test_handshake_uses_engine_python_even_when_path_python_differs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, _capabilities(["strong", "standard"]))
            config = load_project_config(root)
            config["adapters"]["test"]["agent_command"][0] = "{python}"
            write_json(root / ".storyloop/config.json", config)
            with mock.patch("storyloop.capability_protocol.shutil.which", return_value="/wrong/python"):
                result = check(root)
            self.assertTrue(result["adapters"]["test"]["known"])
            self.assertEqual((root / "probes.txt").read_text(encoding="utf-8"), "x")

    def test_failed_handshake_explains_exit_without_process_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, _capabilities(["strong", "standard"]))
            script = root / "adapter.py"
            script.write_text("import missing_storyloop_trial_module\n", encoding="utf-8")
            result = dispatch(parser().parse_args(["adapters", "check", str(root)]))
            reason = result["adapters"]["test"].get("reason", "")
            self.assertIn("exit 1", reason)
            self.assertIn("adapter command", reason)
            self.assertNotIn("missing_storyloop_trial_module", reason)
            self.assertLessEqual(len(reason), 600)

    def test_failed_handshake_never_exposes_stderr_or_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, _capabilities(["strong", "standard"]))
            (root / "adapter.py").write_text(
                "import sys; print('Authorization: Bearer secret-token-xyz', file=sys.stderr); "
                "print('secret-token-xyz'); sys.exit(9)\n",
                encoding="utf-8")
            result = dispatch(parser().parse_args(["adapters", "check", str(root)]))
            reason = result["adapters"]["test"]["reason"]
            self.assertIn("exit 9", reason)
            self.assertIn("adapter command", reason)
            self.assertNotIn("secret-token-xyz", json.dumps(result))
            from storyloop.cross_vendor import require_distinct
            with self.assertRaises(StoryLoopError) as caught:
                require_distinct({"cross_vendor_review": {"enabled": True},
                                  "roles": {"default_adapter": "test", "review": {"adapter": "test"}}},
                                 {"test": result["adapters"]["test"]})
            self.assertNotIn("secret-token-xyz", str(caught.exception))

    def test_check_rejects_known_missing_requested_tier(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, _capabilities(["standard"]))
            with self.assertRaisesRegex(StoryLoopError, "ADAPTER_PROFILE_UNSUPPORTED") as caught:
                check(root)
            self.assertEqual(caught.exception.code, "ADAPTER_PROFILE_UNSUPPORTED")

    def test_check_writes_cache_and_invalidates_when_config_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, _capabilities(["strong", "standard"]))
            check(root)
            cache = root / ".storyloop/state/adapters.json"
            first = json.loads(cache.read_text(encoding="utf-8"))
            config = load_project_config(root)
            config["roles"]["implement"] = {"tier": {"light": "standard", "standard": "strong", "heavy": "strong"}}
            write_json(root / ".storyloop/config.json", config)
            check(root)
            second = json.loads(cache.read_text(encoding="utf-8"))
            self.assertNotEqual(first["config_sha256"], second["config_sha256"])

    def test_unchanged_cache_avoids_probe_and_changed_config_or_argv_reprobes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, _capabilities(["strong", "standard"]))
            ensure_checked(root)
            counter = root / "probes.txt"
            self.assertEqual(counter.read_text(encoding="utf-8"), "x")
            ensure_checked(root)
            self.assertEqual(counter.read_text(encoding="utf-8"), "x")
            config = load_project_config(root)
            config["roles"]["review"] = {"adapter": "test"}
            write_json(root / ".storyloop/config.json", config)
            ensure_checked(root)
            self.assertEqual(counter.read_text(encoding="utf-8"), "xx")
            config = load_project_config(root)
            config["adapters"]["test"]["agent_command"].append("changed")
            write_json(root / ".storyloop/config.json", config)
            ensure_checked(root)
            self.assertEqual(counter.read_text(encoding="utf-8"), "xxx")

    def test_partial_known_cache_is_invalidated_and_reprobed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._project(root, _capabilities(["strong", "standard"]))
            ensure_checked(root)
            cache_path = root / ".storyloop/state/adapters.json"
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            cache["adapters"]["test"] = {"known": True, "tiers": ["strong", "standard"]}
            write_json(cache_path, cache)

            ensure_checked(root)

            self.assertEqual((root / "probes.txt").read_text(encoding="utf-8"), "xx")


if __name__ == "__main__":
    unittest.main()


def _capabilities(tiers):
    return json.dumps({"vendor": "test", "tiers": tiers, "subagents": True,
                       "path_guard": True, "fast_checks": True, "modes": ["*"]})
