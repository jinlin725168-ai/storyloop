import unittest

from storyloop import project_agents
from storyloop.lessons import (
    next_lesson_id,
    parse_lesson,
    render_lesson,
    scope_matches,
    validate_lessons,
)
from storyloop.models import StoryLoopError


ROLES = set(project_agents.ROLES)


def sample(**overrides):
    entry = {
        "id": "L-0001",
        "summary": "一条仓库经验",
        "tier": "normal",
        "scope": ["**"],
        "roles": ["implement"],
        "strength": "prose",
        "source": "manual",
        "baseline_commit": "352630d",
        "status": "active",
        "updated_at": "2026-10-19",
        "supersedes": [],
        "landings": [],
        "body": "正文",
    }
    entry.update(overrides)
    return entry


class LessonFormatTests(unittest.TestCase):
    def assert_invalid(self, entry, field, filename="L-0001.md"):
        with self.assertRaises(StoryLoopError) as raised:
            parse_lesson(render_lesson(entry), filename)
        self.assertEqual(raised.exception.code, "LESSON_INVALID")
        self.assertIn(filename, str(raised.exception))
        self.assertIn(field, str(raised.exception))

    def test_round_trip(self):
        entry = sample(id="L-0007", scope=["backend/**"], roles=["implement"])
        self.assertEqual(parse_lesson(render_lesson(entry), "L-0007.md"), entry)

    def test_crlf_and_bom_are_accepted(self):
        text = "\ufeff" + render_lesson(sample(id="L-0001")).replace("\n", "\r\n")
        entry = parse_lesson(text, "L-0001.md")
        self.assertEqual(entry["id"], "L-0001")
        self.assertNotIn("\r", entry["body"])

    def test_id_must_match_filename(self):
        with self.assertRaisesRegex(StoryLoopError, "L-0002.md"):
            parse_lesson(render_lesson(sample(id="L-0001")), "L-0002.md")

    def test_value_must_be_json(self):
        text = render_lesson(sample(id="L-0001")).replace('"normal"', "normal")
        with self.assertRaisesRegex(StoryLoopError, "tier"):
            parse_lesson(text, "L-0001.md")

    def test_non_prose_strength_requires_landings(self):
        self.assert_invalid(sample(strength="check", landings=[]), "landings")

    def test_body_limit_is_measured_in_utf8_bytes(self):
        self.assert_invalid(sample(body="界" * 1366), "4096")

    def test_scope_rejects_inner_wildcards(self):
        self.assert_invalid(sample(scope=["src/*.py"]), "scope")

    def test_metadata_field_set_is_exact(self):
        missing = sample()
        del missing["source"]
        self.assert_invalid(missing, "source")
        self.assert_invalid(sample(extra="value"), "extra")

    def test_metadata_lines_and_json_are_strict(self):
        text = render_lesson(sample()).replace('tier: "normal"', 'tier:"normal"')
        with self.assertRaisesRegex(StoryLoopError, "tier"):
            parse_lesson(text, "L-0001.md")
        nan = render_lesson(sample()).replace('tier: "normal"', "tier: NaN")
        with self.assertRaisesRegex(StoryLoopError, "tier"):
            parse_lesson(nan, "L-0001.md")

    def test_scalar_fields_are_validated(self):
        cases = [
            (sample(id="L-123"), "id"),
            (sample(summary="two\nlines"), "summary"),
            (sample(summary="x" * 121), "summary"),
            (sample(tier="archive"), "tier"),
            (sample(strength="advice"), "strength"),
            (sample(source=""), "source"),
            (sample(baseline_commit=""), "baseline_commit"),
            (sample(status="draft"), "status"),
            (sample(updated_at="2026-02-30"), "updated_at"),
        ]
        for entry, field in cases:
            with self.subTest(field=field, value=entry[field]):
                self.assert_invalid(entry, field)

    def test_list_fields_are_nonempty_and_well_formed(self):
        cases = [
            (sample(scope=[]), "scope"),
            (sample(roles=[]), "roles"),
            (sample(roles=[1]), "roles"),
            (sample(supersedes=["bad-id"]), "supersedes"),
        ]
        for entry, field in cases:
            with self.subTest(field=field):
                self.assert_invalid(entry, field)

    def test_landing_shapes_are_validated_without_repository_access(self):
        valid = [
            {"kind": "check_command", "argv": ["python", "-m", "unittest"]},
            {"kind": "foundation", "path": "openspec/foundation/runtime.md"},
            {"kind": "principles"},
        ]
        for index, landing in enumerate(valid, 1):
            entry = sample(
                id=f"L-{index:04d}", strength="check", landings=[landing]
            )
            parsed = parse_lesson(render_lesson(entry), f"L-{index:04d}.md")
            self.assertEqual(parsed["landings"], [landing])

        invalid = [
            {"kind": "check_command", "argv": []},
            {"kind": "check_command", "argv": ["python", 1]},
            {"kind": "foundation", "path": "README.md"},
            {"kind": "principles", "path": "extra"},
            {"kind": "unknown"},
        ]
        for landing in invalid:
            with self.subTest(landing=landing):
                self.assert_invalid(
                    sample(strength="check", landings=[landing]), "landings"
                )


class LessonSetTests(unittest.TestCase):
    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(StoryLoopError, "L-0001"):
            validate_lessons([sample(id="L-0001"), sample(id="L-0001")], ROLES)

    def test_superseded_entry_must_exist_and_be_retired(self):
        with self.assertRaisesRegex(StoryLoopError, "supersedes"):
            validate_lessons(
                [sample(id="L-0001"), sample(id="L-0002", supersedes=["L-0001"])],
                ROLES,
            )
        with self.assertRaisesRegex(StoryLoopError, "supersedes"):
            validate_lessons(
                [sample(id="L-0002", supersedes=["L-0009"])], ROLES
            )

    def test_core_total_limit_counts_only_active_entries(self):
        core = [
            sample(id=f"L-000{i}", tier="core", body="x" * 1500)
            for i in range(1, 4)
        ]
        with self.assertRaisesRegex(StoryLoopError, "4096"):
            validate_lessons(core, ROLES)
        core[-1]["status"] = "retired"
        validate_lessons(core, ROLES)

    def test_unknown_role_rejected(self):
        with self.assertRaisesRegex(StoryLoopError, "roles"):
            validate_lessons([sample(id="L-0001", roles=["janitor"])], ROLES)

    def test_scope_matching(self):
        self.assertTrue(scope_matches(["backend/**"], "backend/app/x.py"))
        self.assertTrue(scope_matches(["backend/**"], "backend"))
        self.assertFalse(scope_matches(["backend/**"], "backend2/x.py"))
        self.assertTrue(scope_matches(["**"], "anything/at/all"))
        self.assertTrue(scope_matches(["README.md"], "README.md"))

    def test_next_id(self):
        self.assertEqual(next_lesson_id([]), "L-0001")
        self.assertEqual(
            next_lesson_id([sample(id="L-0009"), sample(id="L-0012")]),
            "L-0013",
        )


if __name__ == "__main__":
    unittest.main()
