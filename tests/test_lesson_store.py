from pathlib import Path
import shutil
import subprocess
import unittest

from storyloop.lesson_store import (
    effective_status, lesson_source_commit, lessons_digest, load_lessons_at, stale_ids,
)
from storyloop.models import StoryLoopError
from storyloop.storage import write_json
from tests.lesson_support import commit_all, commit_file, git_repo, head, sample, write_lesson


class LessonStoreTests(unittest.TestCase):
    def test_missing_directory_is_empty(self):
        repo = git_repo()
        self.addCleanup(shutil.rmtree, repo)
        self.assertEqual(load_lessons_at(repo, head(repo)), [])
        self.assertIsNone(lessons_digest(repo, head(repo)))

    def test_lessons_are_read_from_contract_commit_not_worktree(self):
        repo = git_repo(lessons=[sample(summary="旧")])
        self.addCleanup(shutil.rmtree, repo)
        contract = head(repo)
        write_lesson(repo, sample(summary="新"))
        commit_all(repo, "later")
        self.assertEqual(load_lessons_at(repo, contract)[0]["summary"], "旧")

    def test_invalid_committed_entry_raises(self):
        repo = git_repo(raw_lessons={"L-0001.md": "---\nid: L-0001\n---\n"})
        self.addCleanup(shutil.rmtree, repo)
        with self.assertRaises(StoryLoopError) as caught:
            load_lessons_at(repo, head(repo))
        self.assertEqual(caught.exception.code, "LESSON_INVALID")

    def test_rejects_committed_symlink(self):
        repo = git_repo()
        self.addCleanup(shutil.rmtree, repo)
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
        self.addCleanup(shutil.rmtree, repo)
        frozen = head(repo)
        commit_file(repo, "openspec/foundation/runtime.md", "# Runtime\n")
        with self.assertRaises(StoryLoopError) as caught:
            load_lessons_at(repo, frozen)
        self.assertEqual(caught.exception.code, "LESSON_INVALID")
        self.assertEqual(load_lessons_at(repo, head(repo))[0]["id"], "L-0001")

    def test_change_inside_scope_after_baseline_is_stale(self):
        repo = git_repo(files={"backend/a.py": "1"})
        self.addCleanup(shutil.rmtree, repo)
        entry = sample(scope=["backend/**"], baseline_commit=head(repo))
        commit_file(repo, "frontend/b.js", "x")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), set())
        commit_file(repo, "backend/a.py", "2")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), {"L-0001"})

    def test_global_scope_is_not_narrowed_by_an_additional_scope(self):
        repo = git_repo(files={"backend/a.py": "1"})
        self.addCleanup(shutil.rmtree, repo)
        entry = sample(scope=["**", "backend/**"], baseline_commit=head(repo))
        commit_file(repo, "frontend/b.js", "changed")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), {"L-0001"})

    def test_backslash_scope_is_normalized_before_git_matching(self):
        repo = git_repo(files={"backend/a.py": "1"})
        self.addCleanup(shutil.rmtree, repo)
        entry = sample(scope=["backend\\**"], baseline_commit=head(repo))
        commit_file(repo, "backend/a.py", "2")
        self.assertEqual(stale_ids(repo, [entry], head(repo)), {"L-0001"})

    def test_unreachable_or_non_ancestor_baseline_is_stale(self):
        repo = git_repo()
        self.addCleanup(shutil.rmtree, repo)
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
        self.addCleanup(shutil.rmtree, repo)
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
        self.addCleanup(shutil.rmtree, repo)
        self.assertEqual(lesson_source_commit(repo, None), head(repo))
        run = repo / ".storyloop/state/runs/FEAT-1.json"
        write_json(run, {"source_head": "source", "contract_commit": "contract"})
        self.assertEqual(lesson_source_commit(repo, "FEAT-1"), "contract")
        self.assertEqual(effective_status(sample(), {"L-0001"}), "needs-review")


if __name__ == "__main__":
    unittest.main()
