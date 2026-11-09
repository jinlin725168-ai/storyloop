import json
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

from storyloop.testing.smoke_harness.v1 import HarnessError, precondition

PROBE = r'''
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

from storyloop.acceptance_repair import repair_smoke
from storyloop.approvals import approve_change
from storyloop.baseline import bootstrap_project, load_project_config
from storyloop.cli_output import display
from storyloop.project_loop import load_run_state, run_change
from storyloop.storage import write_json
from tests.routing_support import legacy_create_change
from tests.test_baseline_behavior import repository
from tests.test_project_loop import smoke_payload
from tests.test_smoke_repair import NOW, defective_payload, services, weak_payload
from tests.support import product

prefix, repair_kind = sys.argv[1:]
with tempfile.TemporaryDirectory(prefix=prefix) as folder:
    root = Path(folder).resolve()
    with contextlib.redirect_stdout(io.StringIO()):
        repository(root)
        bootstrap_project(root, {"test_commands": []})
        config = load_project_config(root)
        config["max_attempts"] = 1
        write_json(root / ".storyloop/config.json", config)
        change_id = legacy_create_change(root, "orders", product(), now=lambda: NOW)["change_id"]
        approve_change(root, change_id, now=lambda: NOW)
        initial = run_change(root, change_id, services(
            change_id, lambda project, change: defective_payload(change_id)))
        old_red = load_run_state(root, change_id).get("red_evidence_sha256")
        replacement = weak_payload if repair_kind == "weak" else smoke_payload
        result = repair_smoke(root, change_id, "replace defective acceptance oracle",
                              services(change_id, lambda project, change: replacement(change_id)),
                              now=lambda: NOW)
        saved = load_run_state(root, change_id)
    text_out = io.StringIO()
    json_out = io.StringIO()
    with contextlib.redirect_stdout(text_out):
        display(result, complete_json=False)
    with contextlib.redirect_stdout(json_out):
        display(result, complete_json=True)
    print(json.dumps({"root": str(root), "change_id": change_id,
                      "initial": initial["status"], "old_red": old_red,
                      "result": result, "saved": saved,
                      "text": text_out.getvalue(), "json": json_out.getvalue()}))
'''


def scenario(prefix, repair_kind="good"):
    try:
        child = subprocess.run(
            [sys.executable, "-c", PROBE, prefix, repair_kind],
            cwd=Path.cwd(), capture_output=True, text=True, timeout=180,
        )
        if child.returncode:
            raise HarnessError("Business subprocess failed: " + child.stderr[-1500:])
        payload = json.loads(child.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise HarnessError("Business subprocess/protocol unavailable") from error
    required = {"root", "change_id", "initial", "old_red", "result", "saved", "text", "json"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise HarnessError("Business protocol is invalid")
    precondition(payload["initial"] == "BUDGET_EXHAUSTED",
                 "Fixture must stop at BUDGET_EXHAUSTED before repair")
    precondition(isinstance(payload["result"], dict) and isinstance(payload["saved"], dict),
                 "Repair result and saved state must be available")
    precondition(Path(payload["root"]).is_absolute() and payload["change_id"],
                 "Fixture needs an absolute project path and change ID")
    return payload


def command_parts(case, value):
    case.assertIsInstance(value, str)
    try:
        return shlex.split(value)
    except ValueError as error:
        case.fail("next is not a valid shell command: " + str(error))


class RepairNextAcceptance(unittest.TestCase):
    def test_next_uses_real_path(self):
        # S1-C1: must fail on the contract commit.
        for prefix in ("storyloop-acceptance-", "storyloop acceptance $& "):
            with self.subTest(prefix=prefix):
                data = scenario(prefix)
                precondition(data["result"].get("status") == "ACCEPTANCE_REPAIRED",
                             "Fixture must complete a successful repair")
                self.assertIn("next", data["result"])
                self.assertEqual(command_parts(self, data["result"]["next"]),
                                 ["storyloop", "run", data["root"], "--change", data["change_id"]])

    def test_text_and_json_next_match(self):
        # S1-C2: must fail on the contract commit.
        data = scenario("storyloop acceptance $& ")
        precondition(data["result"].get("status") == "ACCEPTANCE_REPAIRED",
                     "Fixture must complete a successful repair")
        lines = [line for line in data["text"].splitlines() if line.startswith("next: ")]
        self.assertEqual(len(lines), 1)
        try:
            rendered_json = json.loads(data["json"])
        except ValueError as error:
            self.fail("CLI JSON output is invalid: " + str(error))
        self.assertIn("next", rendered_json)
        text_next = lines[0][len("next: "):]
        json_next = rendered_json["next"]
        expected = ["storyloop", "run", data["root"], "--change", data["change_id"]]
        self.assertEqual(text_next, json_next)
        self.assertEqual(command_parts(self, text_next), expected)
        self.assertEqual(command_parts(self, json_next), expected)

    def test_success_fields_and_saved_state(self):
        # S1-C3, S1-C4: must already pass before the fix.
        data = scenario("storyloop-acceptance-")
        result, saved = data["result"], data["saved"]
        precondition(result.get("status") == "ACCEPTANCE_REPAIRED",
                     "Fixture must complete a successful repair")
        precondition(isinstance(saved.get("repairs"), list) and saved["repairs"],
                     "Fixture must save a repair record")
        precondition(isinstance(data["old_red"], str) and data["old_red"],
                     "Fixture must have an earlier RED fingerprint")
        self.assertEqual(result["status"], "ACCEPTANCE_REPAIRED")
        self.assertEqual(result["change_id"], data["change_id"])
        self.assertEqual(result["commit"], saved["repairs"][-1]["commit"])
        self.assertEqual(saved["status"], "IMPLEMENTING")
        self.assertEqual(saved["attempt"], 0)
        self.assertIsInstance(saved.get("red_evidence_sha256"), str)
        self.assertNotEqual(saved["red_evidence_sha256"], data["old_red"])

    def test_unproven_repair_keeps_stop(self):
        # S1-C5: must already pass before the fix.
        data = scenario("storyloop-acceptance-", "weak")
        self.assertEqual(data["result"]["status"], "EXISTING_GREEN")
        self.assertEqual(data["saved"]["status"], "EXISTING_GREEN")
        self.assertNotEqual(data["result"]["status"], "ACCEPTANCE_REPAIRED")


if __name__ == "__main__":
    unittest.main()
