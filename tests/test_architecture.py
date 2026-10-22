"""Executable architecture declarations and guards, including hostile synthetic inputs."""

from pathlib import Path, PureWindowsPath
import tempfile
import unittest

from tests.architecture_rules import (
    DECLARATION, PACKAGE, ArchModule, Declaration, Entry, classify, code_modules, load_declaration,
    module_name, stale_entries, undeclared, undeclared_message,
)

DECL = load_declaration(DECLARATION)


def declaration(entries):
    architecture = {f"m{n}": ArchModule(f"m{n}", f"模块{n}", n) for n in range(1, 4)}
    modules = {name: Entry(name, owner, "base", None) for name, owner in entries.items()}
    return Declaration(architecture, modules, (), frozenset(), {})


class DeclarationRuleTests(unittest.TestCase):
    def package(self, names):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "storyloop"
        root.mkdir()
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        return root

    def test_an_undeclared_module_is_reported(self):
        package = self.package(["a.py", "b.py"])
        self.assertEqual(undeclared(declaration({"a": "m1"}), code_modules(package)), ["b"])

    def test_failure_message_names_the_module_and_the_file(self):
        message = undeclared_message(["b"])
        self.assertIn("b", message)
        self.assertIn("storyloop/architecture.toml", message)

    def test_a_stale_entry_and_an_unmatched_wildcard_are_reported(self):
        decl = declaration({"a": "m1", "gone": "m1", "ghost.*": "m1"})
        self.assertEqual(stale_entries(decl, ["a"]), ["ghost.*", "gone"])

    def test_exact_entry_wins_over_wildcard_and_longest_wildcard_wins(self):
        decl = declaration({"pkg.*": "m1", "pkg.sub.*": "m2", "pkg.sub.x": "m3"})
        for name, owner in [("pkg.sub.x", "m3"), ("pkg.sub.y", "m2"),
                            ("pkg.z", "m1"), ("pkg.sub", "m2")]:
            with self.subTest(name=name):
                self.assertEqual(classify(decl, name).module, owner)
        with self.assertRaises(KeyError):
            classify(decl, "pkg_other")

    def test_nested_package_names_use_dots_on_every_platform(self):
        package = self.package(["sub/__init__.py", "sub/leaf.py", "__main__.py", "__init__.py"])
        self.assertEqual(sorted(code_modules(package)), ["__main__", "sub", "sub.leaf"])
        self.assertEqual(module_name(PureWindowsPath("sub\\leaf.py")), "sub.leaf")

    def test_invalid_declarations_are_rejected(self):
        valid = ('version = 1\n[architecture.m1]\ntitle = "基础"\nrank = 0\n'
                 '[modules]\na = { module = "m1", zone = "base" }\n')
        cases = [
            (valid.replace('version = 1', 'version = 2'), "version"),
            (valid.replace('module = "m1"', 'module = "missing"'), "module"),
            (valid.replace('zone = "base"', 'zone = "core"'), "zone"),
            (valid.replace('zone = "base"', 'zone = "base", split = "E"'), "split"),
            (valid + '[[exemptions]]\nsource="a"\ntarget="b"\nuntil="A"\nreason="later"\n', "until"),
            (valid + '[[exemptions]]\nsource="a"\ntarget="b"\nuntil="D"\nreason=""\n', "reason"),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "architecture.toml"
            for text, key in cases:
                with self.subTest(key=key):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, key):
                        load_declaration(path)

    def test_optional_sections_default_to_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "architecture.toml"
            path.write_text("version=1\n", encoding="utf-8")
            decl = load_declaration(path)
        self.assertEqual(decl.exemptions, ())
        self.assertEqual(decl.cycle_members, frozenset())
        self.assertEqual(decl.invariants, {})


class DeclarationTests(unittest.TestCase):
    def test_every_code_module_is_declared(self):
        missing = undeclared(DECL, code_modules(PACKAGE))
        self.assertEqual(missing, [], undeclared_message(missing))

    def test_no_declaration_entry_is_stale(self):
        self.assertEqual(stale_entries(DECL, code_modules(PACKAGE)), [])

    def test_the_current_package_has_146_modules(self):
        self.assertEqual(len(code_modules(PACKAGE)), 146)
