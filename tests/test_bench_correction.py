"""Bench correction uses bounded simulated decisions and keeps stop evidence."""

import json
from pathlib import Path
import tempfile
import unittest

from storyloop.bench_run import SCORE_CONTRACT, _attempt
from storyloop.bench_worker import _run_until_stop
from storyloop.bench_real_exec import _preserve_stop_evidence, run_isolated_attempt
from storyloop.bench_case import load_case
from storyloop.design_approval import REPLAN_STOPS
from storyloop.models import StoryLoopError
from tests.test_bench_calibrate import calibration_fixture


class BenchCorrectionTests(unittest.TestCase):
    def test_run_state_stop_overrides_blocked_exception(self):
        statuses = iter(["ORACLE_DEFECT", "PASSED"])
        current = {"status": None}
        repairs = []

        def run(*_):
            current["status"] = next(statuses)
            if current["status"] == "ORACLE_DEFECT":
                raise StoryLoopError("RED failed", code="BLOCKED")
            return {"status": "PASSED"}

        outcome = _run_until_stop(
            Path("/tmp/fixture"), "FEAT-20261109-x", run, lambda *_: None,
            lambda *_: {"status": current["status"], "stop": {"blocker": "RED failed"},
                        "intervention_events": []},
            repair_smoke=lambda *_: repairs.append("acceptance-repair"),
        )
        self.assertEqual(outcome["status"], "PASSED")
        self.assertEqual(outcome["stops"][0]["status"], "ORACLE_DEFECT")
        self.assertEqual(repairs, ["acceptance-repair"])

    def test_one_oracle_repair_can_pass_after_routine_design_approval(self):
        statuses = iter(["ORACLE_DEFECT", "DESIGN_APPROVAL_REQUIRED", "PASSED"])
        current = {"status": None}
        calls = []

        def run(*_):
            current["status"] = next(statuses)
            return {"status": current["status"]}

        def state(*_):
            return {"status": current["status"], "stop": {"blocker": "token=private"},
                    "planning": {"plan": {"id": "plan-1"}}, "intervention_events": []}

        outcome = _run_until_stop(Path("/tmp/fixture"), "FEAT-20261109-x", run,
                                  lambda *_: calls.append("approve"), state,
                                  repair_smoke=lambda *_: calls.append("repair"),
                                  replan=lambda *_: calls.append("replan"),
                                  reset_run=lambda *_: calls.append("reset"))
        attempt = _attempt(outcome, "base", 1, {}, lambda *_: True)
        self.assertEqual(outcome["status"], "PASSED")
        self.assertEqual(calls, ["repair", "approve"])
        self.assertEqual(attempt["nonroutine_count"], 1)
        self.assertEqual(attempt["verdict"], "PASSED")
        self.assertEqual([stop["status"] for stop in outcome["stops"]],
                         ["ORACLE_DEFECT", "DESIGN_APPROVAL_REQUIRED"])
        self.assertNotIn("private", json.dumps(outcome))
        self.assertEqual(SCORE_CONTRACT["nonroutine_budget"], 1)
        self.assertEqual(SCORE_CONTRACT["version"], 3)

    def test_second_oracle_stop_needs_human_without_second_repair(self):
        statuses = iter(["ORACLE_DEFECT", "ORACLE_DEFECT"])
        calls = []
        outcome = _run_until_stop(Path("/tmp/fixture"), "FEAT-20261109-x",
                                  lambda *_: {"status": next(statuses)}, lambda *_: None,
                                  lambda *_: {"stop": {"blocker": "still broken"},
                                              "intervention_events": []},
                                  repair_smoke=lambda *_: calls.append("repair"),
                                  replan=lambda *_: None, reset_run=lambda *_: None)
        self.assertEqual(outcome["status"], "NEEDS_HUMAN")
        self.assertEqual(calls, ["repair"])
        self.assertEqual(len(outcome["stops"]), 2)

    def test_needs_input_never_simulates_an_answer(self):
        calls = []
        outcome = _run_until_stop(Path("/tmp/fixture"), "FEAT-20261109-x",
                                  lambda *_: {"status": "NEEDS_INPUT"}, lambda *_: None,
                                  lambda *_: {"stop": {"blocker": "question"},
                                              "intervention_events": []},
                                  repair_smoke=lambda *_: calls.append("repair"),
                                  replan=lambda *_: calls.append("replan"),
                                  reset_run=lambda *_: calls.append("reset"))
        self.assertEqual(outcome["status"], "NEEDS_HUMAN")
        self.assertEqual(calls, [])
        self.assertEqual(outcome["stops"][0]["status"], "NEEDS_INPUT")

    def test_recovery_routes_and_uses_fixed_reason(self):
        from storyloop.bench_worker import STOP_REASON
        for stop, expected in (("EXISTING_GREEN", "repair"),
                               ("DESIGN_REVISION_REQUIRED", "replan"),
                               ("NO_PROGRESS", "replan"),
                               ("WORKSPACE_TAINTED", "reset")):
            with self.subTest(stop=stop):
                calls = []
                statuses = iter([stop, "PASSED"])
                outcome = _run_until_stop(Path("/tmp/fixture"), "FEAT-20261109-x",
                                          lambda *_: {"status": next(statuses)}, lambda *_: None,
                                          lambda *_: {"stop": {}, "intervention_events": []},
                                          repair_smoke=lambda *args: calls.append(("repair", args[-1])),
                                          replan=lambda *args: calls.append(("replan", args[-1])),
                                          reset_run=lambda *args: calls.append(("reset", args[-1])))
                self.assertEqual(outcome["status"], "PASSED")
                self.assertEqual(calls, [(expected, STOP_REASON)])
        self.assertIn("NO_PROGRESS", REPLAN_STOPS)

    def test_persisted_recovery_event_is_marked_once(self):
        events = []
        statuses = iter(["ORACLE_DEFECT", "PASSED"])

        def repair(*_):
            events.append({"kind": "acceptance_repair", "stop": "ORACLE_DEFECT"})

        outcome = _run_until_stop(Path("/tmp/fixture"), "FEAT-20261109-x",
                                  lambda *_: {"status": next(statuses)}, lambda *_: None,
                                  lambda *_: {"stop": {}, "intervention_events": events},
                                  repair_smoke=repair, replan=lambda *_: None, reset_run=lambda *_: None)
        self.assertEqual(len(outcome["events"]), 1)
        self.assertEqual(outcome["events"][0]["simulated"], True)

    def test_stop_detail_survives_attempt_record(self):
        outcome = {"status": "NEEDS_HUMAN", "events": [{"kind": "replan"}],
                   "stops": [{"status": "DESIGN_REVISION_REQUIRED", "detail": {"reason": "repair"}}],
                   "stop_evidence_path": ".storyloop/bench/evidence/abc/"}
        attempt = _attempt(outcome, "base", 1, {}, lambda *_: True)
        self.assertEqual(attempt["stops"], outcome["stops"])
        self.assertEqual(attempt["stop_evidence_path"], outcome["stop_evidence_path"])

    def test_stop_snapshot_survives_cleanup_and_redacts_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "candidate"
            change_id = "FEAT-20261109-x"
            state_path = project / ".storyloop/state/runs" / f"{change_id}.json"
            red_path = project / ".storyloop/evidence" / change_id / "acceptance-red.json"
            for path in (state_path, red_path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"message": "token=private", "results": list(range(25))}))
            status = iter(["ORACLE_DEFECT", "PASSED"])
            outcome = _run_until_stop(project, change_id,
                                      lambda *_: {"status": next(status)}, lambda *_: None,
                                      lambda *_: {"stop": {"blocker": "token=private"},
                                                  "intervention_events": []},
                                      repair_smoke=lambda *_: red_path.unlink(),
                                      replan=lambda *_: None, reset_run=lambda *_: None,
                                      stop_capture_root=root / "stop-evidence")
            self.assertEqual(outcome["status"], "PASSED")
            _preserve_stop_evidence(root, outcome, root / "output")
            kept = root / "output" / outcome["stop_evidence_path"]
            self.assertTrue((kept / "stop-1/run-state.json").is_file())
            self.assertTrue((kept / "stop-1/acceptance-red.json").is_file())
            self.assertNotIn("private", (kept / "stop-1/acceptance-red.json").read_text())
            self.assertEqual(len(json.loads((kept / "stop-1/acceptance-red.json").read_text())["results"]), 25)

    def test_isolated_attempt_keeps_stop_snapshot_after_temp_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, repository = calibration_fixture(root)
            case = load_case(source)

            def engine(tree, *_):
                change_id = "FEAT-20261109-x"
                path = tree / ".storyloop/state/runs" / f"{change_id}.json"
                path.parent.mkdir(parents=True)
                path.write_text('{"reason":"token=private"}')
                statuses = iter(["ORACLE_DEFECT", "ORACLE_DEFECT"])
                return _run_until_stop(tree, change_id, lambda *_: {"status": next(statuses)},
                                       lambda *_: None, lambda *_: {"stop": {"blocker": "broken"},
                                                                     "intervention_events": []},
                                       repair_smoke=lambda *_: None, replan=lambda *_: None,
                                       reset_run=lambda *_: None,
                                       stop_capture_root=tree.parent / "stop-evidence")

            outcome = run_isolated_attempt(case, repository, "a" * 40, "test", "fixture",
                                           {"max_calls": 10}, lambda *_: "APPROVE",
                                           engine=engine, evidence_root=root)
            self.assertEqual(outcome["status"], "NEEDS_HUMAN")
            self.assertTrue((root / outcome["stop_evidence_path"] / "stop-1/run-state.json").is_file())
            self.assertNotIn("private", json.dumps(outcome))


if __name__ == "__main__":
    unittest.main()
