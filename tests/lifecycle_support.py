"""Capture actual CLI/artifacts and normalize only volatile transport metadata."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import re
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from storyloop.changes import load_change
from storyloop.cli import main
from storyloop.project_loop import load_run_state
from tests.lifecycle_cases import execute_case
from tests.test_baseline_behavior import git

SHA = re.compile(r"\b[0-9a-f]{40}(?:[0-9a-f]{24})?\b")
TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)")
DURATIONS = frozenset({"loop_ms", "elapsed_ms", "duration_ms", "wall_ms", "agent_ms",
                       "elapsed_seconds", "duration_seconds"})
CALL = re.compile(r"\b\d{8}T\d{6}-[a-z_-]+-[0-9a-f]{6}\b")
CHANGE_ID = re.compile(r"\b(FEAT|FIX|REF|BASE)-\d{8}-\d{3}\b")


def candidate_roots(value):
    """Use recorded execution roots, never erase arbitrary application path fragments."""
    roots = []
    if isinstance(value, dict):
        for key, item in sorted(value.items()):
            if key in {"cwd", "execution_cwd"} and isinstance(item, str):
                if Path(item).name.startswith(("storyloop-smoke-", "storyloop-regression-",
                                               "storyloop-baseline-", "storyloop-system-smoke-",
                                               "storyloop-characterization-")):
                    roots.append(item)
            roots.extend(candidate_roots(item))
    elif isinstance(value, list):
        for item in value:
            roots.extend(candidate_roots(item))
    return roots


def normalize_measurements(value):
    value = re.sub(r"(\b(?:OK|FAIL|FAILED)\s+)[\d.]+s\b", r"\1<DURATION>s", value)
    value = re.sub(r"(Ran \d+ tests? in )[\d.]+s\b", r"\1<DURATION>s", value)
    value = re.sub(r"(代理耗时 )[\d.]+( 分钟)", r"\1<DURATION>\2", value)
    return re.sub(r"((?:loop_ms|agent_ms|wall_ms|duration_ms)[:：]\s*)\d+", r"\1<DURATION>", value)


def normalize_case(value, root):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    tokens = {}
    for match in SHA.finditer(text):
        tokens.setdefault(match[0], f"<DIGEST-{len(tokens) + 1}>")
    metadata = {}
    for match in CALL.finditer(text):
        metadata.setdefault(match[0], f"<CALL-{len(metadata) + 1}>")
    for match in CHANGE_ID.finditer(text):
        token = metadata.setdefault(match[0], f"<{match[1]}-{sum(k.startswith(match[1] + '-') for k in metadata) + 1}>")
        metadata[match[0].lower()] = token.lower()
    for candidate in candidate_roots(value):
        if candidate not in metadata:
            metadata[candidate] = f"<CANDIDATE-{sum(v.startswith('<CANDIDATE-') for v in metadata.values()) + 1}>"
    def normalize(item, key=""):
        if isinstance(item, dict):
            return {k: normalize(v, k) for k, v in sorted(item.items())}
        if isinstance(item, list):
            return [normalize(v) for v in item]
        if key in DURATIONS and isinstance(item, (int, float)):
            return "<DURATION>"
        if not isinstance(item, str):
            return item
        item = item.replace(str(root), "<PROJECT>").replace(sys.executable, "<PYTHON>")
        item = TIMESTAMP.sub("<TIME>", item)
        item = normalize_measurements(item)
        for source, token in metadata.items():
            item = item.replace(source, token)
        for digest, token in tokens.items():
            item = item.replace(digest, token)
        return item
    return normalize(value)


def cli_output(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(args)
        except SystemExit as exc:
            code = exc.code
    return {"stdout": out.getvalue(), "stderr": err.getvalue(), "exitcode": code}


def capture_case(name):
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        calls = []
        with patch("storyloop.project_agents._codex", side_effect=AssertionError("Real CLI forbidden")):
            identifier, results = execute_case(root, name, calls)
            state = load_run_state(root, identifier)
            value = {"results": results, "state": state, "change": load_change(root, identifier),
                     "calls": calls, "artifacts": artifacts(root, state), "git": topology(root)}
            commands = {"help": ["--help"], "log": ["log", str(root), "--change", identifier],
                        "report": ["report", str(root), "--change", identifier],
                        "reset_invalid": ["reset", str(root), "--change", identifier, "--reason", "invalid"]}
            if state["status"] == "PASSED":
                commands["run"] = ["run", str(root), "--change", identifier]
            value["cli"] = {key: cli_output(args) for key, args in commands.items()}
        return normalize_case(value, root)


def artifacts(root, state):
    report = state.get("report")
    if not report:
        return {}
    path = Path(report)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == state["report_sha256"]
    evidence = root / ".storyloop/evidence" / state["change_id"] / "acceptance-result.json"
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == state["acceptance_evidence_sha256"]
    return {"json": json.loads(path.read_text(encoding="utf-8")),
            "markdown": path.with_suffix(".md").read_text(encoding="utf-8")}


def topology(root):
    lines = git(root, "log", "--format=%H %P %s").splitlines()
    merges = [line for line in lines if len(line.split(" ", 3)) > 3 and
              re.fullmatch(r"[0-9a-f]{40}", line.split()[2])]
    return {"commits": lines, "merges": merges, "branch": git(root, "branch", "--show-current")}
