"""Deterministic RED/GREEN, regression and independent QA execution."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from . import agents, verification
from .engine import SUCCESS, now
from .evidence import valid_evidence, sanitized
from .models import StoryLoopError, require, validate_review
from .processutil import redact
from .storage import load_json, write_json, approval_digest, workspace_digest, digest_tree


class Runner:
    def __init__(self, project: Path, product: dict, checks: list, config: dict, digest: str) -> None:
        self.project, self.product, self.checks, self.config, self.digest = project, product, checks, config, digest
        self.path = project / ".storyloop" / "state.json"
        self.state = load_json(self.path)
        require(self.state.get("approval_digest") == digest, "State belongs to a different approval")
        self.run_id = uuid.uuid4().hex
        self.record = None
        self.story = None

    def save(self) -> None:
        self.state["updated_at"] = now()
        write_json(self.path, self.state)

    def guard(self) -> None:
        require(approval_digest(self.project) == self.digest, "Approved input changed during execution")

    def fresh(self, story: dict) -> bool:
        record = self.state["stories"][story["id"]]
        return (record["status"] in SUCCESS and record.get("approval_digest") == self.digest
                and record.get("workspace_digest") == workspace_digest(self.project) and valid_evidence(self.project, record))

    def run(self, selected: str | None) -> None:
        stories = self.product["stories"]
        if selected is not None:
            require(selected in {s["id"] for s in stories}, "Unknown story")
            # Earlier stories supply prerequisites and regression evidence.
            stories = stories[:next(i for i, s in enumerate(stories) if s["id"] == selected) + 1]
        while True:
            pending = next((s for s in stories if not self.fresh(s)), None)
            if pending is None:
                return
            self.story = pending
            self.record = self.state["stories"][pending["id"]]
            if self.record.get("status") == "FAILED":
                return
            if self.record["status"] in SUCCESS and not valid_evidence(self.project, self.record):
                self.record["invalidated_evidence"] = self.record.get("evidence", [])
                for key in ("baseline", "baseline_kind", "review", "last_checks"):
                    self.record.pop(key, None)
                self.record.update(evidence=[], evidence_hashes={})
            try:
                self.work()
            except StoryLoopError as exc:
                self.record.update(status=exc.code, reason=redact(str(exc)))
                self.save()
                return
            except KeyboardInterrupt:
                self.record.update(status="INTERRUPTED", reason="Interrupted; resume with run")
                self.save()
                raise

    def evidence(self, phase: str, result: dict) -> None:
        relative = Path(".storyloop") / "evidence" / self.run_id / f"{self.story['id']}-{uuid.uuid4().hex}.json"
        write_json(self.project / relative, {"version": 1, "phase": phase, "story_id": self.story["id"],
                   "attempt": self.record["attempts"], "recorded_at": now(), "approval_digest": self.digest,
                   "workspace_digest": workspace_digest(self.project), "result": sanitized(result)})
        self.record.setdefault("evidence", []).append(relative.as_posix())
        self.record.setdefault("evidence_hashes", {})[relative.as_posix()] = hashlib.sha256((self.project / relative).read_bytes()).hexdigest()
        self.save()

    def external(self, action, timeout: int) -> object:
        self.guard()
        spent = self.record.get("elapsed_seconds", 0)
        remaining = self.config["max_seconds"] - spent
        if remaining < 0.1:
            raise StoryLoopError("Story time budget exhausted; adjust budget and approve to continue", "FAILED")
        allowance = min(timeout, remaining)
        # Reserve before launch: interruption cannot reset the process time budget.
        self.record["elapsed_seconds"] = spent + allowance
        self.save()
        protected = digest_tree([self.path, self.project / ".storyloop/approval.json", self.project / ".storyloop/evidence"])
        started = time.monotonic()
        try:
            return action(allowance)
        finally:
            unchanged = protected == digest_tree([self.path, self.project / ".storyloop/approval.json", self.project / ".storyloop/evidence"])
            self.record["elapsed_seconds"] = spent + (time.monotonic() - started)
            self.save()
            self.guard()
            require(unchanged, "External process modified controller state or evidence")

    def run_checks(self, checks: list, phase: str) -> list:
        results = []
        for check in checks:
            before = workspace_digest(self.project)
            timeout = min(check["timeout_seconds"], self.config["check_timeout_seconds"])
            result = self.external(lambda seconds: verification.run_check(self.project, check, seconds), timeout)
            require(before == workspace_digest(self.project), "A check modified implementation files; keep test data in temporary directories")
            results.append(result)
            self.evidence(phase, result)
        return results

    def own(self, kinds: set) -> list:
        return [c for c in self.checks if c["story_id"] == self.story["id"] and c["kind"] in kinds]

    def previous(self) -> list:
        previous = []
        for story in self.product["stories"]:
            if story["id"] == self.story["id"]:
                break
            if self.state["stories"][story["id"]]["status"] in SUCCESS:
                previous.append(story["id"])
        return [c for c in self.checks if c["story_id"] in previous]

    def checked(self, phase: str) -> list:
        checks = self.own({"health", "acceptance", "regression"}) + self.previous()
        results = self.run_checks(checks, phase)
        self.record["last_checks"] = results
        self.save()
        blocked = next((r for r in results if r["outcome"] == "BLOCKED"), None)
        require(blocked is None, f"Check blocked: {blocked['reason']}" if blocked else "")
        bad_health = next((r for r in results if r["kind"] == "health" and r["outcome"] != "GREEN"), None)
        require(bad_health is None, "Environment health failed; it is not a behavioral RED")
        return results

    def preflight(self) -> None:
        results = self.run_checks(self.own({"health"}) + self.previous(), "preflight")
        require(all(r["outcome"] == "GREEN" for r in results), "Environment or previous regression failed before this story")

    def work(self) -> None:
        self.record.update(status="RUNNING", reason="", run_id=self.run_id)
        self.save()
        self.preflight()
        results = self.checked("baseline")
        if "baseline" not in self.record:
            self.record["baseline"] = results
            self.record["baseline_kind"] = "RED" if any(r["outcome"] == "RED" for r in results) else "ALREADY_COVERED"
        while True:
            self.guard()
            if all(r["outcome"] == "GREEN" for r in results) and not self.record.get("needs_repair"):
                review = self.review(results)
                if review["verdict"] == "pass":
                    self.finish()
                    return
                if review["verdict"] == "needs_input":
                    raise StoryLoopError("; ".join(review["questions"]), "NEEDS_INPUT")
                self.record["needs_repair"] = True
            self.track_failure(results)
            self.implement(results)
            results = self.checked("verification")

    def track_failure(self, results: list) -> None:
        failures = [{"check": r["check_id"], "results": r["results"]} for r in results if r["outcome"] != "GREEN"]
        if not failures:
            failures = self.record.get("review", {}).get("findings", [])
        fingerprint = hashlib.sha256(json.dumps(failures, sort_keys=True).encode()).hexdigest()
        count = self.record.get("repeat_count", 0) + 1 if fingerprint == self.record.get("failure_fingerprint") else 1
        self.record.update(failure_fingerprint=fingerprint, repeat_count=count)
        self.save()
        if count > self.config["repeated_failure_limit"]:
            raise StoryLoopError("Repeated failure limit reached; evidence saved for replanning", "FAILED")

    def implement(self, results: list) -> None:
        if self.record["attempts"] >= self.config["max_attempts"]:
            raise StoryLoopError("Implementation attempt budget exhausted", "FAILED")
        self.record["attempts"] += 1
        self.save()
        payload = {"product": self.product, "story": self.story, "checks": results,
                   "review": self.record.get("review"), "attempt": self.record["attempts"],
                   "instruction": "Implement only approved behavior. Use red-green-refactor for internal tests. "
                   "Workspace write and local command execution are already authorized. Try them directly. "
                   "Never modify QA/controller files; only ask questions for unresolved business intent."}
        try:
            result = self.external(lambda seconds: agents.invoke(self.project, "implement", {**payload, "timeout_seconds": seconds}),
                                   self.config["agent_timeout_seconds"])
        except StoryLoopError as exc:
            if self.config["adapter"] == "manual" and exc.code == "NEEDS_INPUT":
                self.record["attempts"] -= 1
                self.record["repeat_count"] = max(0, self.record.get("repeat_count", 1) - 1)
                self.save()
            raise
        require(isinstance(result, dict) and set(result) == {"summary", "questions"}, "Invalid implementation response")
        require(isinstance(result["summary"], str) and bool(result["summary"].strip()), "Missing implementation summary")
        require(isinstance(result["questions"], list) and all(isinstance(q, str) for q in result["questions"]), "Invalid implementation questions")
        self.evidence("implementation", {"summary": redact(result["summary"]), "questions": sanitized(result["questions"])})
        if result["questions"]:
            raise StoryLoopError("; ".join(result["questions"]), "NEEDS_INPUT")
        self.record["needs_repair"] = False
        self.save()

    def review(self, results: list) -> dict:
        before = workspace_digest(self.project)
        payload = {"product": self.product, "story": self.story, "checks": results,
                   "instruction": "Independently inspect the current implementation against each criterion. Do not modify files. Distinguish automated evidence from human-only acceptance."}
        raw = self.external(lambda seconds: agents.invoke(self.project, "review", {**payload, "timeout_seconds": seconds}),
                            self.config["agent_timeout_seconds"])
        require(before == workspace_digest(self.project), "QA reviewer modified implementation files")
        result = sanitized(validate_review(raw, self.story))
        self.record["review"] = result
        self.evidence("review", result)
        return result

    def finish(self) -> None:
        self.guard()
        manual = any(c["kind"] == "manual" for c in self.story["acceptance"])
        self.record.update(status="AWAITING_ACCEPTANCE" if manual else "PASSED", reason="",
                           approval_digest=self.digest, workspace_digest=workspace_digest(self.project), finished_at=now())
        self.record.pop("accepted_note", None)
        self.record.pop("accepted_at", None)
        self.save()
