"""Process containment and multi-story regression integration tests."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.support import product, config, check, write
from storyloop.engine import approve, run
from storyloop.storage import initialize
from storyloop.processutil import run_process


class SystemTests(unittest.TestCase):
    def test_timeout_stops_descendant_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            marker = path / "late.txt"
            child = "import time; from pathlib import Path; time.sleep(2); Path(" + repr(str(marker)) + ").write_text('late')"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(10)"
            result = run_process([sys.executable, "-c", parent], path, 0.5)
            self.assertTrue(result["timed_out"])
            time.sleep(2.1)
            self.assertFalse(marker.exists(), "A timed-out descendant must not write after return")

    def test_second_story_regression_is_repaired_and_prior_evidence_refreshed(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "case"
            initialize(project)
            spec = product()
            second = json.loads(json.dumps(spec["stories"][0]))
            second.update(id="ST-2", title="second feature", depends_on=["ST-1"])
            second["acceptance"][0]["id"] = "AC-2"
            spec["stories"].append(second)
            write(project / "product.json", spec)
            write(project / "config.json", config())
            checks = []
            for index in (1, 2):
                health = check("health")
                health.update(id=f"H-{index}", story_id=f"ST-{index}")
                target = check()
                target.update(id=f"C-{index}", story_id=f"ST-{index}", criteria=[f"AC-{index}"])
                target["command"][-1] = ("import json,sys; from pathlib import Path; good=Path('" + str(index) + ".txt').exists(); "
                    "print(json.dumps({'protocol':'storyloop.check.v1','results':[{'id':'overlap','status':'pass' if good else 'fail','message':'feature exists'}]}));sys.exit(0 if good else 1)")
                checks.extend([health, target])
            write(project / "checks.json", {"version": 1, "checks": checks})
            calls = []
            def agent(root, role, payload):
                key = payload["story"]["id"]
                calls.append((role, key))
                if role == "implement":
                    index = key[-1]
                    (root / "workspace" / f"{index}.txt").write_text("feature")
                    if key == "ST-2" and payload["attempt"] == 1:
                        (root / "workspace" / "1.txt").unlink()
                    elif key == "ST-2":
                        (root / "workspace" / "1.txt").write_text("feature")
                    return {"summary": "implementation", "questions": []}
                return {"verdict": "pass", "summary": "independent review", "findings": [], "questions": []}
            approve(project)
            with patch("storyloop.agents.invoke", agent):
                state = run(project)
            self.assertEqual(state["status"], "PASSED")
            self.assertEqual(state["stories"]["ST-2"]["attempts"], 2)
            self.assertEqual(calls.count(("review", "ST-1")), 2)


if __name__ == "__main__":
    unittest.main()
