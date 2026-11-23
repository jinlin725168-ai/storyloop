"""S arm on a sqlite-utils sample: StoryLoop per the quickstart, frozen human rules, hidden Gold.
  run_s.py <name>
Human rules (same for every sample): accept readiness gaps; answer drafts "recommended + confirm
assumptions" (at most 3 rounds); after approve, one correction like the bench's simulated human."""
import json, subprocess, sys, time
from pathlib import Path
X = Path(__file__).parent
VENV = Path("/home/jinlin/work/l3/venv-xp/bin")
name = sys.argv[1]
sample = json.loads((X / "samples.json").read_text(encoding="utf-8"))[name]
out = X / "results" / f"S-{name}"; out.mkdir(parents=True, exist_ok=True)
tree = out / "tree"
env = {"PATH": f"{VENV}:/usr/bin:/bin:/home/jinlin/.local/bin", "HOME": "/home/jinlin", "LANG": "C.UTF-8"}
log = []


def sl(*args, timeout=10800):
    run = subprocess.run([str(VENV / "storyloop"), *args, "--json"], cwd="/tmp", capture_output=True, text=True,
                         env=env, timeout=timeout)
    text = run.stdout.strip() or run.stderr.strip()
    try:
        value = json.loads(text[text.index("{"):]) if "{" in text else {"status": "NO_OUTPUT"}
    except ValueError:
        value = {"status": "UNPARSED", "raw": text[-2000:]}
    log.append({"argv": list(args), "status": value.get("status"), "next": value.get("next")})
    (out / "log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return value


started = time.monotonic()
subprocess.run([str(X / "fresh_repo.sh"), sample["source"], sample["solution"] + "^", str(tree)], check=True)
project = str(tree)
init = sl("init", project, "--detect", "--yes", "--agent", "claude")
if init.get("readiness") == "READINESS_APPROVAL_REQUIRED":
    sl("approve", project, "--readiness", init["readiness_digest"], "--reason", "实验：项目没有静态检查与系统冒烟层")
draft = sl("change", project, "--idea", sample["requirement"], "--no-interactive")
change_id = draft.get("change_id")
for _ in range(3):
    state = json.loads((tree / ".storyloop/state/changes" / f"{change_id}.json").read_text(encoding="utf-8"))
    checklist = (tree / ".storyloop/drafts" / change_id / "checklist.md").read_text(encoding="utf-8")
    if not state["product"].get("questions") and "**ERROR**" not in checklist:
        break
    sl("change", project, "--change", change_id, "--message", "所有待决问题选推荐项；所有假设确认", "--no-interactive")
result = sl("approve", project, "--change", change_id, "--no-interactive")
corrections = {"ORACLE_DEFECT": ["acceptance-repair", project, "--change", change_id, "--reason",
                                 "bench simulated human: apply engine stop evidence"],
               "EXISTING_GREEN": ["acceptance-repair", project, "--change", change_id, "--reason",
                                  "bench simulated human: apply engine stop evidence"],
               "DESIGN_REVISION_REQUIRED": ["run", project, "--change", change_id, "--replan", "--reason",
                                            "bench simulated human: apply engine stop evidence"],
               "NO_PROGRESS": ["run", project, "--change", change_id, "--replan", "--reason",
                               "bench simulated human: apply engine stop evidence"]}
if result.get("status") in corrections:
    sl(*corrections[result["status"]])
    result = sl("run", project, "--change", change_id, "--no-interactive")
wall = round(time.monotonic() - started)
status = result.get("status")
gold = json.loads(subprocess.run([str(VENV / "python"), str(X / "gold_sqlite.py"), name, project, str(VENV / "python")],
                                 capture_output=True, text=True).stdout)
ledger = json.loads((tree / ".storyloop/state/telemetry" / f"{change_id}.json").read_text(encoding="utf-8"))
cost = round(sum(e.get("cost_usd") or 0 for e in ledger.get("entries", [])), 2)
touches = (tree / ".storyloop/state/touches" / f"{change_id}.jsonl")
summary = {"arm": "S", "name": name, "change_id": change_id, "status": status, "wall_seconds": wall,
           "cost_usd": cost, "calls": len(ledger.get("entries", [])),
           "touches": len(touches.read_text(encoding="utf-8").splitlines()) if touches.is_file() else 0,
           "gold": gold}
(out / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: summary[k] for k in ("name", "status", "wall_seconds", "cost_usd", "calls", "touches")}
                 | {"gold_passed": gold.get("gold_passed")}, ensure_ascii=False))
