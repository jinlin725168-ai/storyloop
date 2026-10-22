"""Lifecycle architecture constraints, proved against real and synthetic violations."""
import unittest
from tests.architecture_rules import PACKAGE, import_graph


class LifecycleDependenciesTests(unittest.TestCase):
    def test_stage_b_dependencies_are_gone(self):
        graph = import_graph(PACKAGE)
        for source, target in (("acceptance_repair", "project_loop"),
                               ("project_loop_gates", "project_loop"),
                               ("project_loop_support", "next_steps"),
                               ("project_loop_support", "stop_report"),
                               ("change_log", "next_steps"), ("supersede", "next_steps")):
            with self.subTest(source=source, target=target):
                self.assertNotIn(target, graph[source])
