"""How many tests really ran: parsed from the runners' own summaries.

An oracle must prove it ran. A runner can exit 0 having executed nothing (a discovery rule that
stopped matching, an incompatible test platform), which looks green. Counting executed tests and
comparing them with the old code's baseline turns that silent hole into a failure. Output that no
parser recognizes stays uncounted (None) and is reported as such, never treated as zero.
"""

from __future__ import annotations

from pathlib import Path
import re

from .models import StoryLoopError, require  # noqa: F401  # 兼容再导出：保留旧模块导入路径
from .state_format import validate_test_count_patterns as validate_patterns  # noqa: F401  # 兼容再导出：保留旧模块导入路径


def _last(pattern: str, text: str):
    matches = list(re.finditer(pattern, text, re.M))
    return matches[-1] if matches else None


def _words(fragment: str) -> dict[str, int]:
    return {word: int(count) for count, word in re.findall(r"(\d+) (\w+)", fragment)}


def _unittest(text: str) -> dict | None:
    ran = _last(r"^Ran (\d+) tests? in ", text)
    if not ran:
        return None
    tail = _last(r"^(?:OK|FAILED)\b(.*)$", text)
    extra = tail.group(1) if tail else ""
    failed = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", extra))
    skipped = re.search(r"skipped=(\d+)", extra)
    skip = int(skipped.group(1)) if skipped else 0
    return {"executed": int(ran.group(1)) - skip, "failed": failed, "skipped": skip}


def _pytest(text: str) -> dict | None:
    line = _last(r"^=+ (.*?) in [\d.]+s(?: \([^)]*\))? =+$", text)
    if not line:
        return None
    words = _words(line.group(1))
    failed = words.get("failed", 0) + words.get("error", 0) + words.get("errors", 0)
    executed = words.get("passed", 0) + failed + words.get("xfailed", 0) + words.get("xpassed", 0)
    return {"executed": executed, "failed": failed, "skipped": words.get("skipped", 0)}


MAVEN_LINE = r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)(.*)$"


def _maven(text: str) -> dict | None:
    """Sum the per-module summaries of a reactor build (lines without "Time elapsed"); a build that
    printed only per-class lines is summed over those instead. Taking just the last line would count
    only the last module, so a green multi-module build could read as zero executed."""
    lines = [match.groups() for match in re.finditer(MAVEN_LINE, text, re.M)]
    if not lines:
        return None
    summaries = [line for line in lines if "Time elapsed" not in line[4]]
    chosen = summaries or lines
    run, failures, errors, skipped = (sum(int(line[i]) for line in chosen) for i in range(4))
    return {"executed": run - skipped, "failed": failures + errors, "skipped": skipped}


def _gradle(text: str) -> dict | None:
    line = _last(r"(\d+) tests? completed(?:, (\d+) failed)?(?:, (\d+) skipped)?", text)
    if not line:
        return None
    total, failed, skipped = (int(group or 0) for group in line.groups())
    return {"executed": total - skipped, "failed": failed, "skipped": skipped}


def _jest(text: str) -> dict | None:
    line = _last(r"^Tests:\s+(.*?)(\d+) total", text)
    if not line:
        return None
    words, total = _words(line.group(1)), int(line.group(2))
    skipped = words.get("skipped", 0) + words.get("todo", 0)
    return {"executed": total - skipped, "failed": words.get("failed", 0), "skipped": skipped}


def _vitest(text: str) -> dict | None:
    line = _last(r"^\s*Tests\s+(.*?)\((\d+)\)\s*$", text)
    if not line:
        return None
    words, total = _words(line.group(1)), int(line.group(2))
    skipped = words.get("skipped", 0) + words.get("todo", 0)
    return {"executed": total - skipped, "failed": words.get("failed", 0), "skipped": skipped}


def _go(text: str) -> dict | None:
    marks = re.findall(r"^\s*--- (PASS|FAIL|SKIP):", text, re.M)
    if not marks:
        return None
    return {"executed": marks.count("PASS") + marks.count("FAIL"), "failed": marks.count("FAIL"),
            "skipped": marks.count("SKIP")}


