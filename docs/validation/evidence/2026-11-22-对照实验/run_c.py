"""C arm: plain Claude Code, one `claude -p` on a fresh base repo, then hidden Gold.
  run_c.py sample <name>      (sqlite-utils sample from samples.json)
  run_c.py case <case-id>     (bench Case v2; the prompt carries its approved contract)"""
import json, subprocess, sys, time
from pathlib import Path
X = Path(__file__).parent
PY = "/home/jinlin/work/l3/venv-xp/bin/python"
TOOLS = ["Read", "Grep", "Glob", "Edit", "Write", "MultiEdit", "Bash(python3:*)", "Bash(python:*)",
         "Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(find:*)", "Bash(grep:*)",
         "Bash(wc:*)", "Bash(diff:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
         "Bash(git show:*)", "Bash(git add:*)", "Bash(git commit:*)"]
ASK = ("你在这个 Git 仓库里工作，没有人可以回答问题，请自行做出合理判断。\n\n{req}\n\n"
       "请实现这个需求，补充或更新相应的测试，运行项目的测试并确保全部通过，最后用 git 提交你的改动。"
       "测试和项目代码用 `python` 运行。完成后用一两句话说明你做了什么。")
kind, name = sys.argv[1], sys.argv[2]
tag = sys.argv[3] if len(sys.argv) > 3 else ""
out = X / "results" / f"C-{name}{tag}"; out.mkdir(parents=True, exist_ok=True)
tree = out / "tree"
if kind == "sample":
    sample = json.loads((X / "samples.json").read_text(encoding="utf-8"))[name]
    source, base, requirement = sample["source"], sample["solution"] + "^", sample["requirement"]
else:
    case = Path("/home/jinlin/work/storyloop-xp/bench/cases") / name / "v2"
    manifest = json.loads((case / "case.json").read_text(encoding="utf-8"))
    source = {"storyloop-dogfood3": "/home/jinlin/work/storyloop-dogfood3",
              "storyloop-dogfood6": "/home/jinlin/work/storyloop-dogfood6"}[manifest["repository_id"]]
    base = manifest["base_commit"]
    product = json.loads((case / "contract.json").read_text(encoding="utf-8"))["product"]
    lines = [f"需求：{product['goal']}", ""]
    for story in product["stories"]:
        lines += [f"故事 {story['id']}：{story['title']}", "规则："] + [f"- {r}" for r in story.get("rules", [])]
        lines += ["验收条件："] + [f"- {a['id']}：GIVEN {a['given']}；WHEN {a['when']}；THEN {a['then']}"
                                   for a in story["acceptance"]]
        lines += ["验证入口："] + [f"- {e['id']}：{e['locator']}" for e in story.get("verification_entries", [])]
    lines += ["非目标："] + [f"- {g}" for g in product.get("non_goals", [])]
    requirement = "\n".join(lines)
subprocess.run([str(X / "fresh_repo.sh"), source, base, str(tree)], check=True)
prompt = ASK.format(req=requirement)
(out / "prompt.md").write_text(prompt, encoding="utf-8")
env = {"PATH": f"/home/jinlin/work/l3/venv-xp/bin:/usr/bin:/bin:/home/jinlin/.local/bin",
       "HOME": "/home/jinlin", "LANG": "C.UTF-8"}
started = time.monotonic()
try:
    run = subprocess.run(["claude", "-p", prompt, "--output-format", "json", "--model", "opus",
                          "--max-turns", "200", "--permission-mode", "acceptEdits",
                          "--tools", "Read,Grep,Glob,Edit,Write,MultiEdit,Bash", "--allowedTools", *TOOLS],
                         cwd=tree, capture_output=True, text=True, timeout=7200, env=env)
    raw, code = run.stdout, run.returncode
except subprocess.TimeoutExpired as exc:
    raw, code = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""), "timeout"
wall = round(time.monotonic() - started)
(out / "claude.json").write_text(raw, encoding="utf-8")
try:
    info = json.loads(raw)
except ValueError:
    info = {}
subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True)
diff = subprocess.run(["git", "-C", str(tree), "diff", "--cached", "HEAD"], capture_output=True, text=True).stdout
committed = subprocess.run(["git", "-C", str(tree), "log", "--oneline"], capture_output=True, text=True).stdout
first = subprocess.run(["git", "-C", str(tree), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.strip()
(out / "candidate.patch").write_bytes(subprocess.run(["git", "-C", str(tree), "diff", "--binary", first],
                                                      capture_output=True).stdout)
if kind == "sample":
    gold = json.loads(subprocess.run([PY, str(X / "gold_sqlite.py"), name, str(tree), PY],
                                     capture_output=True, text=True).stdout)
else:
    sys.path.insert(0, "/home/jinlin/work/storyloop-xp")
    from storyloop.bench_calibrate import judge_gold
    judged = judge_gold(case, tree,
                        python=Path("/home/jinlin/work/storyloop-dogfood-venv-main/bin/python"))
    gold = {"gold_passed": judged["passed"], "checks": [{k: c.get(k) for k in ("check_id", "passed")}
                                                        for c in judged["checks"]]}
result = {"arm": "C", "name": name, "exit": code, "wall_seconds": wall,
          "cost_usd": info.get("total_cost_usd"), "turns": info.get("num_turns"),
          "claimed": (info.get("result") or "")[:1500], "commits": committed.strip().splitlines(),
          "uncommitted_changes": bool(diff.strip()), "gold": gold}
(out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: result[k] for k in ("name", "exit", "wall_seconds", "cost_usd", "turns")} |
                 {"gold_passed": gold.get("gold_passed")}, ensure_ascii=False))
