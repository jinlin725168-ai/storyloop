"""Create authoritative local verification evidence without completing delivery."""
from copy import deepcopy
import sys
from unittest.mock import patch
from storyloop.baseline import load_project_config
from storyloop.changes import load_change
from storyloop.project_loop import load_run_state, run_change
from storyloop.storage import write_json
from tests.lifecycle_cases import approved_case, observed_services


class CandidateReady(Exception):
    pass


def verified_candidate(root, manual=False):
    identifier = approved_case(root, 'manual' if manual else 'verification')
    config = load_project_config(root)
    config['profile']['unit_commands'] = [[sys.executable, '-c',
                                          "print('Ran 1 tests in 0.001s'); print('OK')"]]
    config['profile'].pop('test_commands', None)
    write_json(root / '.storyloop/config.json', config)
    captured = {}
    calls = []
    def capture(project, change, state, **verified):
        captured.update(change=deepcopy(change), state=deepcopy(state), context=deepcopy(verified))
        raise CandidateReady()
    with patch('storyloop.project_loop.complete_change', side_effect=capture):
        try:
            result = run_change(root, identifier, observed_services(identifier, 'verification', calls))
        except CandidateReady:
            return captured
    assert manual and result['status'] == 'MANUAL_ACCEPTANCE_REQUIRED', result
    state = load_run_state(root, identifier)
    return {'change': load_change(root, identifier), 'state': state, 'context': state['verified']}
