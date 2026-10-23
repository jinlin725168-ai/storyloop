"""Capture actual CLI/artifacts and normalize only volatile transport metadata."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import re
from pathlib import Path, PureWindowsPath
import shlex
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

from storyloop.changes import load_change
from storyloop.cli import main
from storyloop.project_loop import load_run_state
from tests.lifecycle_cases import execute_case, fixture_git_policy
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
                path = PureWindowsPath(item) if re.match(r"^[A-Za-z]:[\\/]", item) else Path(item)
                if path.name.startswith(("storyloop-smoke-", "storyloop-regression-",
                                               "storyloop-baseline-", "storyloop-system-smoke-",
                                               "storyloop-characterization-")):
                    roots.append(item)
            roots.extend(candidate_roots(item))
    elif isinstance(value, list):
        for item in value:
            roots.extend(candidate_roots(item))
    return roots


@contextmanager
def fixture_environment():
    """Keep lifecycle captures independent of optional CLI and host Git configuration."""
    from storyloop import smoke_runner
    original_which, original_run = shutil.which, subprocess.run
    original_process = smoke_runner.run_process
    executable = "storyloop-fixture-openspec"
    def which(name, *args, **kwargs):
        return executable if name == "openspec" else original_which(name, *args, **kwargs)
    def run(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and command[0] == executable:
            assert command[1:-2] in (["validate"], ["spec", "validate"])
            assert command[-1] == "--json"
            payload = {"items": [{"id": command[-2], "valid": True}]}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        return original_run(command, *args, **kwargs)
    def process(command, *args, **kwargs):
        result = original_process(command, *args, **kwargs)
        if Path(command[0]) == Path(sys.executable):
            # Canonicalize fixture transport before Markdown turns CR into visible spaces.
            result = {**result, **{key: result[key].replace("\r\n", "\n")
                                   for key in ("stdout", "stderr")}}
        return result
    with patch.dict("os.environ", {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "init.defaultBranch",
                                  "GIT_CONFIG_VALUE_0": "master"}), fixture_git_policy(), \
            patch("shutil.which", side_effect=which), patch("subprocess.run", side_effect=run), \
            patch.object(smoke_runner, "run_process", side_effect=process):
        yield


def replace_recorded_path(value, path, token):
    aliases = {str(path), path.as_posix()}
    if isinstance(path, PureWindowsPath):
        aliases.add(str(path).replace("\\", "\\\\"))
    variants = set()
    for alias in aliases:
        quoted = shlex.quote(alias)
        variants.update((alias, quoted))
    for alias in sorted(variants, key=len, reverse=True):
        value = value.replace(alias, token)
    return value


def canonical_windows_paths(value):
    tagged = r"(<PROJECT>|<CANDIDATE-\d+>|<PYTHON>)([^\"'`\r\n|<>:]*?)(?=\s--|[\"'`\r\n|<>:]|$)"
    value = re.sub(tagged, lambda m: m[1] + m[2].replace("\\\\", "/").replace("\\", "/"), value)
    value = re.sub(r"'((?:<PROJECT>|<CANDIDATE-\d+>|<PYTHON>)(?:/[^'\n]*)?)'", r"\1", value)
    return value.replace("\r\n", "\n")


def normalize_json_transport(value, normalize):
    """Rewrite decoded JSON string values while preserving key order and all non-string bytes."""
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    def replace(match):
        original = json.loads(match[0])
        transformed = normalize(original)
        return match[0] if transformed == original else json.dumps(transformed, ensure_ascii="\\u" in match[0])
    return re.sub(r'"(?:[^"\\]|\\.)*"', replace, value)


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
        decoded = normalize_json_transport(item, normalize)
        if decoded is not None:
            return decoded
        item = replace_recorded_path(item, root, "<PROJECT>")
        item = replace_recorded_path(item, Path(sys.executable), "<PYTHON>")
        item = TIMESTAMP.sub("<TIME>", item)
        item = normalize_measurements(item)
        for source, token in metadata.items():
            if token.startswith("<CANDIDATE-"):
                path = PureWindowsPath(source) if re.match(r"^[A-Za-z]:[\\/]", source) else Path(source)
                item = replace_recorded_path(item, path, token)
        if isinstance(root, PureWindowsPath):
            item = canonical_windows_paths(item)
        for source, token in metadata.items():
            if not token.startswith("<CANDIDATE-"):
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
        with fixture_environment(), patch("storyloop.project_agents._codex", side_effect=AssertionError("Real CLI forbidden")):
            identifier, results = execute_case(root, name, calls)
            state = load_run_state(root, identifier)
            verify_active_bindings(root, state)
            value = {"results": results, "state": state, "change": load_change(root, identifier),
                     "calls": calls, "artifacts": artifacts(root, state), "git": topology(root)}
            commands = {"help": ["--help"], "log": ["log", str(root), "--change", identifier],
                        "report": ["report", str(root), "--change", identifier],
                        "reset_invalid": ["reset", str(root), "--change", identifier, "--reason", "invalid"]}
            if state["status"] == "PASSED":
                commands["run"] = ["run", str(root), "--change", identifier]
            value["cli"] = {key: cli_output(args) for key, args in commands.items()}
        return normalize_case(value, root)


def verify_active_bindings(root, state):
    """Check current authoritative files before digest tokenization; historical evidence is not rewritten."""
    from storyloop.changes import contract_digest
    from storyloop.smoke import smoke_digests
    change = load_change(root, state["change_id"])
    assert state["contract_digest"] == change["approval"]["contract_digest"] == contract_digest(change["product"])
    red_path = root / ".storyloop/evidence" / state["change_id"] / "acceptance-red.json"
    red = json.loads(red_path.read_text(encoding="utf-8"))
    assert state["red_evidence_sha256"] == hashlib.sha256(red_path.read_bytes()).hexdigest()
    assert red["head_commit"] == state["contract_commit"]
    assert red["suite_digests"] == smoke_digests(root, state["change_id"])


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
