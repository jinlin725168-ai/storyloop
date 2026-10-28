"""Failed attempts go to whoever has to fix them (subproject 2a §3)."""

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from storyloop.acceptance_repair import repair_smoke
from storyloop.approvals import approve_change
from storyloop.base_upgrade import draft_from_proposal
from storyloop.changes import load_change
from storyloop.baseline import bootstrap_project, load_project_config
from tests.routing_support import legacy_create_change as create_change
from storyloop.cli import parser, run_with_answers
from storyloop.evidence_locations import evidence_location_exists
from storyloop.models import StoryLoopError
from storyloop.project_loop import LoopServices, load_run_state, run_change
from storyloop.project_loop_support import save_run
from storyloop.project_loop_failures import record_boundary
from storyloop.project_boundaries import audit_project_changes
from storyloop.storage import write_json
from tests.support import product
from tests.boundary_diagnostics import diagnose_core_boundary
from tests.test_baseline_behavior import git, repository
from tests.test_project_loop import smoke_payload

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def approved(root: Path, max_attempts: int = 3, add_core: bool = False,
             add_pom: bool = False, foundation_files: dict[str, bytes] | None = None) -> str:
    repository(root)
    if add_core:
        (root / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(root, "add", "core.py")
        git(root, "commit", "-m", "core")
    if add_pom:
        (root / "pom.xml").write_text("<project>old</project>\n", encoding="utf-8")
        git(root, "add", "pom.xml")
        git(root, "commit", "-m", "pom")
    if foundation_files:
        for name, content in foundation_files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            git(root, "add", name)
        git(root, "commit", "-m", "foundation fixtures")
    bootstrap_project(root, {"test_commands": []})
    config = load_project_config(root)
    config["max_attempts"] = max_attempts
    write_json(root / ".storyloop/config.json", config)
    change_id = create_change(root, "orders", product(), now=lambda: NOW)["change_id"]
    approve_change(root, change_id, now=lambda: NOW)
    return change_id


def implementer(calls: list):
    def implement(project, context):
        calls.append(context)
        target = project / "modules/orders/service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 1\n", encoding="utf-8")
        return {"summary": "done", "questions": []}
    return implement


def failing_review(*findings):
    return lambda project, context: {
        "verdict": "fail", "summary": "no", "findings": list(findings), "questions": []}


def oracle_finding(change_id):
    return {"message": "the test reads .git", "target": "oracle", "criterion_id": "AC-1",
            "location": f"acceptance/changes/{change_id}/flow.py:3"}


