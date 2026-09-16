"""Scripted adapter for the packaged demo only; this is not an AI agent."""
import importlib.util
import json
from pathlib import Path
import sys

IMPLEMENTATION = '''def can_book(existing, start, end):
    """Half-open intervals permit adjacent reservations."""
    if end <= start:
        return False
    return all(end <= booked_start or start >= booked_end
               for booked_start, booked_end in existing)
'''


def review(workspace: Path) -> dict:
    spec = importlib.util.spec_from_file_location("candidate_booking", workspace / "booking.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    probes = [("AC-1", [(600, 660)], 590, 700, False),
              ("AC-1", [(600, 660)], 600, 660, False),
              ("AC-2", [(600, 660)], 540, 600, True),
              ("AC-3", [], 10, 9, False)]
    findings = []
    for criterion, existing, start, end, expected in probes:
        if module.can_book(existing, start, end) is not expected:
            findings.append({"criterion_id": criterion, "message": "Independent boundary probe failed"})
    return {"verdict": "fail" if findings else "pass", "summary": "Scripted independent boundary probes completed; manual product acceptance remains pending",
            "findings": findings, "questions": []}


def main() -> None:
    request = json.load(sys.stdin)
    workspace = Path(request["workspace"])
    if request["role"] == "implement":
        (workspace / "booking.py").write_text(IMPLEMENTATION, encoding="utf-8")
        result = {"summary": "Scripted demo implemented half-open interval checks", "questions": []}
    elif request["role"] == "review":
        result = review(workspace)
    else:
        raise ValueError("The scripted demo supports implementation and review only")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
