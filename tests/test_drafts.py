from pathlib import Path
import tempfile
import unittest

from storyloop.drafts import create_draft, list_drafts, load_draft, reject_draft
from storyloop.models import StoryLoopError


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)

    def test_create_and_load(self):
        ref_id = create_draft(
            self.project, "lesson", "FEAT-20261019-001", "call-1",
            {"action": "create"}, "正文",
        )
        self.assertEqual(ref_id, "D-FEAT-20261019-001-01")
        draft = load_draft(self.project, "lesson/" + ref_id)
        self.assertEqual((draft["status"], draft["body"]), ("pending", "正文"))
        self.assertEqual(draft["failure"], None)

    def test_sequence_per_source_is_shared_and_does_not_reuse_gaps(self):
        first = create_draft(self.project, "lesson", "FEAT-1", None, {}, None)
        self.assertEqual(
            create_draft(self.project, "errata", "FEAT-1", None, {}, None),
            "D-FEAT-1-02",
        )
        draft = self.project / ".storyloop/drafts/lesson" / first
        for child in draft.iterdir():
            child.unlink()
        draft.rmdir()
        self.assertEqual(
            create_draft(self.project, "lesson", "FEAT-1", None, {}, None),
            "D-FEAT-1-03",
        )

    def test_manual_source_uses_utc_timestamp(self):
        draft_id = create_draft(self.project, "lesson", "manual", None, {}, None)
        self.assertRegex(draft_id, r"^D-manual-[0-9]{14}-01$")

    def test_ref_must_be_safe(self):
        for bad in ("lesson/../x", "smoke/D-1", "lesson/D-1/../../etc"):
            with self.subTest(ref=bad), self.assertRaises(StoryLoopError):
                load_draft(self.project, bad)

    def test_list_includes_existing_kinds_read_only(self):
        change = self.project / ".storyloop/drafts/FEAT-20261019-002"
        smoke = self.project / ".storyloop/drafts/smoke/FEAT-20261019-003"
        change.mkdir(parents=True)
        smoke.mkdir(parents=True)
        create_draft(self.project, "lesson", "FEAT-1", None, {}, None)

        kinds = sorted(draft["kind"] for draft in list_drafts(self.project))

        self.assertEqual(kinds, ["change", "lesson", "smoke"])
        self.assertEqual(list(change.iterdir()), [])
        self.assertEqual(list(smoke.iterdir()), [])

    def test_reject_keeps_draft_and_body(self):
        ref_id = create_draft(self.project, "lesson", "FEAT-1", None, {}, "人工正文")
        rejected = reject_draft(self.project, "lesson/" + ref_id, "不具备推广价值")

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["failure"], "不具备推广价值")
        self.assertEqual(load_draft(self.project, "lesson/" + ref_id)["body"], "人工正文")
        self.assertEqual(list_drafts(self.project), [])

    def test_drafts_cli_lists_and_rejects(self):
        from storyloop.cli import dispatch, parser

        baseline = self.project / ".storyloop/baseline.json"
        baseline.parent.mkdir(parents=True)
        baseline.write_text("{}\n", encoding="utf-8")
        draft_id = create_draft(self.project, "lesson", "FEAT-1", None, {}, None)
        listed = dispatch(parser().parse_args(["drafts", str(self.project)]))
        self.assertEqual(listed["drafts"][0]["id"], draft_id)
        rejected = dispatch(parser().parse_args([
            "drafts", str(self.project), "--reject", "lesson/" + draft_id,
            "--reason", "重复",
        ]))
        self.assertEqual(rejected["status"], "rejected")

    def test_drafts_cli_requires_reason_only_for_rejection(self):
        from storyloop.cli import dispatch, parser

        baseline = self.project / ".storyloop/baseline.json"
        baseline.parent.mkdir(parents=True)
        baseline.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(StoryLoopError):
            dispatch(parser().parse_args(["drafts", str(self.project), "--reason", "无目标"]))
        with self.assertRaises(StoryLoopError):
            dispatch(parser().parse_args(["drafts", str(self.project), "--reject", "lesson/D-1"]))


if __name__ == "__main__":
    unittest.main()
