"""Engine owned execution profile and approval version snapshot."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import storyloop
from storyloop.baseline import load_project_config
from storyloop.project_agents import invoke_project_agent
from storyloop.storage import write_json
from storyloop.telemetry import ledger_entries, record_call
from storyloop.models import StoryLoopError
from tests.test_cross_vendor import configured, capabilities
from tests.test_baseline_behavior import repository
from storyloop.baseline import bootstrap_project


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