class EvidenceLocationTests(unittest.TestCase):
    def test_candidate_location_requires_a_real_line_and_rejects_escaping_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            outside = root.parent / f"{root.name}-outside.py"
            outside.write_text("one\ntwo\n", encoding="utf-8")
            try:
                (root / "linked.py").symlink_to(outside)
                self.assertFalse(evidence_location_exists(root, "linked.py:1", None))
                (root / "plain.py").write_text("one\n", encoding="utf-8")
                self.assertTrue(evidence_location_exists(root, "plain.py:1", None))
                self.assertFalse(evidence_location_exists(root, "plain.py:2", None))
            finally:
                outside.unlink(missing_ok=True)

    def test_contract_commit_location_rejects_a_symlink_object(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository(root)
            (root / "target.py").write_text("one\n", encoding="utf-8")
            (root / "folder").mkdir()
            (root / "folder/inside.py").write_text("one\n", encoding="utf-8")
            (root / "linked.py").symlink_to("target.py")
            git(root, "add", "target.py", "linked.py", "folder/inside.py")
            git(root, "commit", "-m", "evidence")
            commit = git(root, "rev-parse", "HEAD")
            (root / "linked.py").unlink()
            self.assertTrue(evidence_location_exists(root, "target.py:1", commit))
            self.assertFalse(evidence_location_exists(root, "linked.py:1", commit))
            self.assertFalse(evidence_location_exists(root, "folder:1", commit))


class ReviewRoutingTests(unittest.TestCase):
    def test_cli_forwards_overrule_only_on_the_first_run(self):
        args = parser().parse_args(["run", ".", "--change", "FEAT-1", "--overrule", "human reason"])
        seen = []

        def runner(project, change_id, **kwargs):
            seen.append(kwargs)
            return {"status": "IMPLEMENTING"}

        run_with_answers(Path("."), args.change_id, False, runner=runner, overrule=args.overrule)
        self.assertEqual(seen, [{"overrule": "human reason"}])

    def test_an_oracle_finding_stops_on_the_first_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            calls = []
            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id), implement=implementer(calls),
                review=failing_review(oracle_finding(change_id))))
            self.assertEqual(result["status"], "ORACLE_DEFECT")
            state = load_run_state(root, change_id)
            self.assertEqual(len(calls), 1)
            self.assertEqual(state["routed"]["target"], "oracle")
            self.assertEqual(state["failures"][-1]["attribution"], "oracle")

    def test_contract_beats_oracle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            contract = {"message": "contradiction", "target": "contract", "criterion_id": "AC-1",
                        "location": "modules/orders/service.py:1"}
            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id), implement=implementer([]),
                review=failing_review(oracle_finding(change_id), contract)))
            self.assertEqual(result["status"], "NEEDS_SPEC_FIX")

    def test_plain_run_on_a_review_routed_stop_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            services = LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id),
                                    implement=implementer([]), review=failing_review(oracle_finding(change_id)))
            run_change(root, change_id, services)
            with self.assertRaises(StoryLoopError) as caught:
                run_change(root, change_id, services)
            self.assertIn("--overrule", str(caught.exception))
            self.assertIn("acceptance-repair", str(caught.exception))

    def test_an_overrule_rewrites_the_finding_target_and_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=2)
            calls = []
            services = LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id),
                                    implement=implementer(calls), review=failing_review(oracle_finding(change_id)))
            run_change(root, change_id, services)
            before = load_run_state(root, change_id)["failures"][0]["signature"]
            result = run_change(root, change_id, services, overrule="判据没错，应当在实现里解决")
            state = load_run_state(root, change_id)
            self.assertEqual(result["status"], "BUDGET_EXHAUSTED")
            self.assertEqual(state["failures"][0]["attribution"], "implement")
            self.assertEqual(state["failures"][0]["review"]["findings"][0]["target"], "implementation")
            self.assertNotEqual(state["failures"][0]["signature"], before)
            self.assertIn("已被人驳回", state["failures"][-1]["review"]["findings"][0]["attribution_note"])

    def test_overruling_contract_in_a_mixed_stop_still_routes_the_oracle_finding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            contract = {"message": "contradiction", "target": "contract", "criterion_id": "AC-1",
                        "location": "modules/orders/service.py:1"}
            services = LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id),
                                    implement=implementer([]),
                                    review=failing_review(oracle_finding(change_id), contract))
            self.assertEqual(run_change(root, change_id, services)["status"], "NEEDS_SPEC_FIX")
            result = run_change(root, change_id, services, overrule="合同并不矛盾")
            self.assertEqual(result["status"], "ORACLE_DEFECT")
            state = load_run_state(root, change_id)
            targets = [item["target"] for item in state["failures"][0]["review"]["findings"]]
            self.assertEqual(targets, ["oracle", "implementation"])

    def test_invalid_overrule_does_not_create_a_feature_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            before = git(root, "branch", "--list")
            with self.assertRaises(StoryLoopError):
                run_change(root, change_id, LoopServices(
                    generate_smoke=lambda p, s: smoke_payload(change_id), implement=implementer([]),
                    review=failing_review()), overrule="not applicable")
            self.assertEqual(git(root, "branch", "--list"), before)
            self.assertFalse((root / ".storyloop/state/runs" / f"{change_id}.json").exists())

    def test_acceptance_repair_clears_routing_and_starts_a_new_epoch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root)
            run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id), implement=implementer([]),
                review=failing_review(oracle_finding(change_id))))

            def regenerate(project, request):
                repaired = smoke_payload(change_id)
                repaired["files"][0]["content"] += "# repaired\n"
                return repaired

            repair_smoke(root, change_id, "reviewer is right", LoopServices(
                generate_smoke=regenerate, implement=implementer([]), review=failing_review()))
            state = load_run_state(root, change_id)
            self.assertNotIn("routed", state)
            self.assertNotIn("mode", state)
            self.assertEqual(len(state["repairs"]), 1)


