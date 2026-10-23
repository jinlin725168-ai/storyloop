"""Complete routing signal fixtures used by routing tests."""
from __future__ import annotations

from typing import TYPE_CHECKING
from copy import deepcopy
from pathlib import Path

from tests.test_baseline_behavior import git

if TYPE_CHECKING:
    from storyloop.project_loop import LoopServices


_SIGNALS = {
    "nature": "feature",
    "stories": 1,
    "criteria": 4,
    "manual": 0,
    "existing_files": 1,
    "created_files": 0,
    "interfaces": 0,
    "max_references": 0,
    "complete": True,
    "risks": {
        key: "no"
        for key in (
            "sensitive",
            "migration",
            "external_contract",
            "new_interface",
            "crosscut",
            "principle_deviation",
            "hard_verify_high_cost",
            "dependency_swap",
            "persistent_state",
            "human_choice",
            "guard",
        )
    },
}


def signals(**overrides) -> dict:
    """Return an isolated, complete Signals object with top-level overrides."""
    value = deepcopy(_SIGNALS)
    value.update(overrides)
    return value


def routing_repository(root: Path) -> None:
    """Create the minimal committed application used by routing evidence tests."""
    git(root, "init", "-q")
    git(root, "config", "user.email", "storyloop@example.invalid")
    git(root, "config", "user.name", "StoryLoop Test")
    (root / "app").mkdir()
    (root / "tests").mkdir()
    (root / "app/service.py").write_text("def serve():\n    return 'ok'\n", encoding="utf-8")
    (root / "tests/test_service.py").write_text(
        "import unittest\n\n"
        "class ServiceTests(unittest.TestCase):\n"
        "    def test_service(self):\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")


def readiness_repository(root: Path, **settings) -> None:
    """Initialize a clean local repository with explicit readiness test layers."""
    from storyloop.onboarding_cli import bootstrap_with_project_checks as bootstrap_project
    routing_repository(root)
    bootstrap_project(root, {
        "unit_commands": [["{python}", "-m", "unittest", "discover", "-s", "tests"]],
        "integration_commands": [], "check_commands": [], **settings,
    })
    local_retrospective_adapter(root)


def local_retrospective_adapter(project: Path) -> None:
    """Keep service-injected fixtures offline without disabling automatic retrospectives."""
    import sys
    from storyloop.storage import load_json, write_json
    path = Path(project) / '.storyloop/config.json'
    config = load_json(path)
    # Explicit adapter/profile tests retain their own transport and mocks.
    if config.get('adapter') != 'codex' or config.get('agent_command') or config.get('adapters') or config.get('roles'):
        return
    script = (
        "import json,sys; request=json.load(sys.stdin); "
        "assert request['role']=='review' and request['payload'].get('mode')=='retrospective', "
        "'Fixture only supports retrospective calls'; "
        "print(json.dumps(dict(lessons=[],errata=[],uncovered_concepts=[],nothing_to_learn='Fixture: nothing reusable')))"
    )
    config.update(adapter='command', agent_command=[sys.executable, '-c', script])
    write_json(path, config)


def legacy_create_change(project, *args, **kwargs):
    """Explicit historical fixture: preserve pre-routing scenarios, never a production bypass."""
    from storyloop.changes import create_change
    from storyloop.storage import write_json
    local_retrospective_adapter(Path(project))
    state = create_change(project, *args, **kwargs)
    state.pop('routing_version')
    state.pop('intake')
    write_json(Path(project) / '.storyloop/state/changes' / f"{state['change_id']}.json", state)
    return state


