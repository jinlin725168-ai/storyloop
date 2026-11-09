"""Persist bounded follow-up for repeated oracle-strength review findings."""

from copy import deepcopy

from .models import identifier, require, text
from .role_schemas import validate_finding

SPEC_FIELDS = ("inputs", "preconditions", "entry", "expected", "negative_boundary",
               "contract_plan")
def review_request(state: dict, binding: dict | None = None) -> dict:
    """Describe full-spec follow-up needed on the next complete review."""
    stored = state.get("review_convergence") or {}
    if binding and any(stored.get("binding", {}).get(key) != binding.get(key)
                       for key in ("contract", "plan")):
        return {}
    criteria = stored.get("criteria") or {}
    return {criterion: {"round": row["round"], "finding_ids": row["finding_ids"],
                        "prior_findings": row["prior_findings"][-2:],
                        "required_spec_fields": list(SPEC_FIELDS)}
            for criterion, row in criteria.items() if row["phase"] == "request_spec"}


def repair_allowed(state: dict) -> bool:
    """A complete-spec follow-up grants one directed acceptance repair."""
    criteria = (state.get("review_convergence") or {}).get("criteria") or {}
    return not any(row["phase"] == "repair_once" and row.get("repair_used") is True
                   for row in criteria.values())


def record_repair(state: dict) -> None:
    criteria = (state.get("review_convergence") or {}).get("criteria") or {}
    for row in criteria.values():
        if row["phase"] == "repair_once":
            row["repair_used"] = True


def reset_binding(state: dict, reason: str) -> None:
    """Retire consecutive rounds when an approved contract or plan binding is void."""
    store = state.get("review_convergence")
    if not store or not store.get("criteria"):
        return
    store["resets"].append({"reason": text(reason, "convergence reset reason"),
                            "before": deepcopy(store.get("binding") or {})})
    store["criteria"] = {}
    store["last_review"] = None


def _identity(state: dict, findings: list[dict]) -> None:
    for item in findings:
        validate_finding(item)
    structured = [item for item in findings if item.get("disposition") is not None]
    if not structured:
        return
    ids = state.setdefault("finding_ids", {"by_root": {}, "by_id": {}})
    in_review = set()
    for item in structured:
        finding_id = identifier(item["id"], "review finding id")
        require(finding_id not in in_review, "Duplicate review finding id")
        in_review.add(finding_id)
        location = str(item.get("location", ""))
        file, separator, line = location.rpartition(":")
        location = file if separator and line.isdigit() else location
        root = "\0".join((str(item.get("criterion_id", "")),
                          str(item.get("target", "")), location,
                          " ".join(str(item.get("required_change", "")).split())))
        require(ids["by_root"].get(root, finding_id) == finding_id,
                "Same review finding root changed id")
        require(ids["by_id"].get(finding_id, root) == root,
                "Review finding id reused for another root")
        ids["by_root"][root] = finding_id
        ids["by_id"][finding_id] = root


def remember_ids(state: dict, findings: list[dict]) -> None:
    """Keep root-to-ID mapping across consistency and final review calls."""
    _identity(state, findings)


def _missing_spec(item: dict, approved_entries: dict[str, set[str]] | None = None) -> list[str]:
    spec = item.get("criterion_spec")
    if not isinstance(spec, dict):
        return list(SPEC_FIELDS)
    missing = [field for field in SPEC_FIELDS
               if not isinstance(spec.get(field), str) or not spec[field].strip()]
    allowed = approved_entries.get(item.get("criterion_id"), set()) if approved_entries else None
    if allowed is not None and "entry" not in missing and spec["entry"].split()[0] not in allowed:
        missing.append("entry")
    return missing


def _exhausted(criterion: str, row: dict) -> dict:
    return {"criterion_id": criterion, "round": row["round"],
            "finding_ids": list(row["finding_ids"]),
            "missing_spec_fields": list(row.get("missing_spec_fields", [])),
            "prior_findings": deepcopy(row["prior_findings"][:2]), "exhausted": True}