class BoundaryAttributionTests(unittest.TestCase):
    def test_large_lock_and_binary_wrapper_get_metadata_only_references(self):
        cases = {
            "package-lock.json": (b"a" * 200_100, b"b" * 200_101),
            "gradle/wrapper/gradle-wrapper.jar": (b"\x00old", b"\x00new"),
        }
        for name, (before, after) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                change_id = approved(root, add_core=True, foundation_files={name: before})

                def implement(project, context):
                    (project / name).write_bytes(after)
                    return {"summary": "base dependency", "questions": []}

                result = run_change(root, change_id, LoopServices(
                    generate_smoke=lambda p, s: smoke_payload(change_id),
                    implement=implement, review=failing_review()))
                self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
                self.assertEqual((root / name).read_bytes(), before)
                proposal = json.loads(Path(result["base_proposal"]).read_text())
                reference = json.loads((root / proposal["reference"]["path"]).read_text())
                entry = next(item for item in reference["changes"] if item["path"] == name)
                self.assertFalse(entry["content_saved"])
                self.assertEqual(entry["after_size"], len(after))
                self.assertTrue(entry["after_oid"])
                self.assertNotIn("after", entry)

    def test_unlinked_historical_run_keeps_old_collision_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=1, add_core=True, add_pom=True)

            def first(project, context):
                target = project / "modules/orders/service.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 2\n", encoding="utf-8")
                return {"summary": "first", "questions": []}

            self.assertEqual(run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id), implement=first,
                review=lambda p, c: {"verdict": "pass", "summary": "ok",
                                     "findings": [], "questions": []}))["status"], "BUDGET_EXHAUSTED")
            state = load_run_state(root, change_id)
            state.pop("e3_collision", None)
            save_run(root, state)
            config = load_project_config(root)
            config["max_attempts"] = 3
            write_json(root / ".storyloop/config.json", config)

            def second(project, context):
                (project / "pom.xml").write_text("<project>new</project>\n", encoding="utf-8")
                return {"summary": "second", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id), implement=second,
                review=lambda p, c: {"verdict": "pass", "summary": "ok",
                                     "findings": [], "questions": []}))
            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertNotIn("blocked_by", load_run_state(root, change_id))
            self.assertEqual((root / "pom.xml").read_text(), "<project>new</project>\n")

    def test_second_attempt_collision_keeps_first_attempt_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=3, add_core=True, add_pom=True)
            calls = []

            def implement(project, context):
                calls.append(context["attempt"])
                if context["attempt"] == 1:
                    target = project / "modules/orders/service.py"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("VALUE = 2\n", encoding="utf-8")
                else:
                    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")
                return {"summary": "candidate", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id),
                implement=implement,
                review=lambda p, c: {"verdict": "pass", "summary": "ok",
                                     "findings": [], "questions": []}))
            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertEqual(calls, [1, 2])
            self.assertEqual((root / "modules/orders/service.py").read_text(), "VALUE = 2\n")
            self.assertEqual((root / "pom.xml").read_text(), "<project>old</project>\n")
            self.assertEqual(load_run_state(root, change_id)["blocked_by"],
                             Path(result["base_proposal"]).stem)

    def test_unowned_write_during_boundary_stop_preserves_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, add_core=True)

            def implement(project, context):
                (project / "pom.xml").write_text("<project>candidate</project>\n", encoding="utf-8")
                return {"summary": "dependency needed", "questions": []}

            def audit_with_external_write(project, selected_change):
                result = audit_project_changes(project, selected_change)
                (project / "personal.txt").write_text("human note\n", encoding="utf-8")
                return result

            with patch("storyloop.project_loop.audit_project_changes",
                       side_effect=audit_with_external_write):
                with self.assertRaises(StoryLoopError) as caught:
                    run_change(root, change_id, LoopServices(
                        generate_smoke=lambda p, s: smoke_payload(change_id),
                        implement=implement, review=failing_review()))
            self.assertEqual(caught.exception.code, "WORKSPACE_TAINTED")
            self.assertTrue((root / "pom.xml").exists())
            self.assertEqual((root / "personal.txt").read_text(encoding="utf-8"), "human note\n")

    def test_base_collision_opens_linked_draft_and_restores_owned_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, add_core=True)

            def implement(project, context):
                (project / "pom.xml").write_text(
                    "<project><dependency>sample</dependency></project>\n", encoding="utf-8")
                target = project / "modules/orders/service.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = 1\n", encoding="utf-8")
                return {"summary": "dependency and feature", "questions": []}

            result = run_change(root, change_id, LoopServices(
                generate_smoke=lambda p, s: smoke_payload(change_id),
                implement=implement, review=failing_review()))

            base_id = Path(result["base_proposal"]).stem
            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertEqual(load_change(root, change_id)["blocked_by"], base_id)
            self.assertEqual(load_run_state(root, change_id)["blocked_by"], base_id)
            self.assertIn(f"--change {base_id}", result["next"])
            self.assertIn(f"--change {base_id}", result["stop"]["options"][0]["command"])
            draft = load_change(root, base_id)
            self.assertEqual(draft["status"], "DRAFT")
            self.assertEqual(draft["product"]["base"]["paths"], ["pom.xml"])
            self.assertNotIn("reference", draft["product"])
            proposal = json.loads(Path(result["base_proposal"]).read_text(encoding="utf-8"))
            reference = root / proposal["reference"]["path"]
            self.assertEqual(hashlib.sha256(reference.read_bytes()).hexdigest(),
                             proposal["reference"]["sha256"])
            self.assertIn("<dependency>sample</dependency>", reference.read_text(encoding="utf-8"))
            preview = (root / ".storyloop/drafts" / base_id / "proposal.md").read_text(encoding="utf-8")
            self.assertIn(proposal["reference"]["path"], preview)
            self.assertIn("仅供参考", preview)
            self.assertEqual((root / "pom.xml").read_text(encoding="utf-8"), "<project/>\n")
            self.assertFalse((root / "modules/orders/service.py").exists())
            self.assertFalse(git(root, "status", "--porcelain"))
            reference.write_text("altered reference\n", encoding="utf-8")
            with self.assertRaises(StoryLoopError):
                draft_from_proposal(root, base_id, scope="foundation")

    def test_repeated_boundary_failure_stops_at_no_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, max_attempts=6, add_core=True)
            def implement(project, context):
                (project / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
                return {"summary": "changed base", "questions": []}

            services = LoopServices(generate_smoke=lambda p, s: smoke_payload(change_id),
                                    implement=implement, review=failing_review())
            first = run_change(root, change_id, services)
            self.assertEqual(first["status"], "BASE_CHANGE_REQUIRED")
            state = load_run_state(root, change_id)
            boundary = {"status": first["status"], "violations": first["violations"],
                        "base_proposal": first["base_proposal"]}
            state["attempt"] = 2
            state["mode"] = None
            self.assertEqual(record_boundary(root, state, boundary)["status"], "BASE_CHANGE_REQUIRED")
            state["attempt"] = 3
            state["mode"] = "root_cause"
            result = record_boundary(root, state, boundary)
            self.assertEqual(result["status"], "NO_PROGRESS")
            self.assertEqual([item["mode"] for item in state["failures"]],
                             [None, None, "root_cause"])

    def test_boundary_stop_records_base_attribution_and_signature_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            change_id = approved(root, add_core=True)

            def implement(project, context):
                (project / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
                return {"summary": "changed base", "questions": []}

            with diagnose_core_boundary(root):
                result = run_change(root, change_id, LoopServices(
                    generate_smoke=lambda p, s: smoke_payload(change_id), implement=implement,
                    review=failing_review()))
            state = load_run_state(root, change_id)
            self.assertEqual(result["status"], "BASE_CHANGE_REQUIRED")
            self.assertEqual(len(state["failures"]), 1)
            self.assertEqual(state["failures"][0]["attribution"], "base")
            self.assertTrue(state["failures"][0]["signature"])


if __name__ == "__main__":
    unittest.main()
