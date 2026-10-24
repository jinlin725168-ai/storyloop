"""Change IDs with optional feature slugs."""
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from storyloop.baseline import bootstrap_project
from storyloop.change_types import type_for_change_id
from storyloop.changes import create_change, contract_digest, revise_change
from storyloop.models import StoryLoopError
from storyloop import product_cli
from tests.support import product
from tests.test_baseline_behavior import repository
from tests.test_change_nature import fix_product, refactor_product


MOMENT = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)


class TraceIdTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        repository(self.root)
        bootstrap_project(self.root)

    def test_slug_id_has_type_date_and_slug(self):
        state = create_change(self.root, "导出用户", product(), now=lambda: MOMENT,
                              slug="user-export")
        self.assertEqual(state["change_id"], "FEAT-20261024-user-export")

    def test_slug_is_not_kept_in_the_contract(self):
        state = create_change(self.root, "导出用户", {**product(), "slug": "user-export"},
                              now=lambda: MOMENT)
        self.assertEqual(state["change_id"], "FEAT-20261024-user-export")
        self.assertNotIn("slug", state["product"])

    def test_revision_cannot_put_slug_into_the_contract_fingerprint(self):
        state = create_change(self.root, "导出用户", product(), now=lambda: MOMENT,
                              slug="user-export")
        revised = revise_change(self.root, state["change_id"],
                                {**product(), "slug": "another-slug"})
        self.assertEqual(revised["change_id"], state["change_id"])
        self.assertNotIn("slug", revised["product"])
        self.assertEqual(contract_digest(revised["product"]),
                         contract_digest({**state["product"], "clarifications": []}))

    def test_without_slug_the_legacy_sequence_is_used(self):
        state = create_change(self.root, "x", product(), now=lambda: MOMENT)
        self.assertEqual(state["change_id"], "FEAT-20261024-001")

    def test_invalid_proposed_slug_is_ignored(self):
        for bad in ("User-Export", "a", "x" * 41, "has space", "-lead", "trail-"):
            with self.subTest(slug=bad):
                self.assertIsNone(product_cli.choose_slug(Namespace(slug=None), bad, False))

    def test_taken_slug_is_refused_with_a_hint(self):
        create_change(self.root, "x", product(), now=lambda: MOMENT, slug="user-export")
        with self.assertRaisesRegex(StoryLoopError, "--slug"):
            create_change(self.root, "y", product(), now=lambda: MOMENT, slug="user-export")

    def test_existing_openspec_change_directory_also_counts_as_taken(self):
        (self.root / "openspec/changes/FEAT-20261024-user-export").mkdir(parents=True)
        with self.assertRaises(StoryLoopError):
            create_change(self.root, "y", product(), now=lambda: MOMENT, slug="user-export")

    def test_flag_beats_interactive_answer_beats_proposal(self):
        self.assertEqual(product_cli.choose_slug(Namespace(slug="from-flag"), "proposed", True,
                                     ask=lambda _: "typed"), "from-flag")
        self.assertEqual(product_cli.choose_slug(Namespace(slug=None), "proposed", True,
                                     ask=lambda _: "typed-in"), "typed-in")
        self.assertEqual(product_cli.choose_slug(Namespace(slug=None), "proposed", True,
                                     ask=lambda _: ""), "proposed")
        self.assertEqual(product_cli.choose_slug(Namespace(slug=None), "proposed", False), "proposed")

    def test_interactive_prompt_uses_the_changes_type_prefix(self):
        prompts = []
        def ask(prompt):
            prompts.append(prompt)
            return ""
        self.assertEqual(product_cli.choose_slug(Namespace(slug=None), "null-amount", True,
                                                 ask=ask, prefix="FIX"), "null-amount")
        self.assertIn("FIX-", prompts[0])

    def test_legacy_ids_still_resolve_their_type(self):
        self.assertEqual(type_for_change_id("FIX-20261001-003"), "fix")
        self.assertEqual(type_for_change_id("FIX-20261024-null-amount"), "fix")

    def test_fix_and_refactor_use_their_prefix(self):
        fix = create_change(self.root, "修复金额", fix_product(), now=lambda: MOMENT,
                            slug="null-amount")
        refactor = create_change(self.root, "重构订单", refactor_product(), now=lambda: MOMENT,
                                 slug="orders-core")
        self.assertEqual(fix["change_id"], "FIX-20261024-null-amount")
        self.assertEqual(refactor["change_id"], "REF-20261024-orders-core")
