"""The nested-project kit lets an acceptance suite drive real runs with scripted roles (C4).

2026-11-14: changes whose acceptance tests had to simulate an agent role were excluded from
self-iteration, because each suite had to build its own nested StoryLoop project and these
fixtures were "very hard to get right". The kit ships that project and valid role answers.
"""

import ast
from pathlib import Path
import tempfile
import unittest


class NestedKitTests(unittest.TestCase):
    def test_a_standard_case_reaches_passed_with_the_default_roles(self):
        from storyloop.testing.nested import nested_case
        with tempfile.TemporaryDirectory() as folder:
            case = nested_case(Path(folder), 'standard')
            result = case.run()
            self.assertEqual(result['status'], 'PASSED', result)
            self.assertEqual(case.state()['attempt'], 1)
            self.assertEqual(case.state()['routing']['effective_tier'], 'standard')

    def test_one_role_can_be_replaced_to_drive_a_mechanism(self):
        from storyloop.testing.nested import nested_case, passing_review
        with tempfile.TemporaryDirectory() as folder:
            reviews = []

            def review(project, context):
                reviews.append(context.get('mode'))
                if len(reviews) == 1:
                    return {'verdict': 'fail', 'summary': 'needs work', 'questions': [], 'findings': [{
                        'id': 'R-1', 'criterion_id': 'AC-1', 'target': 'implementation',
                        'disposition': 'MUST_FIX', 'blocking': True,
                        'location': 'modules/orders/service.py:1', 'evidence': 'VALUE is set',
                        'message': 'Document VALUE', 'required_change': 'Add a comment'}]}
                return passing_review(project, context)

            case = nested_case(Path(folder), 'standard', review=review)
            self.assertEqual(case.run()['status'], 'PASSED')
            self.assertEqual(case.state()['attempt'], 2)
            self.assertGreaterEqual(len(reviews), 2)

    def test_the_kit_needs_nothing_from_the_repository_tests(self):
        source = (Path(__file__).resolve().parents[1] / 'storyloop/testing/nested.py').read_text(encoding='utf-8')
        imported = {node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)}
        imported |= {alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)
                     for alias in node.names}
        self.assertFalse({name for name in imported if name and name.startswith('tests')})


if __name__ == '__main__':
    unittest.main()
