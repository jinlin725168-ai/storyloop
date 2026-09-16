"""Read-only demo Judge implementing the real loop.judge.v2 protocol."""

import json
from pathlib import Path
import sys


request = json.load(sys.stdin)
implemented = Path("service/business/result.txt").is_file()
status = "fail" if request["phase"] == "red" and not implemented else "pass"
response = {
    "protocol": "loop.judge.v2",
    "request_id": request["request_id"],
    "stage": request["stage"],
    "judge": request["judge"],
    "phase": request["phase"],
    "status": status,
    "summary": "expected red" if status == "fail" else "verified green",
    "findings": ["implementation is absent"] if status == "fail" else [],
}
print(json.dumps(response))
raise SystemExit(1 if status == "fail" else 0)
