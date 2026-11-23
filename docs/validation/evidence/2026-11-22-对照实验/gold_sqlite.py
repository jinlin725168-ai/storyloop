"""Hidden Gold for a sqlite-utils sample: overlay the upstream solution's test files, then
F2P = the upstream-added tests pass; P2P = the rest of the suite passes. Usage:
  gold_sqlite.py <sample> <candidate tree> <python> -> JSON on stdout"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
samples = json.loads(Path(__file__).with_name("samples.json").read_text(encoding="utf-8"))
name, tree, python = sys.argv[1], Path(sys.argv[2]).resolve(), sys.argv[3]
sample = samples[name]
with tempfile.TemporaryDirectory(prefix="xp-gold-") as folder:
    work = Path(folder) / "tree"
    shutil.copytree(tree, work, ignore=shutil.ignore_patterns(".git", ".storyloop", "__pycache__", "*.egg-info"))
    for path in sample["test_files"]:
        content = subprocess.run(["git", "-C", sample["source"], "show", f"{sample['solution']}:{path}"],
                                 capture_output=True, check=True).stdout
        (work / path).write_bytes(content)
    def pytest(*args):
        r = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=work,
                           capture_output=True, text=True, timeout=1200)
        return {"returncode": r.returncode, "tail": (r.stdout + r.stderr)[-1500:]}
    f2p = pytest(*sample["f2p"])
    deselect = [arg for node in sample["f2p"] for arg in ("--deselect", node)]
    p2p = pytest("tests", *deselect)
print(json.dumps({"sample": name, "f2p_passed": f2p["returncode"] == 0, "p2p_passed": p2p["returncode"] == 0,
                  "gold_passed": f2p["returncode"] == 0 and p2p["returncode"] == 0, "f2p": f2p, "p2p": p2p},
                 ensure_ascii=False))
