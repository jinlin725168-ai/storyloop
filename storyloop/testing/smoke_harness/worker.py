"""Isolated unittest worker. Candidate imports are confined to this business subprocess."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest

# -I removes the script directory. Only this installation supplies engine modules.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from storyloop.testing.smoke_harness.results import Result


def execute(project: Path, relative: str) -> Result:
    try:
        sys.path.insert(1, str(project))
        spec = importlib.util.spec_from_file_location("storyloop_selected_case", project / relative)
        module = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(module)
            suite = unittest.defaultTestLoader.loadTestsFromModule(module)
            result = unittest.TestResult()
            suite.run(result)
        # unittest counts skips and failed imports as testsRun; neither is an execution proof.
        executed = result.testsRun - len(result.skipped)
        return Result(executed=executed, failed=len(result.failures),
                      errors=len(result.errors) + len(result.unexpectedSuccesses),
                      skipped=len(result.skipped))
    except BaseException:
        return Result(errors=1)


def main() -> int:
    result = execute(Path(sys.argv[1]), sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(result.document()), encoding="utf-8")
    return result.code


if __name__ == "__main__":
    sys.exit(main())
