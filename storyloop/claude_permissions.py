"""Claude Code profile permissions; Edit(path) governs all file modification tools."""
from pathlib import Path
import re
import os
import sys
import shlex

from .models import require
from .path_security import safe_target, safe_absolute_target, forbidden_path_root

READERS = ['Read', 'Grep', 'Glob']
WRITERS = {'implement', 'characterize'}
SHELL_ROLES = WRITERS | {'review', 'design', 'survey'}


def profile_command(claude: str, request: dict, turns: int, extra_bash: list[str], model: str | None) -> list[str]:
    """Deny prompting, limit tool inventory, and whitelist only validated path/command rules."""
    profile, role = request['role_profile'], request['role']
    project = Path(request['project']).resolve()
    writing = role in WRITERS
    tools = READERS + (['Edit', 'Write', 'MultiEdit'] if writing else [])
    tools += ['Bash'] if role in SHELL_ROLES else []
    allowed = list(READERS)
    if writing:
        allowed += [f'Edit(/{_writable_pattern(project, path)})' for path in profile.get('writable_paths', [])]
    denied = [f'Edit(/{_pattern(project, path)})' for path in profile.get('forbidden_paths', [])]
    denied += ['Edit(/.git/**)', 'Edit(/.storyloop/**)']
    if 'Bash' in tools:
        rules = [_bash_rule(project, rule) for rule in extra_bash]
        rules += [_check_rule(project, row) for row in profile.get('fast_checks', [])] if writing else []
        allowed += [f'Bash({rule})' for rule in dict.fromkeys(rules)]
    command = [claude, '-p', '--restricted', '--setting-sources', '', '--strict-mcp-config',
               '--output-format', 'json', '--permission-prompts', 'none', '--max-turns', str(turns),
               '--permission-mode', 'dontAsk', '--tools', ','.join(tools),
               '--allowedTools', *allowed, '--disallowedTools', *dict.fromkeys(denied)]
    return command + (['--model', model] if model else [])


def _pattern(project: Path, value: str) -> str:
    require(isinstance(value, str) and not any(c in value for c in ';|&$()`\n\r\\'), 'Unsafe permission path')
    root = forbidden_path_root(value)
    safe_target(project, root, 'permission path', target_directory=(project / root).is_dir())
    return root + ('/**' if value.endswith('/**') or (project / root).is_dir() else '')


def _writable_pattern(project: Path, value: str) -> str:
    pattern = _pattern(project, value)
    root = project / forbidden_path_root(value)
    # A recursive CLI rule must not bless an existing linked descendant. The final engine
    # audit still checks links created after this snapshot and indirect shell writes.
    if root.is_dir():
        for folder, directories, files in os.walk(root, followlinks=False):
            for name in directories + files:
                entry = Path(folder) / name
                safe_target(project, entry.relative_to(project).as_posix(), 'writable tree',
                            target_directory=entry.is_dir())
    return pattern


def _safe_token(project: Path, token: str) -> str:
    require(isinstance(token, str) and token and re.fullmatch(r'[A-Za-z0-9_./:=+@%-]+', token),
            'Unsafe Bash prefix token')
    require('..' not in Path(token).parts and '*' not in token, 'Unsafe Bash prefix path')
    if '/' in token and not token.startswith('-'):
        target = Path(token)
        if target.is_absolute():
            safe_absolute_target(target, 'Bash prefix path')
        else:
            safe_target(project, token, 'Bash prefix path', target_directory=(project / token).is_dir())
    return token


def _bash_rule(project: Path, value: str) -> str:
    require(isinstance(value, str) and value.endswith(':*'), 'Bash prefix must end in :*')
    tokens = value[:-2].split(' ')
    return ' '.join(_safe_token(project, token) for token in tokens) + ':*'


def _check_rule(project: Path, row: list[str]) -> str:
    require(isinstance(row, list) and row, 'Invalid fast check')
    tokens = [_python_token() if item == '{python}' else _safe_token(project, item) for item in row]
    return ' '.join(tokens) + ':*'


def _python_token() -> str:
    # Only the engine-owned interpreter may be resolved/quoted. User prefixes still
    # pass _safe_token, including its lexical traversal and link-chain checks.
    executable = Path(sys.executable).resolve()
    safe_absolute_target(executable, 'Python interpreter')
    return _executable_token(executable)


def _executable_token(executable) -> str:
    # Claude's Bash tool uses POSIX syntax, including Git Bash on native Windows.
    # Do not use cmd.exe/list2cmdline quoting for Bash permissions. Reject rule
    # metacharacters before shell quoting; spaces are safe inside a quoted token.
    value = executable.as_posix()
    require(executable.is_absolute() and '..' not in executable.parts and
            re.fullmatch(r'[A-Za-z0-9_./: +@%-]+', value), 'Unsafe Python interpreter path')
    return shlex.quote(value)
