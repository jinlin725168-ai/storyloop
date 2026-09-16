"""Deterministic demo command adapter; it never creates a verdict."""

import json
from pathlib import Path
import sys


request = json.load(sys.stdin)
if request["role"] == "implement":
    target = Path("service/business/result.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("demo-green\n", encoding="utf-8")
response = {
    "protocol": "loop.agent.v2",
    "request_id": request["request_id"],
    "status": "ok",
    "summary": f"{sys.argv[1]} completed bounded work",
    "questions": [],
}
print(json.dumps(response))
