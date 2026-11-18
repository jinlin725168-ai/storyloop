"""Requirements map and bootstrap (spec-craft batch 4): agent drafts, human installs."""
from pathlib import Path
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.requirements_map import apply_map, existing_requirements, module_ids, propose_map
from storyloop.requirements_registry import replay
from storyloop.requirement_checklist import evaluate_checklist
from tests.routing_support import readiness_repository
from tests.support import product
from tests.test_baseline_behavior import git

SPEC = """# orders Specification

## Requirements

### Requirement: Reserve a room

_Story ST-1, from FEAT-20261001-001_

As employee, reserve a free room, so that meetings do not collide.

The system SHALL reject an overlapping reservation
The system SHALL keep the reservation owner

#### Scenario: AC-1
- **GIVEN** a reservation exists
- **WHEN** another overlaps
- **THEN** reject it
"""


def survey(answer, calls):
    def invoke(project, role, payload):
        calls.append((role, payload))
        return answer
    return invoke


class RequirementsMapTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        readiness_repository(self.root)
        spec = self.root / "openspec/specs/orders/spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text(SPEC, encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "existing living spec")
        self.answer = {"modules": [{"id": "booking", "name": "Room booking", "responsibility": "Reservations",
                                    "boundaries": "No billing", "code_areas": ["app"]}],
                       "assignments": [{"spec": "orders", "title": "Reserve a room", "module": "booking"}],
                       "questions": []}

    def test_living_spec_requirements_and_rules_are_found(self):
        found = existing_requirements(self.root)
        self.assertEqual(found, [{"spec": "orders", "title": "Reserve a room", "rules": [
            "The system SHALL reject an overlapping reservation", "The system SHALL keep the reservation owner"]}])

    def test_propose_then_apply_installs_the_map_and_registers_existing_requirements(self):
        calls = []
        proposed = propose_map(self.root, survey(self.answer, calls))
        self.assertEqual((calls[0][0], calls[0][1]["mode"]), ("survey", "requirements-map"))
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")  # draft is control plane only
        applied = apply_map(self.root, proposed["digest"])
        self.assertEqual((applied["requirements"], applied["rules"]), (1, 2))
        self.assertEqual(module_ids(self.root), {"booking"})
        registry = replay(self.root)
        requirement = f"{applied['change_id']}.S1"
        self.assertEqual(registry["requirements"][requirement]["module"], "booking")
        self.assertEqual(registry["rules"][f"{applied['change_id']}.R2"]["requirement"], requirement)
        self.assertEqual(existing_requirements(self.root), [])  # now registered

    def test_unplaced_requirements_bad_areas_and_stale_digests_are_refused(self):
        unplaced = dict(self.answer, assignments=[])
        with self.assertRaisesRegex(StoryLoopError, "needs a module"):
            propose_map(self.root, survey(unplaced, []))
        missing = dict(self.answer, modules=[dict(self.answer["modules"][0], code_areas=["nope"])])
        with self.assertRaisesRegex(StoryLoopError, "code area does not exist"):
            propose_map(self.root, survey(missing, []))
        propose_map(self.root, survey(self.answer, []))
        with self.assertRaisesRegex(StoryLoopError, "digest does not match"):
            apply_map(self.root, "0" * 64)

    def test_with_a_map_a_contract_must_name_one_of_its_modules(self):
        apply_map(self.root, propose_map(self.root, survey(self.answer, []))["digest"])
        value = product()
        text = "The contract names an active module of the requirements map in capability"
        self.assertEqual({i["text"]: i["level"] for i in evaluate_checklist(value, self.root)}[text], "error")
        value["capability"] = "booking"
        self.assertEqual({i["text"]: i["level"] for i in evaluate_checklist(value, self.root)}[text], "ok")


class MapCommandTests(unittest.TestCase):
    def test_map_apply_without_a_proposal_says_what_to_run(self):
        from storyloop.cli import dispatch, parser
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            with self.assertRaisesRegex(StoryLoopError, "map-propose"):
                dispatch(parser().parse_args(["map-apply", str(root), "--proposal", "0" * 64]))

    def test_map_propose_runs_the_survey_role_through_the_cli(self):
        from unittest.mock import patch
        from storyloop.cli import dispatch, parser
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            readiness_repository(root)
            answer = {"modules": [{"id": "core", "name": "Core", "responsibility": "All", "boundaries": "None",
                                   "code_areas": ["app"]}], "assignments": [], "questions": []}
            with patch("storyloop.cli.invoke_project_agent", return_value=answer):
                result = dispatch(parser().parse_args(["map-propose", str(root)]))
        self.assertEqual((result["status"], result["modules"]), ("MAP_PROPOSED", ["core"]))


if __name__ == "__main__":
    unittest.main()