def _prepare(state: dict, binding: dict) -> dict:
    store = state.setdefault("review_convergence", {"binding": {}, "criteria": {},
                                                     "last_review": None, "resets": []})
    old = store["binding"]
    for key in ("contract", "plan"):
        if old and old.get(key) != binding[key]:
            store["resets"].append({"reason": f"{key}_binding_changed",
                                    "before": old[key], "after": binding[key]})
            store["criteria"] = {}
            store["last_review"] = None
            break
    store["binding"] = {key: binding[key] for key in ("contract", "plan")}
    return store


def _blocking(findings: list[dict], criteria: dict) -> tuple[dict, dict]:
    blocked, reclassified = {}, {}
    for item in findings:
        criterion = item.get("criterion_id")
        # Normalize oracle MUST_FIX to the AC's criterion-strength weakness;
        # changing prose or finding ID cannot restart the same blocked round.
        if (item.get("target") == "oracle" and item.get("disposition") == "MUST_FIX"
                and item.get("blocking") is True):
            blocked.setdefault(criterion, []).append(item)
        row = criteria.get(criterion)
        if (row and row["phase"] in {"request_spec", "repair_once"}
                and item.get("target") in {"contract", "design"}
                and item.get("blocking") is True):
            reclassified[criterion] = item
    return blocked, reclassified


def _advance_row(row: dict, items: list[dict], approved_entries: dict[str, set[str]] | None) -> None:
    if row["phase"] == "exhausted":
        return
    row["round"] += 1
    for item in items:
        if item["id"] not in row["finding_ids"]:
            row["finding_ids"].append(item["id"])
    first = items[0]
    row["prior_findings"].append({"id": first["id"], "evidence": first["evidence"],
                                  "message": first["message"]})
    if row["phase"] == "repair_once":
        row["phase"] = "exhausted"
        row["missing_spec_fields"] = []
    elif row["phase"] == "request_spec":
        row["missing_spec_fields"] = min(
            (_missing_spec(item, approved_entries) for item in items), key=len)
        row["phase"] = "exhausted" if row["missing_spec_fields"] else "repair_once"
    elif row["round"] == 2:
        row["phase"] = "request_spec"


def advance(state: dict, review: dict, binding: dict,
            *, approved_entries: dict[str, set[str]] | None = None) -> dict | None:
    """Count each complete review once; stop after a missing spec or one failed repair."""
    require(all(isinstance(binding.get(key), str) for key in ("contract", "plan", "review")),
            "Invalid convergence binding")
    findings = review.get("findings") or []
    if not state.get("review_convergence") and not any(
            item.get("disposition") is not None for item in findings):
        _identity(state, findings)
        return None
    store = _prepare(state, binding)
    if store["last_review"] == binding["review"]:
        return next((_exhausted(key, row) for key, row in store["criteria"].items()
                     if row["phase"] == "exhausted"), None)
    _identity(state, findings)
    blocked, reclassified = _blocking(findings, store["criteria"])
    for criterion in set(store["criteria"]) - set(blocked) - set(reclassified):
        del store["criteria"][criterion]
    for criterion, item in reclassified.items():
        row = store["criteria"][criterion]
        row["round"] += 1
        row["phase"] = "exhausted"
        row["missing_spec_fields"] = []
        if item.get("id") and item["id"] not in row["finding_ids"]:
            row["finding_ids"].append(item["id"])
    for criterion, items in blocked.items():
        row = store["criteria"].setdefault(criterion, {"round": 0, "phase": "counting",
                                                         "finding_ids": [], "prior_findings": []})
        _advance_row(row, items, approved_entries)
    store["last_review"] = binding["review"]
    return next((_exhausted(key, row) for key, row in store["criteria"].items()
                 if row["phase"] == "exhausted"), None)
