"""Reproduce offline onboarding with public synthetic sources; no models or downloads.

Run: python -m tests.onboarding_acceptance > /tmp/onboarding-acceptance.json
The mixed fixture exercises javac/java/node, not Maven plugins or npm dependencies.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from storyloop.baseline import bootstrap_project
from storyloop.harness_command import expand_command
from storyloop.onboarding import scaffold_project
from storyloop.readiness import inspect_readiness
from storyloop.system_smoke import run_system_smoke_isolated
from tests.onboarding_support import commit, repository

CASE = '''import json
import subprocess
import sys
import unittest
from storyloop.testing.smoke_harness.v1 import HarnessError

class Contract(unittest.TestCase):
    def test_business_response(self):
        try:
            result = subprocess.run(COMMAND, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise HarnessError("Business process did not complete")
            value = json.loads(result.stdout)
            if not isinstance(value, dict) or set(value) != {"value"}:
                raise HarnessError("Missing business response protocol")
            if type(value["value"]) is not int:
                raise HarnessError("Invalid business response value type")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise HarnessError("Business response unavailable") from None
        self.assertEqual(value["value"], EXPECTED)
'''

EXECUTOR = '''from storyloop.testing.smoke_harness.v1 import python_cases, run_hooks, run_command

def build(context):
    run_command(["javac", "-d", ".", "backend/Main.java"], context.scratch.mirror)

def run_case(context, case):
    return python_cases(context.project, [case], cwd=context.scratch.mirror)

def run(context):
    return run_hooks(context, run_case=run_caseBUILD)
'''


def write(root, relative, content):
    """Write only named public fixture artifacts beneath the disposable repository."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def case_source(command, expected=7):
    return CASE.replace("COMMAND", command).replace("EXPECTED", str(expected))


def sources(mixed):
    if not mixed:
        return {"pyproject.toml": "[project]\nname='public-onboarding-sample'\nversion='0.0.0'\n",
                "app/main.py": "import json\nprint(json.dumps({'value': 7}))\n"}
    return {"backend/pom.xml": '<project><modelVersion>4.0.0</modelVersion>'
            '<groupId>example.invalid</groupId><artifactId>public-sample</artifactId>'
            '<version>0.0.0</version></project>',
            "backend/Main.java": 'public class Main { public static void main(String[] args) {'
            'System.out.println("{\\\"value\\\":7}"); } }\n',
            "frontend/package.json": '{"name":"public-sample","version":"0.0.0","private":true}',
            "frontend/main.js": 'console.log(JSON.stringify({value:7}));\n'}


def settings(mixed):
    check = ["node", "--check", "frontend/main.js"] if mixed else [
        "{python}", "-c", "import ast; ast.parse(open('app/main.py').read())"]
    return {"unit_commands": [["{python}", "-m", "unittest", "discover", "-s", "tests"]],
            "check_commands": [check], "module_roots": ["backend", "frontend"] if mixed else ["app"],
            "test_roots": ["tests"], "integration_paths": []}


def complete(root, mixed):
    path = root / "acceptance/onboarding.json"
    manifest = json.loads(path.read_text())
    manifest["reviewed"] = {"settings": True, "guide": True}
    commands = {"python": "[sys.executable, '-I', '-B', 'app/main.py']"} if not mixed else {
        "maven": "['java', '-cp', '.', 'Main']", "node": "['node', 'frontend/main.js']"}
    tests = []
    for relative, executor in manifest["executors"].items():
        stack = executor["stack"]
        executor["source_files"] = (["backend/Main.java"] if stack == "maven" else
                                    ["frontend/main.js"] if stack == "node" else ["app/main.py"])
        executor["hooks"] = {name: {"status": "not_applicable", "reason": "No persistent service"}
                             for name in ("build", "start", "ready", "cleanup")}
        executor["hooks"]["run_case"] = {"status": "implemented"}
        if stack == "maven":
            executor["hooks"]["build"] = {"status": "implemented"}
        write(root, relative, EXECUTOR.replace("BUILD", ", build=build" if stack == "maven" else ""))
        case = f"acceptance/smoke/test_{stack}.py"
        write(root, case, case_source(commands[stack]))
        write(root, f"acceptance/smoke/unselected_{stack}.py", case_source(commands[stack], 8))
        tests.append({"id": stack, "description": "Real native business process returns value 7",
                      "command": ["{storyloop_harness}", relative, case], "timeout_seconds": 30})
    write(root, "acceptance/onboarding.json", json.dumps(manifest))
    write(root, "acceptance/storyloop-settings.json", json.dumps(settings(mixed)))
    write(root, "acceptance/smoke/manifest.json", json.dumps({"version": 1, "tests": tests}))
    unit = "['node', 'frontend/main.js']" if mixed else commands["python"]
    # Unit collection runs in the project's ordinary interpreter, separate from the judge.
    write(root, "tests/test_business.py", case_source(unit))


def summarize(results):
    return [{key: row.get(key) for key in ("id", "status", "returncode", "counts")}
            for row in results]


def measure_sample(root, mixed):
    started = time.monotonic()
    repository(root, sources(mixed))
    stacks = ["maven", "node"] if mixed else ["python"]
    draft = scaffold_project(root, stacks)
    command = ["{storyloop_harness}", f"acceptance/tools/{stacks[0]}_smoke.py"]
    pending = subprocess.run(expand_command(command), cwd=root, capture_output=True, timeout=10)
    complete(root, mixed)
    commit(root)
    locked = bootstrap_project(root, settings(mixed))
    smoke = run_system_smoke_isolated(root)
    readiness = inspect_readiness(root, refresh=True)
    assert pending.returncode == 2 and locked["status"] == "BASELINE_LOCKED"
    assert smoke["status"] == "PASSED" and readiness["status"] == "READY", (smoke, readiness)
    return {"sample": "maven-node-shape" if mixed else "independent-python", "scaffold": draft["status"],
            "pending_exit": pending.returncode, "init": locked["status"], "smoke": smoke["status"],
            "results": summarize(smoke["results"]), "readiness": readiness["status"],
            "readiness_layers": {name: {key: value.get(key) for key in ("status", "executed", "failed")}
                                 for name, value in readiness["layers"].items()},
            "wall_seconds": round(time.monotonic() - started, 3),
            "real_tools": ["javac", "java", "node"] if mixed else ["python"],
            "simulated_hooks": [], "external_dependencies": [], "maven_plugin_build": "NOT_RUN"}


def main():
    """Emit measured facts, explicitly separating native tools from Maven availability."""
    results = []
    with tempfile.TemporaryDirectory(prefix="storyloop-public-onboarding-") as folder:
        for mixed in (False, True):
            missing = [name for name in ("javac", "java", "node") if not shutil.which(name)] if mixed else []
            if missing:
                results.append({"sample": "maven-node-shape", "status": "NOT_RUN", "missing": missing})
                continue
            root = Path(folder) / ("公开 Maven Node" if mixed else "公开 Python")
            root.mkdir()
            results.append(measure_sample(root, mixed))
    print(json.dumps({"samples": results, "human_effective_hours": None,
                      "wait_seconds_separately_measured": False,
                      "limitation": "Synthetic offline fixtures; no actual RuoYi or universal half-day claim"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
