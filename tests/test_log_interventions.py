"""Public intervention timeline and L1 parity."""

import io
import json
import unittest
from unittest.mock import patch

from storyloop import cli
from storyloop.storage import load_json, write_json
from tests.test_structured_output import approved_change


class LogInterventionTests(unittest.TestCase):
    def setUp(self):
        self.temporary, self.root, self.change_id = approved_change()
        self.addCleanup(self.temporary.cleanup)
        self.change_path = self.root / ".storyloop/state/changes" / f"{self.change_id}.json"
        self.run_path = self.root / ".storyloop/state/runs" / f"{self.change_id}.json"

    def command(self, *args):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(list(args)), 0)
        return output.getvalue()

    def event(self, kind, at, source, stop, **extra):
        return {"change_id": self.change_id, "kind": kind, "at": at,
                "source": source, "stop": stop, **extra}

    def test_timeline_and_text_match_l1(self):
        change = load_json(self.change_path)
        change["intervention_events"] = [self.event(
            "contract_approval", "2026-01-02T23:00:00Z", "approve", "DRAFT")]
        write_json(self.change_path, change)
        write_json(self.run_path, {"version": 1, "change_id": self.change_id, "status": "PASSED",
                                   "intervention_events": [self.event(
                                       "acceptance_repair", "2026-01-04T00:00:00Z", "engine",
                                       "ORACLE", automatic=True), self.event(
                                       "replan", "2026-01-05T00:00:00Z", "engine",
                                       "DESIGN", automatic=True)],
                                   "repairs": [{"automatic": False}]})
        for folder, event in (("answers", self.event("answer", "2026-01-03T00:00:00Z",
                                                     "answer", "INPUT", planned=False)),
                              ("acceptance", self.event("overrule", "2026-01-03T00:00:00+02:00",
                                                        "overrule", "REVIEW"))):
            write_json(self.root / ".storyloop/state" / folder / f"{self.change_id}.json",
                       {"version": 1, "change_id": self.change_id, "intervention_events": [event]})
        before = {path: path.read_bytes() for path in (self.change_path, self.run_path)}
        log = json.loads(self.command("log", str(self.root), "--change", self.change_id, "--json"))
        metrics = json.loads(self.command("metrics", str(self.root), "--json"))
        text = self.command("log", str(self.root), "--change", self.change_id)
        events = log["interventions"]["events"]
        self.assertEqual([(e["kind"], e["class"], e["inferred"]) for e in events], [
            ("overrule", "nonroutine", False), ("contract_approval", "routine", False),
            ("answer", "nonroutine", False), ("acceptance_repair", "automatic_repair", False),
            ("replan", "not_counted", False), ("acceptance_repair", "nonroutine", True)])
        self.assertEqual(events[-1], {"at": None, "kind": "acceptance_repair", "source": None,
                                      "stop": None, "class": "nonroutine", "inferred": True})
        row = metrics["metrics"]["L1"]["by_change"][self.change_id]
        self.assertEqual(log["interventions"]["totals"],
                         {key: row[key] for key in ("routine", "nonroutine", "automatic_repair", "unknown")})
        self.assertIn("人工介入\n", text)
        self.assertIn("推断  acceptance_repair  非例行（无，停点 无）", text)
        self.assertIn("合计：非例行 3，例行 1，自动修复 1", text)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_missing_run_preserves_unknown(self):
        log = json.loads(self.command("log", str(self.root), "--change", self.change_id, "--json"))
        metrics = json.loads(self.command("metrics", str(self.root), "--json"))
        row = metrics["metrics"]["L1"]["by_change"][self.change_id]
        self.assertIsNone(log["interventions"]["totals"]["nonroutine"])
        self.assertEqual(log["interventions"]["totals"]["unknown"], row["unknown"])

    def test_complete_empty_state_has_two_line_section(self):
        change = load_json(self.change_path)
        change["intervention_events"] = []
        change["approval"] = None
        write_json(self.change_path, change)
        write_json(self.run_path, {"version": 1, "change_id": self.change_id,
                                   "status": "PASSED", "intervention_events": []})
        log = json.loads(self.command("log", str(self.root), "--change", self.change_id, "--json"))
        text = self.command("log", str(self.root), "--change", self.change_id)
        self.assertEqual(log["interventions"]["events"], [])
        self.assertIn("人工介入\n合计：非例行 0，例行 0，自动修复 0", text)


if __name__ == "__main__":
    unittest.main()
