"""Bounded, deterministic RED defect evidence and entry-use checks."""

import re

from .processutil import redact


def red_fixture_category(rows: list[dict]) -> str:
    for row in rows:
        messages = [str(row.get("stdout") or ""), str(row.get("stderr") or "")]
        messages.extend(str(item.get("message") or "")
                        for item in (row.get("counts") or {}).get("diagnostics", [])
                        if isinstance(item, dict))
        if any(any(signal in message for signal in
                   ("outside declared roots", "source_files", "ModuleNotFoundError:"))
               for message in messages):
            return "MANIFEST_OR_SOURCE_BOUNDARY"
    return "CASE_OR_FIXTURE_ERROR"


def red_defect_context(manifest: dict, change: dict, rows: list[dict], category: str) -> dict:
    ids = {row["id"] for row in rows}
    references = {entry["id"]: entry for story in change.get("product", {}).get("stories", [])
                  for entry in story.get("verification_entries", [])}
    entry_ids = {entry_id for test in manifest["tests"] if test["id"] in ids
                 for entry_id in test.get("entry_ids", [])}
    diagnostics = []
    for row in rows:
        found = [diag for diag in (row.get("counts") or {}).get("diagnostics", [])
                 if isinstance(diag, dict)]
        diagnostics.extend(found or [{
            "case_id": row["id"], "kind": "fail" if row["status"] == "FAIL" else "error",
            "message": redact(f"{category}: {row['status']}")[:2000],
            "traceback_tail": redact(str(row.get("stderr") or row.get("stdout") or ""))[-4000:],
        }])
    return {"defect_category": category, "test_ids": sorted(ids),
            "entries": [references[key] for key in sorted(entry_ids) if key in references],
            "diagnostics": diagnostics[:20], "blockers": _blockers(manifest, rows, category)}


def _blockers(manifest: dict, rows: list[dict], category: str) -> list[dict]:
    tests = {item["id"]: item for item in manifest["tests"]}
    found = []
    for row in rows:
        test = tests.get(row["id"], {})
        criteria = row.get("criteria") or test.get("criteria") or [""]
        paths = test.get("source_files") or [""]
        diagnostics = [item for item in (row.get("counts") or {}).get("diagnostics", [])
                       if isinstance(item, dict)]
        mechanisms = _mechanisms(row, diagnostics, category)
        for criterion in criteria:
            for path in paths:
                for mechanism in mechanisms:
                    found.append({"criterion_id": criterion, "path": path,
                                  "mechanism": mechanism, "test_id": row["id"]})
                    if len(found) == 20:
                        return found
    return found


def _mechanisms(row: dict, diagnostics: list[dict], category: str) -> list[str]:
    messages = [str(item.get("message") or "") for item in diagnostics]
    specific = [item for item in messages if item and item != f"{category}: {row['status']}"]
    if specific:
        return [redact(item)[:500] for item in specific]
    output = str(row.get("stderr") or row.get("stdout") or "")
    exceptions = re.findall(r"(?m)^([A-Za-z_]\w*(?:Error|Exception):[^\n]*)$", output)
    return [redact(item)[:500] for item in exceptions[-3:]] or [category]


def red_failure_classification(judged: list[dict]) -> str | None:
    """Classify broken RED checks by concrete execution signals, not ERROR status alone."""
    broken = [item for item in judged if item["status"] == "ERROR" or (
        item["status"] == "FAIL" and (item.get("counts") or {}).get("executed") == 0)]
    if not broken:
        return None
    for item in broken:
        diagnostics = (item.get("counts") or {}).get("diagnostics") or []
        isolation = any(isinstance(diag, dict) and str(diag.get("message", "")).startswith(
            "ImportIsolationError:") for diag in diagnostics)
        if (item.get("timed_out") or item.get("truncated") or item.get("process_error")
                or item.get("returncode") is None or isolation):
            return "environment"
    return "oracle"
