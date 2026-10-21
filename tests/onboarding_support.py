"""Public synthetic repositories used only by onboarding tests."""
import contextlib
import io
import json
from pathlib import Path

from storyloop.cli import main
from tests.test_baseline_behavior import git


def repository(root: Path, markers=None):
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Onboarding Fixture")
    for name, content in (markers or {"pyproject.toml": "[project]\nname='fixture'\n",
                                     "app/main.py": "VALUE = 1\n"}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    commit(root)


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "fixture")


def cli(*arguments):
    output, error = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        try:
            code = main(list(map(str, arguments)))
        except SystemExit as exc:
            code = exc.code
    raw = output.getvalue() or error.getvalue()
    try:
        value = json.loads(raw)
    except ValueError:
        value = {"raw": raw}
    return code, value