def routed_case(root: Path, tier: str, nature: str = 'feature') -> tuple[dict, LoopServices]:
    """Build a real counted repository and approved contract; roles only author artifacts."""
    from storyloop.approvals import approve_change
    from storyloop.changes import create_change
    from storyloop.project_loop import LoopServices
    from storyloop.readiness import inspect_readiness
    from tests.test_routing_planning import answer
    from tests.test_structured_output import passing_review
    if tier not in ('light', 'standard', 'heavy'):
        raise ValueError('Unknown routing tier')
    from storyloop.onboarding_cli import bootstrap_with_project_checks as bootstrap_project
    routing_repository(root)
    (root / 'core.py').write_text('STABLE = True\n', encoding='utf-8')
    (root / 'tests/test_service.py').write_text(
        'import unittest\nfrom app.service import serve\nclass Service(unittest.TestCase):\n'
        '    def test_serve(self): self.assertEqual(serve(), "ok")\n', encoding='utf-8')
    git(root, 'add', 'core.py', 'tests/test_service.py')
    _install_counted_smoke(root)
    bootstrap_project(root, {'module_roots': ['app', 'modules'], 'test_roots': ['tests'],
        'unit_commands': [['{python}', '-m', 'unittest', 'discover', '-s', 'tests']],
        'check_commands': [['{python}', '-c', 'pass']]})
    local_retrospective_adapter(root)
    ready = inspect_readiness(root)
    assert ready['status'] == 'READY'
    assert ready['layers']['unit']['executed'] == ready['layers']['smoke']['executed'] == 1
    assert ready['layers']['checks']['status'] == 'PASSED'
    value = routed_product()
    if nature != 'feature':
        from storyloop.state_format import ROW_TYPES
        value['nature'] = nature
        value['coverage_exemptions'] = [
            {'row_type': row, 'reason': 'Not relevant to this routed sample'}
            for row in ROW_TYPES if row != 'positive']
    change = create_change(root, 'orders', value)
    change = approve_change(root, change['change_id'], tier=None if tier == 'light' else tier)
    services = LoopServices(lambda p, c: routed_acceptance(change['change_id']),
                            routed_implement, passing_review, characterize=routed_characterize,
                            design=lambda p, c: answer(value))
    return change, services


def _install_counted_smoke(root: Path) -> None:
    from storyloop.storage import write_json
    source = ('import unittest\nimport runpy\nclass Core(unittest.TestCase):\n'
              '    def test_core(self): self.assertEqual(runpy.run_path("app/service.py")["serve"](), "ok")\n'
              "if __name__ == '__main__': unittest.main()\n")
    folder = root / 'acceptance/smoke'
    folder.mkdir(parents=True)
    (folder / 'test_core.py').write_text(source, encoding='utf-8')
    write_json(folder / 'manifest.json', {
        'version': 1, 'tests': [{'id': 'core', 'description': 'protected core smoke',
        'command': ['{python}', 'acceptance/smoke/test_core.py'], 'timeout_seconds': 10}]})
    git(root, 'add', 'acceptance')
    git(root, 'commit', '-qm', 'protected system smoke')


def routed_implement(project, context):
    target = project / 'modules/orders/service.py'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('VALUE = 1\n', encoding='utf-8')
    return {'summary': 'Implement AC-1', 'questions': []}


def routed_characterize(project, context):
    name = 'tests/test_lock.py'
    target = project / name
    source = ('import unittest\nfrom app.service import serve\n'
              'class Lock(unittest.TestCase):\n'
              '    def test_serve(self): self.assertEqual(serve(), "ok")\n')
    # Replanning must produce an auditable test edit, not re-write identical tracked bytes.
    if target.exists():
        source = target.read_text(encoding='utf-8') + '# Revalidated behavior after replan\n'
    target.write_text(source, encoding='utf-8')
    return {'summary': 'Preserve serve behavior', 'touched': ['app/service.py'],
            'tests': [name], 'questions': []}


def routed_acceptance(identifier):
    from tests.test_project_loop import smoke_payload
    payload = smoke_payload(identifier)
    payload['files'][0]['content'] = (
        'import unittest\nimport runpy\nfrom pathlib import Path\nclass Acceptance(unittest.TestCase):\n'
        '    def test_new_value(self):\n'
        '        path = Path("modules/orders/service.py")\n'
        '        self.assertTrue(path.is_file())\n'
        '        self.assertEqual(runpy.run_path(str(path))["VALUE"], 1)\n'
        'if __name__ == "__main__": unittest.main()\n')
    return payload


def routed_product():
    """Describe exactly the executable sample, rather than borrowing unrelated business claims."""
    from tests.test_intake import assessed_product
    value = assessed_product()
    value['goal'] = 'Provide order module version one while preserving service health'
    story = value['stories'][0]
    story.update(title='Read order module version', as_a='local module consumer',
                 want='read the order module version', so_that='I can select the expected behavior',
                 rules=['The order module version is the integer one'])
    story['acceptance'][0].update(given='the order module is available',
        when='its version constant is read', then='VALUE equals the integer one')
    value['assessment']['scope']['creates'] = ['modules/orders/service.py']
    value['assessment']['criteria'][0]['method']['expected'] = 'Order VALUE is the integer one'
    return value
