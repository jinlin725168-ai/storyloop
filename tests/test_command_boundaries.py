"""Error paths of commands the coverage manifest listed as boundary gaps (roadmap D1)."""
from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.cli import dispatch, parser
from storyloop.models import StoryLoopError
from tests.routing_support import legacy_create_change as create_change
from tests.support import product
from tests.test_baseline_behavior import repository


class CommandBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        repository(self.root)
        bootstrap_project(self.root)
        self.draft = create_change(self.root, "idea", product())["change_id"]

    def _refused(self, args):
        with self.assertRaises(StoryLoopError) as refused:
            dispatch(args)
        return str(refused.exception)

    def test_ci_refuses_an_unknown_change(self):
        self.assertIn("不存在", self._refused(parser().parse_args(["ci", str(self.root), "--change", "FEAT-20260101-999"])))

    def test_knowledge_propose_refuses_a_change_that_has_not_passed(self):
        self.assertIn("PASSED", self._refused(parser().parse_args(["knowledge-propose", str(self.root), "--change", self.draft])))

    def test_authorize_knowledge_refuses_an_unknown_proposal(self):
        message = self._refused(parser().parse_args(["authorize-knowledge", str(self.root), "--proposal", "K-20260101-nope",
                                 "--paths", "openspec/foundation/x.md"]))
        self.assertNotIn("Invalid", message)  # refused because it does not exist, not on format

    def test_base_review_refuses_a_change_that_is_not_a_base_candidate(self):
        self.assertIn("verified green candidate", self._refused(parser().parse_args(["base-review", str(self.root), "--change", self.draft, "--verdict", "pass", "--note", "looks fine"])))

    def test_bench_report_refuses_an_unknown_run(self):
        self.assertIn("unavailable", self._refused(parser().parse_args(["bench", "report", "0" * 32, "--output-root", str(self.root)])))


if __name__ == "__main__":
    unittest.main()
