"""Prove characterization against committed old code plus declared lock artifacts."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .baseline import _git, load_project_config
from .boundaries import capture_snapshot, snapshot_changes
from .models import StoryLoopError, require
from .project_profile import matches_test_root
from .oracle_counts import project_patterns
from .path_security import safe_target
from .smoke_runner import _files_digest, run_commands


def run_characterization_baseline(project, commands, timeout, pending):
    """Keep implementation tests untouched; run all baseline units and entire lock files.

    Late characterization restores old business code while implementation-authored
    feature tests remain in the workspace. They belong to final verification, not
    to the old-behavior proof. A lock file itself is never filtered by test function.
    """
    project = Path(project).resolve()
    before = capture_snapshot(project, include_control=True, include_ignored=True)
    head = _git(project, 'rev-parse', 'HEAD').strip()
    roots = load_project_config(project)['profile'].get('test_roots', [])
    try:
        with tempfile.TemporaryDirectory(prefix='storyloop-characterization-') as folder:
            candidate = Path(folder)
            paths = _baseline(project, candidate, head)
            for relative in sorted(set(pending['tests']) | set(pending['changed'])):
                require(matches_test_root(relative, roots), 'Characterization artifact must be a test path')
                source = safe_target(project, relative, 'characterization source')
                target = safe_target(candidate, relative, 'characterization target')
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    paths.add(relative)
            digest = _files_digest(candidate, sorted(paths))
            result = run_commands(candidate, commands, timeout, project_patterns(project))
    finally:
        after = capture_snapshot(project, include_control=True, include_ignored=True)
        if snapshot_changes(before, after):
            raise StoryLoopError('Characterization modified the source project', code='WORKSPACE_TAINTED')
    return {**result, 'candidate_sha256': digest, 'source_commit': head,
            'candidate_basis': 'COMMITTED_BASELINE_WITH_CHARACTERIZATION_ARTIFACTS'}


def _baseline(project, candidate, commit):
    # Archive attributes may drop tests or substitute bytes; read raw tree/blob objects.
    # safe_target resolves its root, so compare against the resolved spelling too
    # (Windows temp folders are often 8.3 short names such as RUNNER~1).
    candidate = Path(candidate).resolve()
    tree =subprocess.run(['git', 'ls-tree', '-rz', '--full-tree', commit], cwd=project,
                          capture_output=True, check=True).stdout
    paths = set()
    for entry in tree.split(b'\0'):
        if not entry:
            continue
        metadata, raw_path = entry.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        require(kind == b'blob' and mode in {b'100644', b'100755'},
                'Characterization baseline must contain only regular files')
        relative = os.fsdecode(raw_path)
        target = safe_target(candidate, relative, 'characterization baseline target')
        require(target.relative_to(candidate).as_posix() == relative,
                'Characterization baseline path must be canonical')
        content = subprocess.run(['git', 'cat-file', 'blob', oid.decode('ascii')], cwd=project,
                                 capture_output=True, check=True).stdout
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if mode == b'100755' else 0o644)
        paths.add(relative)
    return paths
