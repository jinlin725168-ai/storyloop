"""Executable architecture declarations and guards, including hostile synthetic inputs."""

from pathlib import Path, PureWindowsPath
import tempfile
import unittest

from tests import architecture_rules
from tests.architecture_rules import (
    DECLARATION, PACKAGE, ArchModule, Declaration, Entry, classify, code_modules, load_declaration,
    Exemption, Violation, cycle_members, import_graph, module_name, stale_entries,
    stale_exemptions, undeclared, undeclared_message, unexempted, violations,
    MODULES_MD, modules_md_rows,
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


class LayeringRuleTests(unittest.TestCase):
    def test_base_cycle_graph_excludes_mutable_detours_but_layering_still_rejects_them(self):
        architecture = {"root": ArchModule("root", "基础", 0)}
        entries = {name: Entry(name, "root", "mutable" if name == "m" else "base", None)
                   for name in ("a", "b", "m")}
        decl = Declaration(architecture, entries, (), frozenset(), {})
        graph = {"a": {"b", "m"}, "b": {"a"}, "m": {"a"}}
        self.assertTrue(hasattr(architecture_rules, "base_graph"))
        self.assertEqual(architecture_rules.base_graph(decl, graph),
                         {"a": {"b"}, "b": {"a"}})
        self.assertEqual(cycle_members(architecture_rules.base_graph(decl, graph)), {"a", "b"})
        self.assertEqual([(v.source, v.target, v.rules) for v in violations(decl, graph)],
                         [("a", "m", ("base_imports_mutable",))])

    def test_every_import_form_is_counted(self):
        sources = {
            "top.py": ("from .low import f\nimport storyloop.other as o\n"
                       "def g():\n    from . import lazy\n"
                       "if TYPE_CHECKING:\n    from .typed import T\n"
                       "import pathlib\nfrom elsewhere import ignored\nfrom . import top\n"),
            "sub/__init__.py": "from . import leaf\n",
            "sub/leaf.py": "from ..models import x\n",
        }
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / "storyloop"
            for name in [*sources, "low.py", "other.py", "lazy.py", "typed.py", "models.py"]:
                path = package / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(sources.get(name, ""), encoding="utf-8")
            graph = import_graph(package)
        self.assertEqual(graph["top"], {"low", "other", "lazy", "typed"})
        self.assertEqual(graph["sub.leaf"], {"models"})
        self.assertEqual(graph["sub"], {"sub.leaf"})

    def test_from_package_import_resolves_submodules_before_attributes(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / "storyloop"
            (package / "sub").mkdir(parents=True)
            (package / "sub/__init__.py").write_text("", encoding="utf-8")
            (package / "sub/leaf.py").write_text("", encoding="utf-8")
            (package / "top.py").write_text(
                "from storyloop.sub import leaf, constant\nfrom storyloop import top\n",
                encoding="utf-8")
            self.assertEqual(import_graph(package)["top"], {"sub", "sub.leaf"})

    def test_upward_and_base_to_mutable_edges_are_violations(self):
        decl = declaration({"f": "m1", "c": "m3", "g": "m2", "r": "m3",
                            "lo": "m1", "hi": "m2"})
        entries = {n: Entry(n, e.module, "base" if n in {"f", "g"} else "mutable", None)
                   for n, e in decl.modules.items()}
        decl = Declaration(decl.architecture, entries, (), frozenset(), {})
        graph = {"f": {"c"}, "g": {"r"}, "lo": {"hi"}, "c": {"f"}}
        found = violations(decl, graph)
        self.assertEqual([(v.source, v.target, v.rules) for v in found], [
            ("f", "c", ("upward", "base_imports_mutable")),
            ("g", "r", ("upward", "base_imports_mutable")),
            ("lo", "hi", ("upward",)),
        ])

    def test_same_rank_base_to_mutable_is_still_a_violation(self):
        decl = declaration({"x": "m1", "y": "m1"})
        entries = {"x": decl.modules["x"], "y": Entry("y", "m1", "mutable", None)}
        decl = Declaration(decl.architecture, entries, (), frozenset(), {})
        self.assertEqual(violations(decl, {"x": {"y"}}),
                         [Violation("x", "y", ("base_imports_mutable",))])

    def test_unexempted_and_stale_exemptions(self):
        decl = Declaration({}, {}, (Exemption("a", "b", "D", "拆分"),
                                    Exemption("e", "f", "B", "迁移")), frozenset(), {})
        found = [Violation("a", "b", ("upward",)), Violation("c", "d", ("upward",))]
        self.assertEqual([(v.source, v.target) for v in unexempted(decl, found)], [("c", "d")])
        self.assertEqual([(e.source, e.target) for e in stale_exemptions(decl, found)], [("e", "f")])

    def test_cycle_members(self):
        graph = {"a": {"b"}, "b": {"a"}, "c": {"a"}, "d": set(), "self": {"self"}}
        self.assertEqual(cycle_members(graph), {"a", "b"})

    def test_deep_graph_does_not_exhaust_the_default_recursion_limit(self):
        graph = {str(n): {str(n + 1)} for n in range(1500)}
        graph["1500"] = {"1499"}
        self.assertEqual(cycle_members(graph), {"1499", "1500"})


class LayeringTests(unittest.TestCase):
    def test_entry_files_stay_below_450_lines(self):
        for name in ("cli.py", "project_loop.py"):
            with self.subTest(name=name):
                self.assertLess(len((PACKAGE / name).read_text(encoding="utf-8").splitlines()), 450)

    def test_stage_d_has_no_exemptions_violations_or_base_cycles(self):
        graph = import_graph(PACKAGE)
        self.assertEqual(DECL.exemptions, ())
        self.assertEqual(violations(DECL, graph), [])
        self.assertEqual(cycle_members(architecture_rules.base_graph(DECL, graph)), set())

    def test_agent_transport_has_no_business_context_imports(self):
        graph = import_graph(PACKAGE)
        self.assertTrue("agent_transport" in graph, "缺少代理传输模块")
        forbidden = {"adapter_profiles", "call_context", "call_log", "cost_limits", "cross_vendor",
                     "errata", "foundation_survey", "lesson_retrieval", "role_instructions",
                     "role_profiles", "role_subagents", "skills", "telemetry"}
        self.assertEqual(sorted(graph["agent_transport"] & forbidden), [])

    def test_capability_protocol_does_not_depend_on_role_policy(self):
        graph = import_graph(PACKAGE)
        self.assertTrue("capability_protocol" in graph, "缺少能力协议模块")
        self.assertEqual(sorted(graph["capability_protocol"]
                                & {"cross_vendor", "role_profiles", "role_subagents"}), [])
        self.assertNotIn("adapter_capabilities", graph["role_schemas"])

    def test_evidence_kernels_receive_business_facts_from_callers(self):
        graph = import_graph(PACKAGE)
        forbidden = {"baseline": {"adapter_capabilities", "system_smoke"},
                     "project_boundaries": {"system_smoke"},
                     "smoke_runner": {"system_smoke"},
                     "project_loop_guards": {"errata"}}
        self.assertEqual({source: sorted(graph[source] & targets)
                          for source, targets in forbidden.items()},
                         {source: [] for source in forbidden})

    def test_git_and_evidence_kernels_do_not_import_upper_owners(self):
        graph = import_graph(PACKAGE)
        forbidden = {"project_git": {"baseline"},
                     "evidence_locations": {"failure_attribution"},
                     "project_receipt": {"change_report"}}
        self.assertEqual({source: sorted(graph[source] & targets)
                          for source, targets in forbidden.items()},
                         {source: [] for source in forbidden})

    def test_product_and_smoke_bases_do_not_read_mutable_contract_owners(self):
        graph = import_graph(PACKAGE)
        forbidden = {"models": {"assessment"}, "smoke": {"changes"},
                     "smoke_runner": {"changes"}}
        self.assertEqual({source: sorted(graph[source] & targets)
                          for source, targets in forbidden.items()},
                         {source: [] for source in forbidden})

    def test_contract_format_consumers_do_not_import_mutable_owners(self):
        graph = import_graph(PACKAGE)
        old_edges = {
            "role_schemas": {"lessons", "routing_policy"},
            "project_profile": {"oracle_counts"},
            "onboarding_manifest": {"onboarding_templates"},
        }
        self.assertEqual({source: sorted(graph[source] & targets) for source, targets in old_edges.items()},
                         {source: [] for source in old_edges})

    def test_no_unexempted_violations(self):
        found = violations(DECL, import_graph(PACKAGE))
        self.assertEqual(unexempted(DECL, found), [],
                         "新违规：改代码，不要加豁免（设计 §2 反目标）")

    def test_no_stale_exemptions(self):
        stale = stale_exemptions(DECL, violations(DECL, import_graph(PACKAGE)))
        self.assertEqual(stale, [], "这些依赖已消除，从 storyloop/architecture.toml 删掉对应豁免")

    def test_no_new_module_joins_a_cycle(self):
        members = cycle_members(import_graph(PACKAGE))
        self.assertEqual(sorted(members - DECL.cycle_members), [], "新增了依赖环")

    def test_no_stale_cycle_members(self):
        members = cycle_members(import_graph(PACKAGE))
        self.assertEqual(sorted(DECL.cycle_members - members), [], "这些模块已不在环里，从 [cycles] 删掉")


class ModulesDocumentationTests(unittest.TestCase):
    def test_rows_with_several_modules_dotted_names_and_blank_layer(self):
        text = ("| 架构模块 | 模块 | 分区 | 职责 | 对外提供 |\n|---|---|---|---|---|\n"
                "| 基础 | `a` / `b` | 底座 | x | y |\n| | `testing.smoke_harness` | 可变区 | x | y |\n")
        self.assertEqual(modules_md_rows(text), {
            "a": ("基础", "底座"), "b": ("基础", "底座"),
            "testing.smoke_harness": ("基础", "可变区")})

    def test_a_module_listed_twice_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "a"):
            modules_md_rows("| 基础 | `a` | 底座 | x | y |\n| 合同 | `a` | 可变区 | x | y |\n")

    def test_modules_md_lists_every_module_under_its_declared_title(self):
        rows = modules_md_rows(MODULES_MD.read_text(encoding="utf-8"))
        names = {name for name in code_modules(PACKAGE) if not name.startswith("testing.smoke_harness.")}
        names.add("testing.smoke_harness")
        self.assertEqual(sorted(names - set(rows)), [], "docs/modules.md 漏登记的模块")
        self.assertEqual(sorted(set(rows) - names), [], "docs/modules.md 登记了不存在的模块")
        zones = {"base": "底座", "mutable": "可变区"}
        expected = {name: (DECL.architecture[classify(DECL, name).module].title,
                           zones[classify(DECL, name).zone]) for name in rows}
        self.assertEqual({name: row for name, row in rows.items() if row != expected[name]}, {},
                         "第一列应为架构模块标题，分区列应与声明一致")
