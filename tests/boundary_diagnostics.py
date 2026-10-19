"""Failure-only evidence for the synthetic core.py boundary regression fixture."""

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import sys
from unittest.mock import patch

from storyloop import project_loop, project_loop_guards
from storyloop.baseline import _git
from storyloop.models import StoryLoopError


def _core_git_evidence(root):
    commands = {
        "head": ("show", "HEAD:core.py"), "index": ("show", ":core.py"),
        "diff": ("diff", "--name-only", "-z", "HEAD", "--", "core.py"),
        "index_stat": ("ls-files", "--debug", "--", "core.py"),
    }
    result = {}
    for label, arguments in commands.items():
        try:
            value = _git(root, *arguments)
            result[label] = (hashlib.sha256(value.encode()).hexdigest()
                             if label in {"head", "index"} else value)
        except StoryLoopError:
            result[label] = "UNAVAILABLE"
    result["worktree"] = hashlib.sha256((root / "core.py").read_bytes()).hexdigest()
    return result


@contextmanager
def diagnose_core_boundary(root):
    """Retain the original error; emit only this fixture's synthetic boundary evidence."""
    evidence = {"core_snapshots": []}
    capture = project_loop_guards.capture_snapshot
    visible = project_loop.require_git_visible

    def snapshot(project, **kwargs):
        result = capture(project, **kwargs)
        core = dict(result.entries).get("core.py")
        if core:
            fields = ("size", "sha256", "mode", "mtime_ns")
            evidence["core_snapshots"].append({name: getattr(core, name) for name in fields})
        return result

    def check(touched, boundary):
        evidence["touched"] = [asdict(item) for item in touched]
        evidence["boundary"] = {key: boundary[key] for key in ("allowed", "violations")}
        return visible(touched, boundary)

    with patch.object(project_loop_guards, "capture_snapshot", snapshot), \
            patch.object(project_loop, "require_git_visible", check):
        try:
            yield
        except StoryLoopError:
            try:
                evidence["git"] = _core_git_evidence(root)
                print("Synthetic core boundary evidence: " + json.dumps(evidence), file=sys.stderr)
            except OSError:
                print("Synthetic core boundary evidence unavailable", file=sys.stderr)
            raise
