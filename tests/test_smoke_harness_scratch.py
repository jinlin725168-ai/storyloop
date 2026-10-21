import os
from pathlib import Path
import tempfile
import unittest

from storyloop.models import StoryLoopError
from storyloop.testing.smoke_harness import v1


class ScratchTests(unittest.TestCase):
    def scratch(self, root, base):
        self.assertTrue(hasattr(v1, "Scratch"), "Missing leased scratch implementation")
        value = v1.Scratch(root, base=base)
        self.addCleanup(value.close)
        return value

    def test_same_repository_concurrent_runs_and_other_repositories_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in ("a", "b", "leases"):
                (base / name).mkdir()
            a = self.scratch(base / "a", base / "leases")
            a2 = self.scratch(base / "a", base / "leases")
            b = self.scratch(base / "b", base / "leases")
            self.assertEqual(len({a.root, a2.root, b.root}), 3)
            self.assertNotEqual(a.repository_id, b.repository_id)
            self.assertEqual(a.repository_id, a2.repository_id)
            a.close()
            self.assertTrue(a2.root.exists())
            self.assertTrue(b.root.exists())
            a2.close()
            b.close()

    def test_same_size_mtime_changes_sync_by_content_and_delete_only_owned_mirror(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            project = base / "project"
            project.mkdir()
            source = project / "source.py"
            source.write_text("ONE")
            scratch = self.scratch(project, base)
            mirror = scratch.sync(project, ["source.py"])
            before = source.stat()
            source.write_text("TWO")
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            scratch.sync(project, ["source.py"])
            self.assertEqual((mirror / "source.py").read_text(), "TWO")
            (scratch.root / "keep.txt").write_text("independent")
            source.unlink()
            scratch.sync(project, ["source.py"])
            self.assertFalse((mirror / "source.py").exists())
            self.assertEqual((scratch.root / "keep.txt").read_text(), "independent")
            scratch.close()

    def test_source_allowlist_never_scans_or_reads_credentials(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            project = base / "project"
            project.mkdir()
            (project / "safe.py").write_text("VALUE = 1")
            (project / ".env").write_text("synthetic forbidden fixture")
            scratch = self.scratch(project, base)
            original = Path.open
            def deny_secret(path, *args, **kwargs):
                if path == project / ".env":
                    self.fail("Credential was opened")
                return original(path, *args, **kwargs)
            with patch.object(Path, "open", deny_secret):
                mirror = scratch.sync(project, ["safe.py"])
                self.assertEqual([p.name for p in mirror.iterdir()], ["safe.py"])
                for bad in ([".env"], ["."], ["../escape"], ["project"]):
                    with self.subTest(bad=bad), self.assertRaises(StoryLoopError):
                        scratch.sync(project, bad)
            scratch.close()

    def test_cache_missing_or_source_disguised_as_cache_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            cache = base / "cache"
            cache.mkdir()
            scratch = self.scratch(base, base)
            with self.assertRaises(StoryLoopError):
                scratch.cache("maven", base / "missing", ["dependency.jar"])
            (cache / "source.py").write_text("not dependency archive")
            with self.assertRaises(StoryLoopError):
                scratch.cache("maven", cache, ["source.py"])
            (cache / "dependency.jar").write_bytes(b"public synthetic archive")
            copied = scratch.cache("maven", cache, ["dependency.jar"])
            (copied / "dependency.jar").write_bytes(b"private build mutation")
            self.assertEqual((cache / "dependency.jar").read_bytes(), b"public synthetic archive")
            scratch.close()

    def test_cleanup_refuses_forged_lease_or_link_without_following_external_target(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            scratch = self.scratch(base, base)
            ownership = scratch.root / ".storyloop-lease.json"
            before = ownership.read_bytes()
            ownership.write_text("{}")
            with self.assertRaises(StoryLoopError):
                scratch.close()
            self.assertTrue(scratch.root.exists())
            ownership.write_bytes(before)
            if os.name != "nt":
                external = base / "external.txt"
                external.write_text("keep")
                (scratch.root / "link").symlink_to(external)
                with self.assertRaises(StoryLoopError):
                    scratch.close()
                self.assertEqual(external.read_text(), "keep")
                (scratch.root / "link").unlink()
            scratch.close()
