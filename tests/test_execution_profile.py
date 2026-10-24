"""Engine owned execution profile and approval version snapshot."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import storyloop
from storyloop.baseline import load_baseline, load_project_config
from storyloop.approvals import approve_change
from storyloop.changes import contract_digest, load_change
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import write_json
from storyloop.telemetry import ledger_entries, record_call
from storyloop.models import StoryLoopError
from storyloop import telemetry_summary, report_markdown
from storyloop.report_evidence import collect_evidence
from tests.test_cross_vendor import configured, capabilities
from tests.test_baseline_behavior import repository
from storyloop.baseline import bootstrap_project
from tests.routing_support import legacy_create_change
from tests.support import product


class ExecutionProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def test_every_entry_carries_the_engine_version(self):
        entry = record_call(self.root, "product", "FEAT-20261024-x", {}, 10)
        self.assertEqual(entry["engine_version"], storyloop.__version__)

    def test_adapter_claims_cannot_set_profile_fields(self):
        entry = record_call(self.root, "product", "FEAT-20261024-x",
                            {"engine_version": "9.9.9", "instructions_sha256": "f" * 64,
                             "schema_sha256": "f" * 64}, 10)
        self.assertEqual(entry["engine_version"], storyloop.__version__)
        self.assertNotIn("instructions_sha256", entry)
        self.assertNotIn("schema_sha256", entry)

    def test_engine_fields_carry_instruction_and_schema_digests(self):
        entry = record_call(self.root, "product", "FEAT-20261024-x", {}, 10,
                            engine_fields={"instructions_sha256": "a" * 64,
                                           "schema_sha256": "b" * 64})
        self.assertEqual((entry["instructions_sha256"], entry["schema_sha256"]),
                         ("a" * 64, "b" * 64))

    def test_malformed_digests_are_dropped(self):
        entry = record_call(self.root, "product", "FEAT-20261024-x", {}, 10,
                            engine_fields={"instructions_sha256": "not-hex",
                                           "schema_sha256": "B" * 64})
        self.assertNotIn("instructions_sha256", entry)
        self.assertNotIn("schema_sha256", entry)

    def test_a_real_role_call_records_digests_of_what_was_sent(self):
        repository(self.root)
        bootstrap_project(self.root)
        seen = self.root / "seen.json"
        script = self.root / "agent.py"
        script.write_text("import json,sys\n"
                          "request=json.load(sys.stdin)\n"
                          "json.dump(request,open(sys.argv[1],'w'))\n"
                          "print(json.dumps({'summary':'ok','questions':[]}))\n", encoding="utf-8")
        config = load_project_config(self.root)
        config.update(adapter="command", agent_command=[sys.executable, str(script), str(seen)])
        write_json(self.root / ".storyloop/config.json", config)
        invoke_project_agent(self.root, "implement", {"change": {"change_id": "FEAT-1"}})
        sent = json.loads(seen.read_text(encoding="utf-8"))
        entry = ledger_entries(self.root, "FEAT-1")[-1]
        digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
        self.assertEqual(entry["instructions_sha256"], digest(sent["instructions"]))
        self.assertEqual(entry["schema_sha256"], digest(json.dumps(
            sent["response_schema"], sort_keys=True, ensure_ascii=False)))

    def test_failed_availability_entries_carry_the_engine_version(self):
        configured(self.root, "fail_closed")
        with patch("storyloop.adapter_capabilities.probe",
                   side_effect=[capabilities("a"), capabilities("b")]), \
             patch("storyloop.agent_transport._command",
                   side_effect=StoryLoopError("offline", code="AGENT_UNAVAILABLE")):
            with self.assertRaises(StoryLoopError):
                invoke_project_agent(self.root, "review", {"change_id": "FEAT-1"})
        entries = ledger_entries(self.root, "FEAT-1")
        self.assertTrue(entries)
        self.assertTrue(all(entry.get("engine_version") == storyloop.__version__ for entry in entries))


class VersionSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        repository(self.root)
        bootstrap_project(self.root)
        self.change_id = legacy_create_change(self.root, "idea", product())["change_id"]

    def test_approval_records_the_version_snapshot(self):
        versions = approve_change(self.root, self.change_id)["approval"]["versions"]
        self.assertEqual(versions["engine_version"], storyloop.__version__)
        self.assertEqual(versions["baseline_version"], load_baseline(self.root)["baseline_version"])
        self.assertIsNone(versions["map_version"])
        self.assertIn("oracle-craft", {skill["name"] for skill in versions["skills"]})

    def test_snapshot_does_not_change_the_contract_digest(self):
        before = contract_digest(load_change(self.root, self.change_id)["product"])
        state = approve_change(self.root, self.change_id)
        self.assertEqual(state["approval"]["contract_digest"], before)

    def test_reapproving_keeps_the_first_snapshot(self):
        first = approve_change(self.root, self.change_id)["approval"]["versions"]
        with patch("storyloop.approvals.__version__", "9.9.9"):
            again = approve_change(self.root, self.change_id)["approval"]["versions"]
        self.assertEqual(again, first)

    def test_a_project_skill_copy_is_what_the_snapshot_records(self):
        local = self.root / ".claude/skills/oracle-craft"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text(
            "---\nname: oracle-craft\nversion: 9.9.9\ndescription: x\n---\nroles: [product]\n",
            encoding="utf-8")
        versions = approve_change(self.root, self.change_id)["approval"]["versions"]
        self.assertIn(("oracle-craft", "9.9.9"),
                      {(skill["name"], skill["version"]) for skill in versions["skills"]})

    def test_approving_a_legacy_change_records_versions(self):
        self.assertRegex(self.change_id, r"^FEAT-\d{8}-\d{3}$")
        self.assertIn("versions", approve_change(self.root, self.change_id)["approval"])


SNAP = {"version": 1, "engine_version": "0.1.0", "baseline_version": "1.0.0",
        "map_version": None, "skills": [{"name": "oracle-craft", "path": "storyloop/skills/oracle-craft/SKILL.md",
                                         "version": "1.1.1", "sha256": "a" * 64}]}
SKILL = SNAP["skills"][0]


class VersionDriftTests(unittest.TestCase):
    def test_same_versions_are_unchanged(self):
        drift = telemetry_summary.version_drift(SNAP, [{"engine_version": "0.1.0", "skills": [SKILL]}])
        self.assertEqual(drift["status"], "unchanged")

    def test_a_different_engine_version_is_changed(self):
        drift = telemetry_summary.version_drift(
            SNAP, [{"engine_version": "0.1.0"}, {"engine_version": "0.2.0"}])
        self.assertEqual((drift["status"], drift["engine_versions"]),
                         ("changed", ["0.1.0", "0.2.0"]))

    def test_a_skill_digest_outside_the_snapshot_is_changed(self):
        other = {**SKILL, "version": "1.2.0", "sha256": "b" * 64}
        drift = telemetry_summary.version_drift(SNAP, [{"engine_version": "0.1.0", "skills": [other]}])
        self.assertEqual((drift["status"], drift["skills_changed"]),
                         ("changed", ["oracle-craft@1.2.0"]))

    def test_entries_without_profile_are_unknown_not_drift(self):
        self.assertEqual(telemetry_summary.version_drift(SNAP, [{"role": "product"}])["status"],
                         "unknown")
        drift = telemetry_summary.version_drift(
            SNAP, [{"role": "product"}, {"engine_version": "0.1.0"}])
        self.assertEqual((drift["status"], drift["unprofiled_calls"]), ("unchanged", 1))

    def test_no_snapshot_is_unknown(self):
        self.assertEqual(telemetry_summary.version_drift(
            None, [{"engine_version": "0.1.0"}])["status"], "unknown")

    def test_report_shows_the_drift_line(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            state = legacy_create_change(root, "idea", product())
            state = approve_change(root, state["change_id"])
            change_id = state["change_id"]
            write_json(root / f".storyloop/state/telemetry/{change_id}.json",
                       {"version": 1, "change_id": change_id,
                        "entries": [{"role": "product", "wall_ms": 1, "engine_version": "0.2.0"}]})
            evidence = collect_evidence(root, state, {"layers": {}, "traceability": []})
            lines = report_markdown._cost(evidence["telemetry"])
            self.assertIn("- 版本漂移：运行中版本有变化（引擎 0.2.0）", lines)

    def test_missing_ledger_preserves_its_unavailable_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            bootstrap_project(root)
            state = legacy_create_change(root, "idea", product())
            state = approve_change(root, state["change_id"])
            evidence = collect_evidence(root, state, {"layers": {}, "traceability": []})
            self.assertIsNone(evidence["telemetry"])
            self.assertEqual(evidence["availability"]["telemetry"], "NOT_AVAILABLE")
