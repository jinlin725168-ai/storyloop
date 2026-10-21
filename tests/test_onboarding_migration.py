"""Legacy repositories adopt or revert the helper only through a reviewed BASE commit."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from storyloop.base_upgrade import propose_base_change, upgrade_baseline
from storyloop.baseline import bootstrap_project, load_baseline
from storyloop.models import StoryLoopError
from storyloop.onboarding import scaffold_project
from tests.onboarding_support import commit, repository
from tests.test_onboarding_lock import complete

ARTIFACTS = ["acceptance/AGENT_GUIDE.md", "acceptance/storyloop-settings.json",
             "acceptance/onboarding.json", "acceptance/tools/python_smoke.py"]


def prepare(base):
    root, draft = base / "legacy", base / "draft"
    root.mkdir()
    draft.mkdir()
    repository(root)
    repository(draft)
    settings = complete(draft)
    (root / "tests").mkdir()
    shutil.copyfile(draft / "tests/test_public.py", root / "tests/test_public.py")
    commit(root)
    bootstrap_project(root, settings)
    proposal = propose_base_change(root, ARTIFACTS, "Adopt the versioned helper from an external draft")
    for relative in ARTIFACTS:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(draft / relative, target)
    return root, proposal


class OnboardingMigrationTests(unittest.TestCase):
    def test_external_draft_is_adopted_only_via_base_and_stays_protected(self):
        with tempfile.TemporaryDirectory() as folder:
            root, proposal = prepare(Path(folder))
            with self.assertRaisesRegex(StoryLoopError, "baseline"):
                scaffold_project(root, ["python"])
            commit(root)
            result = upgrade_baseline(root, proposal["change_id"])
            self.assertEqual(result["status"], "BASELINE_UPGRADED")
            self.assertEqual(load_baseline(root)["baseline_version"], "1.0.1")
            self.assertTrue(set(ARTIFACTS) <= set(load_baseline(root)["foundation_paths"]))
            # Returning to vendored execution is another independent BASE change.
            rollback = propose_base_change(root, ["acceptance/onboarding.json"], "Return to legacy executor")
            (root / "acceptance/onboarding.json").unlink()
            commit(root)
            self.assertEqual(upgrade_baseline(root, rollback["change_id"])["status"], "BASELINE_UPGRADED")

    def test_base_upgrade_cannot_adopt_an_incomplete_new_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root, proposal = prepare(Path(folder))
            path = root / "acceptance/onboarding.json"
            value = json.loads(path.read_text())
            value["executors"]["acceptance/tools/python_smoke.py"]["hooks"]["run_case"]["status"] = "pending"
            path.write_text(json.dumps(value))
            commit(root)
            with self.assertRaisesRegex(StoryLoopError, "pending"):
                upgrade_baseline(root, proposal["change_id"])
            self.assertEqual(load_baseline(root)["baseline_version"], "1.0.0")
