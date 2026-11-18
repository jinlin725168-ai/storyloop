"""The capability coverage manifest stays complete and truthful (roadmap D1, 2026-11-17).

The first audit found three stop types no test asserts (EXPLORATION_RECOMMENDED appears in every
real L3 run) and commands without any error-path test; they are listed as GAPS until closed.
"""
import argparse
import ast
from pathlib import Path
import re
import unittest

from storyloop.cli import parser
from storyloop.intervention_events import STOP_CLASSES
from tests.coverage_manifest import COMMANDS, GAPS, STOPS

ROOT = Path(__file__).resolve().parents[1]


def _commands() -> set[str]:
    actions = [a for a in parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return set(actions[0].choices)


def _test_body(test_id: str) -> str | None:
    module, cls, name = test_id.rsplit(".", 2)
    path = ROOT / (module.replace(".", "/") + ".py")
    if not path.is_file():
        return None
    source = path.read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return ast.get_source_segment(source, item)
    return None


def _mentions_command(body: str, command: str, symbols: list[str]) -> bool:
    quoted = re.escape(command)
    cli = (rf"(?:parse_args|main|dispatch)\(\s*\[\s*['\"]{quoted}['\"]"
           rf"|['\"](?:storyloop|-m['\"],\s*['\"]storyloop)['\"],\s*['\"]{quoted}['\"]")
    return bool(re.search(cli, body)) or any(re.search(rf"\b{re.escape(s)}\b", body) for s in symbols)


class CoverageManifestTests(unittest.TestCase):
    def test_manifest_lists_exactly_the_public_commands_and_stop_types(self):
        stops = {status for members in STOP_CLASSES.values() for status in members}
        gap_stops = {name for kind, name in GAPS if kind == "stop"}
        self.assertEqual(set(COMMANDS), _commands())
        self.assertEqual(set(STOPS) | gap_stops, stops)
        self.assertFalse(set(STOPS) & gap_stops, "a stop is both covered and declared a gap")

    def test_every_command_has_positive_and_boundary_evidence_or_a_declared_gap(self):
        for command, entry in COMMANDS.items():
            for kind in ("positive", "boundary"):
                with self.subTest(command=command, kind=kind):
                    gap = ("command-" + kind, command) in GAPS
                    self.assertNotEqual(bool(entry.get(kind)), gap,
                                        "name a test or declare a gap, not both or neither")

    def test_named_tests_exist_and_exercise_what_they_cover(self):
        for command, entry in COMMANDS.items():
            for kind in ("positive", "boundary"):
                if entry.get(kind):
                    with self.subTest(command=command, kind=kind):
                        body = _test_body(entry[kind])
                        self.assertIsNotNone(body, f"missing test {entry[kind]}")
                        self.assertTrue(_mentions_command(body, command, entry["symbols"]),
                                        f"{entry[kind]} does not exercise {command}")
        for status, test_id in STOPS.items():
            with self.subTest(stop=status):
                body = _test_body(test_id)
                self.assertIsNotNone(body, f"missing test {test_id}")
                self.assertRegex(body, rf"['\"]{status}['\"]")

    def test_every_gap_says_why(self):
        for key, reason in GAPS.items():
            with self.subTest(gap=key):
                self.assertIn(key[0], {"command-positive", "command-boundary", "stop"})
                self.assertTrue(isinstance(reason, str) and reason.strip())


if __name__ == "__main__":
    unittest.main()
