from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tests.lesson_support import cleanup_repo
from storyloop.lesson_store import (
    confirm_lesson, effective_status, lesson_source_commit, lessons_digest, list_lessons,
    load_lessons_at, retire_lesson, stale_ids,
)
from storyloop.models import StoryLoopError
from storyloop.baseline import _git as real_git
from storyloop.storage import write_json
from tests.lesson_support import commit_all, commit_file, git_repo, head, sample, write_lesson


class LessonStoreTests(unittest.TestCase):
    def test_list_marks_stale_and_reads_recurrences(self):
        repo = git_repo(files={"app/x.py": "old"})
        self.addCleanup(cleanup_repo, repo)
        entry = sample(scope=["app/**"], baseline_commit=head(repo))
        write_lesson(repo, entry)
        commit_all(repo, "add lesson")
        commit_file(repo, "app/x.py", "changed")
        write_json(repo / ".storyloop/state/lessons.json", {
            "L-0001": {"recurrences": ["FEAT-1", "FEAT-2"]},
        })

        result = list_lessons(repo)

        self.assertEqual(result["lessons"][0]["effective_status"], "needs-review")
        self.assertEqual(result["lessons"][0]["recurrences"], 2)

    def test_vector_rate_uses_latest_thirty_change_records_and_strict_boundaries(self):
        repo = git_repo(lessons=[sample()])
        self.addCleanup(cleanup_repo, repo)
        runs = repo / ".storyloop/state/runs"
        for index in range(31):
            write_json(runs / f"FEAT-{index:02d}.json", {
                "change_id": f"FEAT-{index:02d}",
                "failures": ([{"attempt": 1}] if index < 10 else []),
            })
            write_json(repo / f".storyloop/state/telemetry/FEAT-{index:02d}.json", {
                "version": 1, "change_id": f"FEAT-{index:02d}",
                "entries": [{"role": "implement", "lessons_provided": ["L-0001"]}],
            })

        signal = list_lessons(repo)["vector_signal"]

        self.assertEqual(signal["recurrence_rate"], 9 / 30)
        self.assertFalse(signal["reevaluate"])
        write_json(runs / "FEAT-30.json", {
            "change_id": "FEAT-30", "failures": [{"attempt": 1}],
        })
        self.assertTrue(list_lessons(repo)["vector_signal"]["reevaluate"])

    def test_vector_rate_is_none_without_provided_lesson_records(self):
        repo = git_repo(lessons=[sample()])
        self.addCleanup(cleanup_repo, repo)
        write_json(repo / ".storyloop/state/runs/FEAT-1.json", {"failures": [{}]})
        write_json(repo / ".storyloop/state/telemetry/FEAT-1.json", {
            "version": 1, "change_id": "FEAT-1", "entries": [{"role": "implement"}],
        })
        self.assertIsNone(list_lessons(repo)["vector_signal"]["recurrence_rate"])

    def test_vector_signal_count_must_exceed_two_hundred(self):
        repo = git_repo()
        self.addCleanup(cleanup_repo, repo)
        entries = [sample(id=f"L-{index:04d}") for index in range(1, 202)]
        with patch("storyloop.lesson_store.load_lessons_at", return_value=entries[:200]), \
                patch("storyloop.lesson_store.stale_ids", return_value=set()):
            self.assertFalse(list_lessons(repo)["vector_signal"]["reevaluate"])
        with patch("storyloop.lesson_store.load_lessons_at", return_value=entries), \
                patch("storyloop.lesson_store.stale_ids", return_value=set()):
            self.assertTrue(list_lessons(repo)["vector_signal"]["reevaluate"])

    def test_confirm_moves_baseline_and_commits(self):
        repo = git_repo(lessons=[sample()])
        self.addCleanup(cleanup_repo, repo)
        before = head(repo)

        result = confirm_lesson(repo, "L-0001")

        entry = load_lessons_at(repo, head(repo))[0]
        self.assertEqual(entry["baseline_commit"], before)
        self.assertEqual(entry["status"], "active")
        self.assertEqual(result["status"], "COMMITTED")
        self.assertEqual(
            subprocess.run(["git", "log", "-1", "--format=%s"], cwd=repo,
                           capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip(),
            "chore(lessons): 确认 L-0001 仍然有效",
        )

    def test_confirm_refuses_retired_without_writes(self):
        repo = git_repo(lessons=[sample(status='retired')])
        self.addCleanup(cleanup_repo, repo)
        before = head(repo)
        target = repo / 'openspec/lessons/L-0001.md'
        original = target.read_bytes()
        with self.assertRaisesRegex(StoryLoopError, 'retired'):
            confirm_lesson(repo, 'L-0001')
        self.assertEqual(head(repo), before)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(real_git(repo, 'status', '--porcelain'), '')

    def test_confirm_has_no_fallible_enrichment_after_commit(self):
        repo = git_repo(lessons=[sample()])
        self.addCleanup(cleanup_repo, repo)
        before = head(repo)

        def fail_query_after_commit(project, *args):
            enrichment = args == ("branch", "--show-current") or args == ("rev-parse", "HEAD")
            if enrichment and real_git(project, "rev-parse", "HEAD").strip() != before:
                raise StoryLoopError("simulated post-commit query failure")
            return real_git(project, *args)

        with patch("storyloop.project_git._git", side_effect=fail_query_after_commit):
            result = confirm_lesson(repo, "L-0001")

        self.assertEqual(result["status"], "COMMITTED")
        self.assertNotEqual(head(repo), before)
        self.assertEqual(load_lessons_at(repo, head(repo))[0]["baseline_commit"], before)

    def test_retire_rejects_oversized_body_without_writes(self):
        for body, reason in [('x' * 4090, '结束'), ('正文', '界' * 1400)]:
            with self.subTest(body_bytes=len(body.encode())):
                repo = git_repo(lessons=[sample(body=body)])
                self.addCleanup(cleanup_repo, repo)
                before = head(repo)
                target = repo / 'openspec/lessons/L-0001.md'
                original = target.read_bytes()
                with self.assertRaisesRegex(StoryLoopError, '4096'):
                    retire_lesson(repo, 'L-0001', reason)
                self.assertEqual(head(repo), before)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(real_git(repo, 'status', '--porcelain'), '')

    def test_retire_requires_reason_and_appends_it(self):
        repo = git_repo(lessons=[sample(body="保留正文\n")])
        self.addCleanup(cleanup_repo, repo)
        with self.assertRaises(StoryLoopError):
            retire_lesson(repo, "L-0001", "")

        retire_lesson(repo, "L-0001", "接口已删除")

        entry = load_lessons_at(repo, head(repo))[0]
        self.assertEqual(entry["status"], "retired")
        self.assertIn("\n\n退休：", entry["body"])
        self.assertIn("接口已删除", entry["body"])

    def test_mutations_refuse_feature_dirty_and_active_run(self):
        cases = ("feature", "dirty", "active")
        for case in cases:
            with self.subTest(case=case):
                repo = git_repo(lessons=[sample()])
                self.addCleanup(cleanup_repo, repo)
                if case == "feature":
                    subprocess.run(["git", "switch", "-q", "-c", "feature/FEAT-1"],
                                   cwd=repo, check=True)
                    expected = "feature/"
                elif case == "dirty":
                    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
                    expected = "干净"
                else:
                    write_json(repo / ".storyloop/state/runs/FEAT-1.json",
                               {"status": "IMPLEMENTING"})
                    expected = "进行中"
                with self.assertRaisesRegex(StoryLoopError, expected):
                    confirm_lesson(repo, "L-0001")

    def test_missing_directory_is_empty(self):
        repo = git_repo()
        self.addCleanup(cleanup_repo, repo)
        self.assertEqual(load_lessons_at(repo, head(repo)), [])
        self.assertIsNone(lessons_digest(repo, head(repo)))

    def test_lessons_are_read_from_contract_commit_not_worktree(self):
        repo = git_repo(lessons=[sample(summary="旧")])
        self.addCleanup(cleanup_repo, repo)
        contract = head(repo)
        write_lesson(repo, sample(summary="新"))
        commit_all(repo, "later")
        self.assertEqual(load_lessons_at(repo, contract)[0]["summary"], "旧")

    def test_invalid_committed_entry_raises(self):
        repo = git_repo(raw_lessons={"L-0001.md": "---\nid: L-0001\n---\n"})
        self.addCleanup(cleanup_repo, repo)
        with self.assertRaises(StoryLoopError) as caught:
            load_lessons_at(repo, head(repo))
        self.assertEqual(caught.exception.code, "LESSON_INVALID")

    def test_rejects_committed_symlink(self):
        repo = git_repo()
        self.addCleanup(cleanup_repo, repo)
        target = repo / "openspec/lessons/L-0001.md"
        target.parent.mkdir(parents=True)
        target.symlink_to("../../README.md")
        commit_all(repo, "unsafe lesson")
        with self.assertRaises(StoryLoopError) as caught:
            load_lessons_at(repo, head(repo))
        self.assertEqual(caught.exception.code, "LESSON_INVALID")

    def test_foundation_landing_must_exist_at_same_commit(self):
        entry = sample(strength="knowledge", landings=[{
            "kind": "foundation", "path": "openspec/foundation/runtime.md",
        }])
        repo = git_repo(lessons=[entry])
        self.addCleanup(cleanup_repo, repo)
        frozen = head(repo)
        commit_file(repo, "openspec/foundation/runtime.md", "# Runtime\n")
        with self.assertRaises(StoryLoopError) as caught:
            load_lessons_at(repo, frozen)
        self.assertEqual(caught.exception.code, "LESSON_INVALID")
        self.assertEqual(load_lessons_at(repo, head(repo))[0]["id"], "L-0001")

    def test_change_inside_scope_after_baseline_is_stale(self):
        repo = git_repo(files={"backend/a.py": "1"})
        self.addCleanup(cleanup_repo, repo)
        entry = sample(scope=["backend/**"], baseline_commit=head(repo))
        commit_file(repo, "frontend/b.js", "x")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), set())
        commit_file(repo, "backend/a.py", "2")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), {"L-0001"})

    def test_global_scope_is_not_narrowed_by_an_additional_scope(self):
        repo = git_repo(files={"backend/a.py": "1"})
        self.addCleanup(cleanup_repo, repo)
        entry = sample(scope=["**", "backend/**"], baseline_commit=head(repo))
        commit_file(repo, "frontend/b.js", "changed")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), {"L-0001"})

    def test_backslash_scope_is_normalized_before_git_matching(self):
        repo = git_repo(files={"backend/a.py": "1"})
        self.addCleanup(cleanup_repo, repo)
        entry = sample(scope=["backend\\**"], baseline_commit=head(repo))
        commit_file(repo, "backend/a.py", "2")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), {"L-0001"})

    def test_staleness_does_not_enumerate_full_history(self):
        repo = git_repo(files={'app/a.py': '1'})
        self.addCleanup(cleanup_repo, repo)
        baseline = head(repo)
        commit_file(repo, 'app/a.py', '2')
        def bounded_git(project, *args):
            self.assertNotEqual(args[0], 'rev-list', 'must not enumerate full history')
            return real_git(project, *args)
        with patch('storyloop.lesson_store._git', side_effect=bounded_git):
            self.assertEqual(stale_ids(repo, [sample(baseline_commit=baseline)], head(repo)), {'L-0001'})

    def test_real_broken_git_history_is_not_treated_as_nonancestor(self):
        repo = git_repo()
        self.addCleanup(cleanup_repo, repo)
        baseline = head(repo)
        missing = commit_file(repo, 'middle.py', 'middle')
        target = commit_file(repo, 'last.py', 'last')
        # Corrupt only this disposable fixture: both endpoint commits still resolve.
        import stat
        object_path = repo / '.git/objects' / missing[:2] / missing[2:]
        object_path.chmod(object_path.stat().st_mode | stat.S_IWRITE)
        object_path.unlink()
        with self.assertRaises(StoryLoopError) as raised:
            stale_ids(repo, [sample(baseline_commit=baseline)], target)
        self.assertEqual(raised.exception.code, 'GIT_BLOCKED')

    def test_unreachable_or_non_ancestor_baseline_is_stale(self):
        repo = git_repo()
        self.addCleanup(cleanup_repo, repo)
        missing = sample(baseline_commit="0" * 40)
        self.assertEqual(stale_ids(repo, [missing], head(repo)), {"L-0001"})
        main = head(repo)
        commit_file(repo, "main.py", "main")
        requested = head(repo)
        subprocess.run(["git", "checkout", "-q", "--detach", main], cwd=repo, check=True)
        divergent = commit_file(repo, "other.py", "other")
        entry = sample(baseline_commit=divergent)
        self.assertEqual(stale_ids(repo, [entry], requested), {"L-0001"})

    def test_revision_metadata_cannot_be_interpreted_as_git_options(self):
        repo = git_repo()
        self.addCleanup(cleanup_repo, repo)
        output = repo / "attacker-controlled"
        entry = sample(baseline_commit=f"--output={output}")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), {"L-0001"})
        self.assertFalse(output.exists())
        with self.assertRaises(StoryLoopError) as caught:
            load_lessons_at(repo, f"--output={output}")
        self.assertEqual(caught.exception.code, "LESSON_INVALID")
        self.assertFalse(output.exists())

    def test_source_commit_precedence_and_effective_status(self):
        repo = git_repo()
        self.addCleanup(cleanup_repo, repo)
        self.assertEqual(lesson_source_commit(repo, None), head(repo))
        run = repo / ".storyloop/state/runs/FEAT-1.json"
        write_json(run, {"source_head": "source", "contract_commit": "contract"})
        self.assertEqual(lesson_source_commit(repo, "FEAT-1"), "contract")
        self.assertEqual(effective_status(sample(), {"L-0001"}), "needs-review")


