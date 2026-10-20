from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.project_agents import invoke_project_agent
from storyloop.models import StoryLoopError
from storyloop.skills import instruction_skill_snapshot
from storyloop.storage import load_json, write_json
from storyloop.telemetry import record_call, summarize
from tests.test_baseline_behavior import repository


class ReportCallProvenanceTests(unittest.TestCase):
    def test_snapshot_freezes_exact_text_version_path_and_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()  # the engine reads resolved paths (8.3 temp names on Windows)
            skill = root / ".codex/skills/oracle-craft/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: oracle-craft\nversion: 1.2\n---\nfirst\n", encoding="utf-8")

            text, snapshot = instruction_skill_snapshot(root, "oracle-craft")

            self.assertEqual(snapshot, {
                "name": "oracle-craft",
                "path": ".codex/skills/oracle-craft/SKILL.md",
                "version": "1.2",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            })
            skill.write_text("---\nname: oracle-craft\nversion: 1.3\n---\nsecond\n", encoding="utf-8")
            _, changed = instruction_skill_snapshot(root, "oracle-craft")
            self.assertNotEqual(snapshot["sha256"], changed["sha256"])

    def test_snapshot_version_is_parsed_from_the_same_frozen_read_as_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()  # the engine reads resolved paths (8.3 temp names on Windows)
            skill = root / ".codex/skills/oracle-craft/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("placeholder", encoding="utf-8")
            first = "---\nname: oracle-craft\nversion: 1.2\n---\nfrozen\n"
            second = "---\nname: oracle-craft\nversion: 9.9\n---\nchanged\n"
            original = Path.read_text
            reads = 0

            def changing_read(path, *args, **kwargs):
                nonlocal reads
                if path == skill:
                    reads += 1
                    return first if reads == 1 else second
                return original(path, *args, **kwargs)

            with patch.object(Path, "read_text", changing_read):
                text, snapshot = instruction_skill_snapshot(root, "oracle-craft")

            self.assertEqual(reads, 1)
            self.assertEqual(text, first)
            self.assertEqual(snapshot["version"], "1.2")
            self.assertEqual(snapshot["sha256"], hashlib.sha256(first.encode("utf-8")).hexdigest())

    def test_each_call_records_engine_snapshot_and_adapter_cannot_spoof_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            skill = root / ".codex/skills/oracle-craft/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: oracle-craft\nversion: 1\n---\nlocal\n", encoding="utf-8")
            script = root / ".storyloop/agent.py"
            script.write_text(
                "import json, sys\n"
                "request = json.load(sys.stdin)\n"
                "print(json.dumps({'verdict': 'pass', 'summary': 'ok', 'findings': [], "
                "'questions': [], '_telemetry': "
                "{'skills': [{'name': 'spoof'}], 'vendor': 'acme', 'tier_applied': 'strong'}}))\n",
                encoding="utf-8",
            )
            config = load_project_config(root)
            config.update({"adapter": "command", "agent_command": [sys.executable, str(script)]})
            write_json(root / ".storyloop/config.json", config)

            invoke_project_agent(root, "review", {"change": {"change_id": "FEAT-1"}})
            first = load_json(root / ".storyloop/state/telemetry/FEAT-1.json")["entries"][0]
            skill = root / first["skills"][0]["path"]
            skill.write_text(skill.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
            invoke_project_agent(root, "review", {"change": {"change_id": "FEAT-1"}})
            entries = load_json(root / ".storyloop/state/telemetry/FEAT-1.json")["entries"]

            self.assertEqual([entry["skills"][0]["name"] for entry in entries],
                             ["oracle-craft", "oracle-craft"])
            self.assertNotEqual(entries[0]["skills"][0]["sha256"], entries[1]["skills"][0]["sha256"])
            self.assertEqual(entries[0]["vendor"], "acme")
            self.assertEqual(entries[0]["tier_applied"], "strong")

    def test_partial_numeric_coverage_keeps_known_subtotal_but_total_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record_call(root, "review", "C1", {"cost_usd": 0.2, "turns": 2}, 10)
            record_call(root, "review", "C1", {}, 20, failed=True)

            result = summarize(root, "C1")

            self.assertIsNone(result["cost_usd"])
            self.assertEqual(result["known_cost_usd"], 0.2)
            self.assertEqual(result["coverage"]["cost_usd"], {"known": 1, "total": 2})
            self.assertIsNone(result["turns"])
            self.assertEqual(result["by_role"]["review"]["failed"], 1)

    def test_no_known_cost_has_unknown_subtotal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            empty = summarize(root, "C1")
            record_call(root, "review", "C2", {}, 10)
            missing = summarize(root, "C2")

            self.assertIsNone(empty["known_cost_usd"])
            self.assertIsNone(missing["known_cost_usd"])
            self.assertIsNone(missing["by_role"]["review"]["known_cost_usd"])

    def test_missing_strings_and_tokens_stay_unknown_and_values_are_bounded_sorted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record_call(root, "review", "C1", {"model": "z", "vendor": "v", "tier_applied": "strong",
                                                 "input_tokens": 4, "output_tokens": 2}, 10)
            record_call(root, "review", "C1", {"model": "a"}, 20)
            result = summarize(root, "C1")
            self.assertIsNone(result["input_tokens"])
            self.assertEqual(result["coverage"]["input_tokens"], {"known": 1, "total": 2})
            self.assertEqual(result["models"], ["a", "z"])
            self.assertEqual(result["vendors"], ["v"])
            self.assertEqual(result["tiers"], ["strong"])
            self.assertEqual(result["coverage"]["models"], {"known": 2, "total": 2})
            self.assertEqual(result["coverage"]["vendors"], {"known": 1, "total": 2})
            self.assertEqual(result["coverage"]["tiers"], {"known": 1, "total": 2})

    def test_missing_categorical_values_are_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record_call(root, "review", "C1", {}, 10)
            result = summarize(root, "C1")

            self.assertIsNone(result["models"])
            self.assertIsNone(result["vendors"])
            self.assertIsNone(result["tiers"])
            self.assertEqual(result["coverage"]["models"], {"known": 0, "total": 1})
            self.assertEqual(result["coverage"]["vendors"], {"known": 0, "total": 1})
            self.assertEqual(result["coverage"]["tiers"], {"known": 0, "total": 1})

    def test_legacy_missing_skills_differs_from_explicit_empty_skills(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record_call(root, "review", "OLD", {}, 10)
            record_call(root, "implement", "NEW", {"skills": []}, 10)

            legacy = summarize(root, "OLD")
            explicit = summarize(root, "NEW")

            self.assertIsNone(legacy["skills"])
            self.assertEqual(legacy["coverage"]["skills"], {"known": 0, "total": 1})
            self.assertEqual(explicit["skills"], [])
            self.assertEqual(explicit["coverage"]["skills"], {"known": 1, "total": 1})

    def test_non_finite_boolean_and_negative_numbers_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record_call(root, "review", "C1", {"cost_usd": float("nan"), "turns": True,
                                                 "input_tokens": float("inf"), "output_tokens": -1}, 10)
            entry = load_json(root / ".storyloop/state/telemetry/C1.json")["entries"][0]
            self.assertNotIn("cost_usd", entry)
            self.assertNotIn("turns", entry)
            self.assertNotIn("input_tokens", entry)
            self.assertNotIn("output_tokens", entry)

    def test_failed_schema_call_retains_available_adapter_telemetry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            script = root / ".storyloop/agent.py"
            script.write_text(
                "import json\n"
                "print(json.dumps({'summary': 'invalid', '_telemetry': "
                "{'cost_usd': 0.4, 'input_tokens': 9, 'model': 'reported'}}))\n",
                encoding="utf-8",
            )
            config = load_project_config(root)
            config.update({"adapter": "command", "agent_command": [sys.executable, str(script)]})
            write_json(root / ".storyloop/config.json", config)

            with self.assertRaises(StoryLoopError):
                invoke_project_agent(root, "review", {"change": {"change_id": "FEAT-2"}})

            result = summarize(root, "FEAT-2")
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["cost_usd"], 0.4)
            self.assertEqual(result["input_tokens"], 9)
            self.assertEqual(result["models"], ["reported"])


if __name__ == "__main__":
    unittest.main()
