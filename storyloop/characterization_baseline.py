"""Prove characterization against committed old code plus declared lock artifacts."""
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
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
    archive = subprocess.run(['git', 'archive', '--format=tar', commit], cwd=project,
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = [member for member in tar.getmembers() if member.isfile()]
        for member in members:
            safe_target(candidate, member.name, 'characterization baseline target')
        tar.extractall(candidate, members=members, filter='data')
    return {member.name for member in members}
