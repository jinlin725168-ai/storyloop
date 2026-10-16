"""Verify configured trusted offline resolvers against committed dependency inputs."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile

from . import dependency_resolver
from .baseline import _git, load_project_config
from .intake import digest
from .models import StoryLoopError, require
from .path_security import repository_relative, safe_target
from .processutil import run_process

RESOLVER = Path(dependency_resolver.__file__).resolve()
# Capture engine code, never execute a candidate-provided wrapper or import target project modules.
RESOLVER_SOURCE = RESOLVER.read_bytes()
SUPPORT = ('Provide a configured trusted offline Python wheel resolver: only committed local '
           'py3-none-any wheels without Requires-Dist/Requires-Python are supported; '
           'no installation or arbitrary shell command is permitted.')


def verify_dependencies(project: Path, state: dict, dependencies: list[dict]) -> list[dict]:
    """Return stable command/input-bound evidence, or NEEDS_INPUT without unsafe execution."""
    if not dependencies:
        return []
    commands = load_project_config(project)['profile']['check_commands']
    evidence = []
    try:
        for dependency in dependencies:
            index = dependency['verification']['check_index']
            require(type(index) is int and 0 <= index < len(commands), 'Invalid resolver check_index')
            require(dependency['ecosystem'] == 'python', 'Unsupported dependency ecosystem')
            command = commands[index]
            relative = _supported(command, project)
            record = _resolve(project, state['contract_commit'], command, relative)
            expected = {key: dependency[key] for key in ('ecosystem', 'name', 'version')}
            require(expected in record['resolved'], 'Resolver did not prove the requested exact version')
            evidence.append({**record, 'check_index': index})
    except (StoryLoopError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        raise StoryLoopError(SUPPORT, code='NEEDS_INPUT') from exc
    return evidence


def _supported(command: list[str], project: Path) -> str:
    require(isinstance(command, list) and len(command) == 9 and all(
        isinstance(item, str) for item in command), 'Unsupported resolver command')
    require(command[0] == sys.executable and command[1] == '-I'
            and command[2] == str(RESOLVER) and not RESOLVER.is_relative_to(project.resolve()),
            'Resolver must be the installed trusted engine script and interpreter')
    require(command[3] == '--wheel' and command[5] == '--cache-dir' and command[7] == '--output',
            'Unsupported resolver arguments')
    for argument in (command[4], command[6], command[8]):
        require(repository_relative(argument, 'resolver path') == argument
                and not any(ord(char) < 32 for char in argument), 'Unsafe resolver path')
    require(command[4].endswith('.whl'), 'Resolver input must be a wheel')
    return command[4]


def _committed_wheel(project: Path, commit: str, relative: str, root: Path) -> tuple[Path, str]:
    entry = _git(project, 'ls-tree', commit, '--', relative).strip()
    require(entry and entry.split(None, 1)[0] == '100644', 'Wheel must be a regular committed blob')
    blob = entry.split()[2]
    size = int(_git(project, 'cat-file', '-s', blob).strip())
    require(0 < size <= dependency_resolver.MAX_WHEEL, 'Wheel exceeds bounded input size')
    archive_path = root / 'input.tar'
    _git(project, 'archive', '--format=tar', '--output=' + str(archive_path), commit, '--', relative)
    with tarfile.open(archive_path) as archive:
        member = archive.getmember(relative)
        require(member.isfile() and member.size == size, 'Wheel archive is not the committed blob')
        target = safe_target(root / 'project', relative, 'resolver input')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.extractfile(member).read(size + 1))
    require(_git(project, 'hash-object', '--no-filters', str(target)).strip() == blob,
            'Wheel archive differs from the trusted commit')
    return target, hashlib.sha256(target.read_bytes()).hexdigest()


def _resolve(project: Path, commit: str, command: list[str], relative: str) -> dict:
    with tempfile.TemporaryDirectory(prefix='storyloop-dependency-') as folder:
        root = Path(folder)
        (root / 'project').mkdir()
        wheel, wheel_digest = _committed_wheel(project, commit, relative, root)
        runner = root / 'resolver.py'
        runner.write_bytes(RESOLVER_SOURCE)
        output = root / 'output.json'
        cache = root / 'cache'
        argv = [sys.executable, '-I', str(runner), '--wheel', str(wheel),
                '--cache-dir', str(cache), '--output', str(output)]
        environment = {'HOME': str(root), 'TMP': str(root), 'TEMP': str(root), 'TMPDIR': str(root)}
        result = run_process(argv, root / 'project', 30, environment_allowlist=list(environment),
                             source_environment=environment)
        require(result.get('contained') is True and result.get('returncode') == 0
                and not any(result.get(k) for k in ('error', 'truncated', 'timed_out')),
                'Dependency resolution failed')
        lines = result.get('stdout', '').splitlines()
        require(bool(lines), 'Resolver omitted its evidence')
        value = json.loads(lines[-1])
        require(isinstance(value, dict) and set(value) == {'resolved'}
                and isinstance(value['resolved'], list), 'Invalid resolver evidence')
        require(output.is_file() and json.loads(output.read_text()) == value, 'Resolver output differs')
        return {'resolved': value['resolved'], 'source_commit': commit, 'wheel_digest': wheel_digest,
                'command_digest': digest(command), 'resolver_digest': hashlib.sha256(RESOLVER_SOURCE).hexdigest()}
