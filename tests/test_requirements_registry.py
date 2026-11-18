"""Requirement registry: stable IDs, versioned history and concurrency checks (link skeleton 2)."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.requirements_registry import apply_record, conflicts, plan, replay, write_record
from tests.support import product
from tests.test_baseline_behavior import git


def empty():
    return {"requirements": {}, "rules": {}, "examples": {}}


class PlanTests(unittest.TestCase):
    def test_new_contract_gets_stable_ids_in_contract_order(self):
        result = plan(product(), "FEAT-20261118-rooms", empty())
        self.assertEqual(result["ids"]["requirements"], {"ST-1": "FEAT-20261118-rooms.S1"})
        self.assertEqual(result["ids"]["rules"], {"ST-1#1": "FEAT-20261118-rooms.R1"})
        self.assertEqual(result["ids"]["examples"], {"AC-1": "FEAT-20261118-rooms.E1"})
        self.assertEqual({item["op"] for kind in ("requirements", "rules", "examples")
                          for item in result["record"][kind]}, {"created"})

    def test_revision_keeps_assigned_numbers_and_appends_new_ones(self):
        first = plan(product(), "FEAT-1", empty())["ids"]
        revised = product()
        revised["stories"][0]["rules"] = ["Second rule", "No overlap"]
        ids = plan(revised, "FEAT-1", empty(), previous=first)["ids"]
        self.assertEqual(ids["rules"]["ST-1#1"], "FEAT-1.R1")
        self.assertEqual(ids["rules"]["ST-1#2"], "FEAT-1.R2")

    def test_referencing_an_existing_rule_modifies_it_with_its_base_version(self):
        registry = empty()
        apply_record(registry, plan(product(), "FEAT-1", empty())["record"])
        later = product()
        later["stories"][0].update(requirement="FEAT-1.S1", rule_ids=["FEAT-1.R1"])
        later["stories"][0]["rules"] = ["No overlap, including back-to-back bookings"]
        record = plan(later, "FEAT-2", registry)["record"]
        rule = next(item for item in record["rules"] if item["id"] == "FEAT-1.R1")
        self.assertEqual((rule["op"], rule["base_version"]), ("modified", 1))
        story = next(item for item in record["requirements"] if item["id"] == "FEAT-1.S1")
        self.assertEqual(story["op"], "referenced")  # unchanged story fields do not bump it
        apply_record(registry, record)
        self.assertEqual(registry["rules"]["FEAT-1.R1"]["version"], 2)
        self.assertEqual([h["change"] for h in registry["rules"]["FEAT-1.R1"]["history"]], ["FEAT-1", "FEAT-2"])

    def test_unknown_or_retired_references_are_refused(self):
        registry = empty()
        apply_record(registry, plan(product(), "FEAT-1", empty())["record"])
        bad = product()
        bad["stories"][0]["rule_ids"] = ["FEAT-9.R1"]
        with self.assertRaisesRegex(StoryLoopError, "not an active rule"):
            plan(bad, "FEAT-2", registry)
        retire = product()
        retire["retire"] = ["FEAT-1.R1"]
        apply_record(registry, plan(retire, "FEAT-3", registry)["record"])
        self.assertEqual(registry["rules"]["FEAT-1.R1"]["status"], "retired")
        with self.assertRaisesRegex(StoryLoopError, "not an active rule"):
            plan(bad | {"stories": [dict(bad["stories"][0], rule_ids=["FEAT-1.R1"])]}, "FEAT-4", registry)

    def test_a_concurrent_modification_is_a_conflict(self):
        registry = empty()
        apply_record(registry, plan(product(), "FEAT-1", empty())["record"])
        change = product()
        change["stories"][0].update(rule_ids=["FEAT-1.R1"], rules=["Changed by FEAT-2"])
        record = plan(change, "FEAT-2", registry)["record"]
        other = deepcopy(change)
        other["stories"][0]["rules"] = ["Changed by FEAT-3"]
        apply_record(registry, plan(other, "FEAT-3", registry)["record"])
        self.assertEqual(conflicts(record, registry), ["FEAT-1.R1 is at version 2, this change was based on 1"])

    def test_fix_rules_count_against_the_rule(self):
        registry = empty()
        apply_record(registry, plan(product(), "FEAT-1", empty())["record"])
        apply_record(registry, {"change_id": "FIX-1", "requirements": [], "rules": [], "examples": [],
                                "fixes": ["FEAT-1.R1"]})
        self.assertEqual(registry["rules"]["FEAT-1.R1"]["fixes"], ["FIX-1"])


class ContractReferenceTests(unittest.TestCase):
    def test_registry_references_are_optional_and_validated(self):
        from storyloop.product_contract import validate_product
        validate_product(product())  # old contracts need none of them
        value = product()
        value["stories"][0].update(requirement="FEAT-1.S1", rule_ids=["FEAT-1.R1"])
        value["stories"][0]["acceptance"][0].update(rule=1, example="FEAT-1.E1")
        value["retire"] = ["FEAT-1.R2"]
        validate_product(value)
        for mutate in (lambda v: v["stories"][0].update(rule_ids=["FEAT-1.S1"]),
                       lambda v: v["stories"][0].update(rule_ids=["new", "new"]),
                       lambda v: v["stories"][0]["acceptance"][0].update(rule=2),
                       lambda v: v.update(retire=["not-an-id"])):
            broken = deepcopy(value)
            mutate(broken)
            with self.subTest(), self.assertRaises(StoryLoopError):
                validate_product(broken)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "storyloop@example.invalid")
        git(self.root, "config", "user.name", "StoryLoop Test")

    def commit(self, record):
        path = write_record(self.root, record, "HEAD") if self.has_head() else self.raw(record)
        git(self.root, "add", path)
        git(self.root, "commit", "-qm", f"docs({record['change_id']}): registry")

    def has_head(self):
        try:
            git(self.root, "rev-parse", "HEAD")
            return True
        except Exception:
            return False

    def raw(self, record):
        path = Path("openspec/requirements/changes") / f"{record['change_id']}.json"
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(json.dumps(record), encoding="utf-8")
        return path.as_posix()

    def test_replay_follows_commit_order_and_write_refuses_stale_bases(self):
        self.commit(plan(product(), "FEAT-1", empty())["record"])
        later = product()
        later["stories"][0].update(rule_ids=["FEAT-1.R1"], rules=["Revised"])
        stale = plan(later, "FEAT-2", replay(self.root))["record"]
        self.commit(plan(dict(later, stories=[dict(later["stories"][0], rules=["Other"])]), "FEAT-3",
                         replay(self.root))["record"])
        registry = replay(self.root)
        self.assertEqual(registry["rules"]["FEAT-1.R1"]["version"], 2)
        self.assertEqual(registry["rules"]["FEAT-1.R1"]["text"], "Other")
        with self.assertRaisesRegex(StoryLoopError, "registry changed since approval"):
            write_record(self.root, stale, "HEAD")


if __name__ == "__main__":
    unittest.main()


class RequirementsCommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "storyloop@example.invalid")
        git(self.root, "config", "user.name", "StoryLoop Test")
        record = plan(product(), "FEAT-1", empty())["record"]
        path = self.root / "openspec/requirements/changes/FEAT-1.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record), encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "docs(FEAT-1): registry")

    def test_requirements_lists_requirements_with_their_rules(self):
        from storyloop.cli import dispatch, parser
        result = dispatch(parser().parse_args(["requirements", str(self.root)]))
        self.assertEqual(result["requirements"][0]["id"], "FEAT-1.S1")
        self.assertEqual(result["requirements"][0]["rules"][0]["id"], "FEAT-1.R1")
        one = dispatch(parser().parse_args(["requirements", str(self.root), "--id", "FEAT-1.R1"]))
        self.assertEqual(one["item"]["history"][0]["change"], "FEAT-1")

    def test_requirements_rejects_an_unknown_id(self):
        from storyloop.cli import dispatch, parser
        with self.assertRaisesRegex(StoryLoopError, "Unknown registry ID"):
            dispatch(parser().parse_args(["requirements", str(self.root), "--id", "FEAT-9.R1"]))


class RegistryLifecycleTests(unittest.TestCase):
    def test_a_passed_change_merges_its_registry_record_and_stable_ids_into_the_spec(self):
        from storyloop.testing.nested import nested_case
        with tempfile.TemporaryDirectory() as folder:
            case = nested_case(Path(folder).resolve(), "standard")
            self.assertEqual(case.run()["status"], "PASSED")
            registry = replay(case.root)
            requirement = f"{case.change_id}.S1"
            self.assertEqual(registry["requirements"][requirement]["version"], 1)
            self.assertEqual(registry["rules"][f"{case.change_id}.R1"]["requirement"], requirement)
            spec = next((case.root / "openspec/specs").rglob("spec.md")).read_text(encoding="utf-8")
            self.assertIn(f"_Requirement {requirement}_", spec)
            self.assertIn(f"[{case.change_id}.R1] ", spec)
            tracked = git(case.root, "ls-files", "openspec/requirements")
            self.assertIn(f"openspec/requirements/changes/{case.change_id}.json", tracked)