if __name__ == "__main__":
    unittest.main()


class FreshnessMetadataTests(unittest.TestCase):
    def test_confirmation_and_metadata_do_not_stale_but_code_does(self):
        from storyloop.lesson_store import confirm_lesson
        from tests.lesson_support import commit_file
        repo = git_repo(lessons=[sample(tier='core')])
        self.addCleanup(cleanup_repo, repo)
        confirm_lesson(repo, 'L-0001')
        entries = load_lessons_at(repo, head(repo))
        self.assertEqual(stale_ids(repo, entries, head(repo)), set())
        commit_file(repo, 'openspec/lessons/L-0001.md',
                    (repo / 'openspec/lessons/L-0001.md').read_text(encoding='utf-8') + '\nmetadata only\n')
        self.assertEqual(stale_ids(repo, entries, head(repo)), set())
        commit_file(repo, 'app/code.py', 'changed')
        self.assertEqual(stale_ids(repo, entries, head(repo)), {'L-0001'})

    def test_explicit_lessons_scope_ignores_metadata(self):
        from tests.lesson_support import commit_file
        repo = git_repo(lessons=[sample(scope=['openspec/**'])])
        self.addCleanup(cleanup_repo, repo)
        entries = load_lessons_at(repo, head(repo))
        entries[0]['baseline_commit'] = head(repo)
        commit_file(repo, 'openspec/lessons/L-0001.md',
                    (repo / 'openspec/lessons/L-0001.md').read_text(encoding='utf-8') + '\nmetadata only\n')
        self.assertEqual(stale_ids(repo, entries, head(repo)), set())
        commit_file(repo, 'openspec/specs/new.md', 'real contract change')
        self.assertEqual(stale_ids(repo, entries, head(repo)), {'L-0001'})
