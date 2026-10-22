"""Static checks track run records, aliases and copies rather than matching a spelling."""
import unittest
from tests.lifecycle_rules import status_writes


class LifecycleRulesTests(unittest.TestCase):
    def test_each_top_level_write_and_reconstruction_is_detected(self):
        operations = ["record['status'] = 'PASSED'", "alias = record; alias['status'] = 'PASSED'",
                      "record.update({'status': 'PASSED'})", "record.update(status='PASSED')",
                      "payload = {'status': 'PASSED'}; record.update(**payload)",
                      "record |= {'status': 'PASSED'}", "del record['status']",
                      "record.pop('status')", "record.setdefault('status', 'PASSED')",
                      "copy = {**record, 'status': 'PASSED'}; save_run(project, copy)",
                      "copy = dict(record, status='PASSED'); save_run(project, copy)"]
        for operation in operations:
            source = "def broken(project):\n    record = load_json(run_path(project, 'id'))\n    " + operation
            with self.subTest(operation=operation):
                self.assertTrue(status_writes(source, "business"))

    def test_load_producers_save_arguments_and_initial_constructor_are_tracked(self):
        for source in (
            "def f(project):\n    x = load_run_state(project, 'id')\n    x['status'] = 'PASSED'",
            "def f(project):\n    x = {'status': 'PASSED'}\n    save_run(project, x)",
            "def _new_run_state():\n    return {'status': 'PREPARING'}",
            "def f(project):\n    x = load_json(project / '.storyloop/state/runs/a.json')\n    x.update(status='PASSED')",
            "def f(project):\n    write_json(run_path(project, 'id'), {'status': 'PASSED'})",
            "def f(project):\n    path = project / '.storyloop/state/runs/a.json'\n    write_json(path, payload)",
        ):
            with self.subTest(source=source):
                self.assertTrue(status_writes(source, 'business'))

    def test_nested_status_and_presentation_are_not_run_writes(self):
        source = """def f(project, state):
    state['planning']['status'] = 'DONE'
    boundary = {'status': 'PASSED'}
    boundary['status'] = 'FAILED'
    return {'status': state['status'], 'boundary': boundary}
"""
        self.assertEqual(status_writes(source, 'business'), [])

    def test_only_the_kernel_assignment_and_contract_owned_functions_are_excluded(self):
        self.assertEqual(status_writes("def transition(project, state):\n    state['status'] = target",
                                       'run_transitions'), [])
        self.assertTrue(status_writes("def another(project, state):\n    state['status'] = target",
                                      'run_transitions'))
        self.assertTrue(status_writes("def rogue(project, state):\n    state['status'] = 'PASSED'", 'changes'))

    def test_reading_a_run_does_not_taint_a_new_report_or_substage_record(self):
        source = """def f(project, state, run):
    response = review(project, state)
    record = {**response, 'status': 'COHERENT'}
    state['planning']['consistency'] = record
    pairs = {'status': nested(run, 'status')}
    return pairs
"""
        self.assertEqual(status_writes(source, 'business'), [])

    def test_copies_passed_directly_to_save_and_local_path_parameters_are_tracked(self):
        for source in (
            "def f(project, state):\n    save_run(project, {**state, 'status': 'PASSED'})",
            "def f(project):\n    path = run_path(project, 'id')\n    helper(project, path)\ndef helper(project, path):\n    record = load_json(path)\n    record['status'] = 'PASSED'",
        ):
            with self.subTest(source=source):
                self.assertTrue(status_writes(source, 'business'))

    def test_dictionary_returned_to_a_run_saver_is_tracked(self):
        source = """def forged(record):
    return {**record, 'status': 'PASSED'}
def f(project):
    original = load_run_state(project, 'id')
    copy = forged(original)
    save_run(project, copy)
"""
        self.assertTrue(status_writes(source, 'business'))

    def test_saver_alias_dict_position_and_keyword_write_are_detected(self):
        for source in (
            "def f(project):\n    record = {}\n    alias = record\n    record['status'] = 'PASSED'\n    save_run(project, alias)",
            "def f(project):\n    record = load_run_state(project, 'id')\n    record.update(dict({'status': 'PASSED'}))",
            "def f(project):\n    write_json(path=run_path(project, 'id'), value=record)",
        ):
            with self.subTest(source=source):
                self.assertTrue(status_writes(source, 'business'))

    def test_nested_function_locals_and_known_substate_arguments_are_separate(self):
        for source in (
            "def f(project, state):\n    def view():\n        state = {}\n        state['status'] = 'RENDERED'\n        return state",
            "def report(state):\n    state['status'] = 'RENDERED'\ndef f(project, run):\n    report(run['planning'])",
        ):
            with self.subTest(source=source):
                self.assertEqual(status_writes(source, 'business'), [])