PARSERS = (("maven", _maven), ("gradle", _gradle), ("pytest", _pytest), ("unittest", _unittest),
           ("jest", _jest), ("vitest", _vitest), ("go", _go))
UNCOUNTED = {"executed": None, "failed": None, "skipped": None, "parser": None}


def count_tests(command: list[str], output: str, patterns: list[dict] | None = None) -> dict:
    """Executed/failed/skipped from a runner's full output; a project pattern wins for its command."""
    text = output.replace("\r\n", "\n")
    joined = " ".join(command)
    for item in patterns or []:
        if item["match"] in joined:
            match = _last(item["executed"], text)
            if match:
                return {"executed": int(match.group("executed")), "failed": None, "skipped": None,
                        "parser": "custom"}
    found = [(name, result) for name, parse in PARSERS if (result := parse(text)) is not None]
    if not found:
        return dict(UNCOUNTED)
    name, best = max(found, key=lambda pair: pair[1]["executed"])
    return {**best, "parser": name}


def project_patterns(project: Path) -> list[dict]:
    """The configured patterns, or none when the project has no config (e.g. inside a candidate)."""
    from .baseline import CONFIG_PATH, load_project_config
    if not (Path(project) / CONFIG_PATH).is_file():
        return []
    profile = load_project_config(project).get("profile", {})
    return list(profile.get("test_count_patterns") or [])


COUNTED_LAYERS = ("unit", "integration", "acceptance", "smoke")
BASELINE_LAYERS = ("unit", "integration")


def executed_list(layer: dict) -> list:
    return [(item.get("counts") or {}).get("executed") for item in layer.get("results") or []]


def removal_paths(product: dict) -> list[str]:
    return [item["path"] for item in product.get("test_removals") or []]


def sample_layer(project: Path, config: dict, commit: str, layer: str, removals: list[str],
                 run_at_commit) -> list:
    """Executed counts of one layer on a commit's tree, without the tests the contract allows to delete.

    The tree comes from the commit, never from the workspace, so implementation edits (including
    deleted tests) cannot lower it; excluding the declared removals makes it the floor the
    implementation must still reach, so a declaration excuses exactly those files and nothing more.
    """
    from .project_profile import layer_commands
    commands = layer_commands(config["profile"], layer)
    if not commands:
        return []
    return executed_list(run_at_commit(project, commit, commands, config["check_timeout_seconds"],
                                       exclude=removals))


def sample_baseline(project: Path, config: dict, commit: str, removals: list[str], run_at_commit) -> dict:
    """The unit and integration floors on the code before implementation."""
    return {name: sample_layer(project, config, commit, name, removals, run_at_commit)
            for name in BASELINE_LAYERS}


def ensure_baseline(project: Path, state: dict, product: dict, run_at_commit) -> None:
    """Sample the floors once, on the contract commit; idempotent for every path back to IMPLEMENTING."""
    if "oracle" not in state:
        from .baseline import load_project_config
        state["oracle"] = {"baseline": sample_baseline(project, load_project_config(project),
                                                       state["contract_commit"], removal_paths(product),
                                                       run_at_commit)}


def check_layers(layers: dict, oracle: dict | None) -> dict:
    """Fail a green layer that executed nothing, or fewer tests than its baseline floor."""
    baseline = (oracle or {}).get("baseline") or {}
    checked = dict(layers)
    for name in COUNTED_LAYERS:
        if name in layers:
            checked[name] = _check_layer(layers[name], baseline.get(name))
    return checked


def _check_layer(layer: dict, baseline: list | None) -> dict:
    results = layer.get("results") or []
    uncounted = [item["id"] for item in results if (item.get("counts") or {}).get("executed") is None]
    out = {**layer, "uncounted": uncounted}
    if layer.get("status") != "PASSED":
        return out
    problems = []
    for index, item in enumerate(results):
        executed = (item.get("counts") or {}).get("executed")
        if executed is None:
            continue
        if executed == 0:
            problems.append(f"{item['id']}: 执行数为 0")
        before = baseline[index] if baseline and index < len(baseline) else None
        if before is not None and executed < before:
            problems.append(f"{item['id']}: 执行数从 {before} 降到 {executed}")
    if problems:
        out.update({"status": "FAILED", "oracle_problems": problems})
    return out
