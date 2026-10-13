"""How many tests really ran: parsed from the runners' own summaries.

An oracle must prove it ran. A runner can exit 0 having executed nothing (a discovery rule that
stopped matching, an incompatible test platform), which looks green. Counting executed tests and
comparing them with the old code's baseline turns that silent hole into a failure. Output that no
parser recognizes stays uncounted (None) and is reported as such, never treated as zero.
"""

from __future__ import annotations

from pathlib import Path
import re

from .models import StoryLoopError, require


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


def _maven(text: str) -> dict | None:
    line = _last(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)", text)
    if not line:
        return None
    run, failures, errors, skipped = (int(group) for group in line.groups())
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


def validate_patterns(value: object) -> list[dict]:
    """settings `test_count_patterns`: [{match, executed}] where `executed` has a named group."""
    require(isinstance(value, list), "test_count_patterns must be a list")
    result = []
    for item in value:
        require(isinstance(item, dict) and set(item) == {"match", "executed"},
                "Each test count pattern needs exactly match and executed")
        require(isinstance(item["match"], str) and bool(item["match"]), "pattern match must be nonempty text")
        try:
            compiled = re.compile(item["executed"], re.M)
        except (TypeError, re.error) as exc:
            raise StoryLoopError(f"Invalid test count pattern: {exc}") from None
        require("executed" in compiled.groupindex, "A test count pattern needs a named group 'executed'")
        result.append({"match": item["match"], "executed": item["executed"]})
    return result


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


def sample_baseline(project: Path, config: dict) -> dict:
    """Executed counts of the unit and integration commands on the code before implementation."""
    from .project_profile import layer_commands
    from .smoke_runner import run_commands_isolated
    profile, timeout = config["profile"], config["check_timeout_seconds"]
    baseline = {}
    for name in BASELINE_LAYERS:
        commands = layer_commands(profile, name)
        baseline[name] = executed_list(run_commands_isolated(project, commands, timeout)) if commands else []
    return baseline


def removals_explained(project: Path, product: dict) -> bool:
    """A drop in executed tests is explained only when every declared removal really happened."""
    removals = product.get("test_removals") or []
    return bool(removals) and all(not (Path(project) / item["path"]).exists() for item in removals)


def check_layers(layers: dict, oracle: dict | None, explained: bool) -> dict:
    """Fail a green layer that executed nothing, or fewer tests than its baseline without explanation."""
    baseline = (oracle or {}).get("baseline") or {}
    checked = dict(layers)
    for name in COUNTED_LAYERS:
        if name in layers:
            checked[name] = _check_layer(layers[name], baseline.get(name), explained)
    return checked


def _check_layer(layer: dict, baseline: list | None, explained: bool) -> dict:
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
        if before is not None and executed < before and not explained:
            problems.append(f"{item['id']}: 执行数从 {before} 降到 {executed}")
    if problems:
        out.update({"status": "FAILED", "oracle_problems": problems})
    return out
