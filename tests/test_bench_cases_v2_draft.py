"""Check the reviewable v2 Case drafts without requiring local source repositories."""

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from storyloop.testing.smoke_harness.python_runner import python_cases
from storyloop.bench_case import _check_inventory, _gold, load_case
from storyloop.changes import contract_digest
from storyloop.product_contract import validate_product
from storyloop.requirement_checklist import evaluate_checklist
from storyloop.smoke import _validate_manifest


CASES = Path(__file__).resolve().parents[1] / "bench/cases"


class BenchV2DraftTests(unittest.TestCase):
    def test_frozen_case_bytes_are_not_rewritten_by_checkout(self):
        source = CASES / "dogfood3-stash-git-safe/v2/case.json"
        result = subprocess.run(
            ["git", "check-attr", "text", "--", str(source)],
            cwd=CASES.parents[1], capture_output=True, text=True, check=True,
        )
        self.assertIn("text: unset", result.stdout)

    def test_derived_cases_keep_source_interval_and_gold_commands(self):
        for original in sorted(CASES.glob("*/v1")):
            with self.subTest(case=original.parent.name):
                draft = original.parent / "v2"
                old_case = json.loads((original / "case.json").read_text(encoding="utf-8"))
                old_contract = json.loads((original / "contract.json").read_text(encoding="utf-8"))
                case = json.loads((draft / "case.json").read_text(encoding="utf-8"))
                contract = json.loads((draft / "contract.json").read_text(encoding="utf-8"))
                gold = json.loads((draft / "gold.json").read_text(encoding="utf-8"))
                old_gold = json.loads((original / "gold.json").read_text(encoding="utf-8"))
                self.assertEqual(case["case_version"], 2)
                self.assertFalse(case["legacy_seam"])
                for key in ("case_id", "repository_id", "base_commit", "solution_commit"):
                    self.assertEqual(case[key], old_case[key])
                self.assertEqual(case["case_revision"], "2.1")
                self.assertEqual(contract["approval"]["contract_sha256"],
                                 contract_digest(contract["product"]))
                prior = old_contract["product"]
                derived = json.loads(json.dumps(contract["product"]))
                for story in derived["stories"]:
                    del story["verification_entries"]
                    for criterion in story["acceptance"]:
                        del criterion["entry_ids"]
                        if (original.parent.name == "dogfood9-scratch-glob"
                                and criterion["id"] == "AC-4"):
                            criterion["then"] = next(item["then"] for old_story in prior["stories"]
                                                     for item in old_story["acceptance"]
                                                     if item["id"] == "AC-4")
                self.assertEqual(derived, prior)
                self.assertTrue({item["check_id"] for item in old_gold["checks"]}
                                <= {item["check_id"] for item in gold["checks"]})
                self.assertEqual(hashlib.sha256((draft / "contract.json").read_bytes()).hexdigest(),
                                 case["contract_sha256"])
                self.assertEqual(hashlib.sha256((draft / "gold.json").read_bytes()).hexdigest(),
                                 case["gold_sha256"])
                self.assertTrue(load_case(draft)["contract_approved"])

    def test_approved_entry_locators_name_the_public_commands(self):
        expected = {
            "dogfood3-stash-git-safe": ("VE-BASE-STOP", "stop_json", "storyloop status"),
            "dogfood5-stash-hint-coverage": ("VE-BASE-STOP", "stop_json", "storyloop status"),
            "dogfood6-design-revision-findings": ("VE-DESIGN-STOP", "stop_json", "storyloop status"),
            "dogfood9-scratch-glob": ("VE-ONBOARDING-DRAFT", "cli", "storyloop init"),
        }
        for case_id, (entry_id, kind, command) in expected.items():
            with self.subTest(case=case_id):
                source = CASES / case_id / "v2"
                contract = json.loads((source / "contract.json").read_text(encoding="utf-8"))
                entry = next(entry for story in contract["product"]["stories"]
                             for entry in story["verification_entries"] if entry["id"] == entry_id)
                self.assertEqual(entry["kind"], kind)
                self.assertIn(command, entry["locator"])

    def test_entries_suite_and_provenance_are_linked(self):
        for draft in sorted(CASES.glob("*/v2")):
            with self.subTest(case=draft.parent.name):
                case = json.loads((draft / "case.json").read_text(encoding="utf-8"))
                contract = json.loads((draft / "contract.json").read_text(encoding="utf-8"))
                evidence = json.loads((draft / "verification-evidence.json").read_text(encoding="utf-8"))
                manifest_path = next(draft.glob("checks/acceptance/changes/*/manifest.json"))
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                product = validate_product(contract["product"])
                _validate_manifest(manifest, {"product": product})
                self.assertFalse([item for item in evaluate_checklist(product, require_entries=True)
                                  if item["level"] == "error"])
                owners = {entry["id"]: story["id"] for story in contract["product"]["stories"]
                          for entry in story["verification_entries"]}
                gold = json.loads((draft / "gold.json").read_text(encoding="utf-8"))
                gold_by_criterion = {item["requirement_id"]: item["check_ids"]
                                     for item in gold["requirements"]}
                criteria = {item["id"]: story["id"] for story in contract["product"]["stories"]
                            for item in story["acceptance"]}
                self.assertEqual(set(owners), set(evidence))
                self.assertEqual(len(owners), sum(len(story["verification_entries"])
                                                  for story in contract["product"]["stories"]))
                for entry_id, item in evidence.items():
                    self.assertTrue(item["chain"])
                    self.assertTrue(all(ref["path"] and ref["line"] > 0 and ref["quote"]
                                        for ref in item["chain"]))
                    linked = [test for test in manifest["tests"] if entry_id in test["entry_ids"]]
                    self.assertEqual(item["manifest_test_ids"], [test["id"] for test in linked])
                    self.assertEqual(item["criterion_ids"],
                                     sorted({criterion for test in linked for criterion in test["criteria"]}))
                    self.assertEqual(item["gold_check_ids"], sorted({check
                                     for criterion in item["criterion_ids"]
                                     for check in gold_by_criterion[criterion]}))
                for test in manifest["tests"]:
                    self.assertTrue(test["entry_ids"])
                    self.assertEqual(test["command"][0], "{storyloop_harness}")
                    self.assertEqual(set(test["entry_ids"]),
                                     {entry for entry in test["entry_ids"]
                                      if owners[entry] in {criteria[item] for item in test["criteria"]}})
                suite = hashlib.sha256()
                for item in case["suite_files"]:
                    self.assertNotIn("__pycache__", Path(item["path"]).parts)
                    self.assertNotEqual(Path(item["path"]).suffix, ".pyc")
                    content = (draft / item["path"]).read_bytes()
                    self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])
                    suite.update(item["path"].encode() + b"\0" + content)
                self.assertEqual(suite.hexdigest(), case["suite_sha256"])
                _check_inventory(draft, [item["path"] for item in case["suite_files"]])
                _gold(json.loads((draft / "gold.json").read_text(encoding="utf-8")), draft, contract)

    def test_protected_selectors_match_original_tests(self):
        for draft in sorted(CASES.glob("*/v2")):
            for wrapper in draft.glob("checks/acceptance/changes/*/bench_*.py"):
                with self.subTest(wrapper=wrapper):
                    tree = ast.parse(wrapper.read_text(encoding="utf-8"))
                    constants = {node.targets[0].id: ast.literal_eval(node.value)
                                 for node in tree.body if isinstance(node, ast.Assign)
                                 and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                                 and node.targets[0].id in {"SOURCE", "SHA256", "SELECTOR"}}
                    source = wrapper.with_name(constants["SOURCE"])
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), constants["SHA256"])
                    tests = {f"{item.name}.{method.name}" for item in ast.parse(source.read_text(encoding="utf-8")).body
                             if isinstance(item, ast.ClassDef)
                             for method in item.body if isinstance(method, ast.FunctionDef)
                             and method.name.startswith("test_")}
                    classes = {item.name for item in ast.parse(source.read_text(encoding="utf-8")).body
                               if isinstance(item, ast.ClassDef)}
                    self.assertIn(constants["SELECTOR"], tests | classes)

    def test_selector_wrapper_runs_only_its_selected_method(self):
        original = next(CASES.glob("dogfood3-stash-git-safe/v2/checks/acceptance/changes/*/bench_*.py"))
        code = original.read_text(encoding="utf-8")
        source = ("import unittest\n"
                  "class Demo(unittest.TestCase):\n"
                  "    def test_selected(self): pass\n"
                  "    def test_other(self): self.fail('not selected')\n")
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            # The selector hashes source bytes, so a Windows text write must not add CRLF.
            (project / "test_sample.py").write_bytes(source.encode("utf-8"))
            code = code.replace("SOURCE = 'test_core.py'", "SOURCE = 'test_sample.py'")
            old_hash = next(line.split("'")[1] for line in code.splitlines()
                            if line.startswith("SHA256 = "))
            code = code.replace(old_hash, hashlib.sha256(source.encode()).hexdigest())
            old_selector = next(line.split("'")[1] for line in code.splitlines()
                                if line.startswith("SELECTOR = "))
            code = code.replace(old_selector, "Demo.test_selected")
            (project / "selected.py").write_text(code, encoding="utf-8")
            result = python_cases(project, ["selected.py"], cwd=project)
            self.assertEqual((result.executed, result.failed, result.errors), (1, 0, 0))


if __name__ == "__main__":
    unittest.main()
