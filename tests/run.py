"""Run a test suite with one process per module, several modules at a time (standard library only).

    python tests/run.py --suite core            # Linux PRs and pushes to main
    python tests/run.py --suite platform        # Windows PRs
    python tests/run.py --suite full --workers 4 # every tests/test_*.py module

Modules run in separate processes because each test builds its own temporary Git repository;
a module is the unit of parallelism, so the slowest module bounds the wall time.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suites import CORE, PLATFORM_SUITE  # noqa: E402  (tests/ is not a package root when run as a script)

RAN = re.compile(r"^Ran (\d+) tests? in", re.MULTILINE)
SKIPPED = re.compile(r"(?:^|[,(]\s*)skipped=(\d+)", re.MULTILINE)


def modules(root: Path, suite: str) -> list[str]:
    if suite == "core":
        return list(CORE)
    if suite == "platform":
        return list(PLATFORM_SUITE)
    return sorted("tests." + path.stem for path in (root / "tests").glob("test_*.py"))


def run_module(root: Path, module: str) -> dict:
    start = time.monotonic()
    done = subprocess.run([sys.executable, "-m", "unittest", module], cwd=root, capture_output=True)
    output = done.stderr.decode("utf-8", "replace") + done.stdout.decode("utf-8", "replace")
    ran = RAN.search(output)
    skipped = SKIPPED.search(output)
    return {"module": module, "ok": done.returncode == 0, "seconds": time.monotonic() - start,
            "tests": int(ran.group(1)) if ran else 0,
            "skipped": int(skipped.group(1)) if skipped else 0, "output": output}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=("core", "platform", "full"), default="core")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--list", action="store_true", help="print the suite's modules and exit")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to a legacy code page (cf. cli.py)
        sys.stdout.reconfigure(encoding="utf-8")
    names = modules(args.root, args.suite)
    if args.list:
        print("\n".join(names))
        return 0
    return _run_suite(args, names)


def _run_suite(args, names: list[str]) -> int:
    started, failed, total, skipped = time.monotonic(), [], 0, 0
    with ThreadPoolExecutor(max(1, args.workers)) as pool:
        futures = [pool.submit(run_module, args.root, name) for name in names]
        for future in as_completed(futures):
            result = future.result()
            total += result["tests"]
            skipped += result["skipped"]
            print(f"{'ok  ' if result['ok'] else 'FAIL'} {result['seconds']:7.1f}s  {result['module']}", flush=True)
            if not result["ok"]:
                failed.append(result)
    for result in failed:
        print(f"\n===== {result['module']} =====\n{result['output']}", flush=True)
    status = f"失败 {len(failed)} 个模块" if failed else "全部通过"
    print(f"\n{args.suite}: {len(names)} 个模块，{total} 个测试，跳过 {skipped} 个，{status}，"
          f"用时 {time.monotonic() - started:.0f}s（{args.workers} 路并行）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
