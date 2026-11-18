"""Skill release gate (roadmap D2, 2026-11-17): evidence is for exact content, never assumed.

Skills were changed and shipped 14 times in two weeks (spec-craft 1.4.0 -> 1.4.7) with only text
assertions; whether a version actually worked on a real run was never recorded. Evidence is now
per version and content digest, so editing a skill without new evaluation makes it "pending".
"""
import json
import unittest

from storyloop.skills import packaged_skills
from tests.skill_release import EVALS, VERDICTS, cases, results, skill_sha256, status


class SkillReleaseGateTests(unittest.TestCase):
    def test_every_packaged_skill_declares_cases(self):
        for name in packaged_skills():
            with self.subTest(skill=name):
                declared = cases(name)
                self.assertTrue(declared, f"declare evals/skills/{name}/cases.json")
                keys = [case["key"] for case in declared]
                self.assertEqual(len(keys), len(set(keys)))
                for case in declared:
                    self.assertTrue(case.get("name") and case.get("expected"))

    def test_results_match_the_exact_content_of_their_version(self):
        skills = packaged_skills()
        for path in sorted(EVALS.glob("*/*.json")):
            if path.name == "cases.json":
                continue
            name, version = path.parent.name, path.stem
            with self.subTest(results=f"{name}/{version}"):
                self.assertIn(name, skills, "results for a skill that is not packaged")
                record = results(name, version)
                self.assertEqual(record["version"], version)
                if version == skills[name]["version"]:
                    # Evidence recorded for other bytes must not certify the shipped skill.
                    self.assertEqual(record["skill_sha256"], skill_sha256(skills[name]),
                                     f"{name} {version} changed after its evaluation: bump the "
                                     "version and record new results")
                declared = {case["key"] for case in cases(name)}
                for item in record["results"]:
                    self.assertIn(item["case"], declared)
                    self.assertIn(item["verdict"], VERDICTS)
                    self.assertTrue(item.get("evidence", "").strip() and item.get("reviewed_by"))

    def test_status_follows_the_evidence(self):
        skills = packaged_skills()
        states = {name: status(name, skill)["state"] for name, skill in skills.items()}
        # 1.4.8 (batch 3 handoff) has no evaluation yet: pending until batch 5 records its samples.
        self.assertIn(states["spec-craft"], {"verified", "pending"})
        self.assertEqual(states["review"], "verified")
        # No real survey run yet: declared but unproven, and reported as such.
        self.assertEqual(states["foundation-survey"], "pending")
        edited = dict(skills["review"], files=[])
        self.assertEqual(status("review", edited)["state"], "pending")

    def test_cases_files_are_valid_json_objects(self):
        for path in EVALS.glob("*/cases.json"):
            with self.subTest(path=path.parent.name):
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)


if __name__ == "__main__":
    unittest.main()
