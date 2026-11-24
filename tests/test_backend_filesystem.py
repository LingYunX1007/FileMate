import os
from pathlib import Path
import subprocess
import unittest

from tempfile import TemporaryDirectory


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        from services.filemate import filesystem

        self.fs = filesystem
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.directory = self.base / "root"
        self.directory.mkdir()
        self.root = self.fs.authorize_root(str(self.directory))
        self.source = self.directory / "source.txt"
        self.source.write_bytes(b"customer")

    def move(self, target, fingerprint=None):
        self.fs.move(
            self.root, self.source.name, self.root, target,
            fingerprint or self.fs.fingerprint(self.root, self.source.name),
        )

    def test_traversal_and_win32_aliases_are_rejected(self):
        for unsafe in ("../escape.txt", "sub/../../escape.txt", "C:/escape.txt",
                       "/escape.txt", "a/CON.txt", "a/lpt¹.txt", "x.txt:stream",
                       "x.txt.", "folder /x.txt", "//server/share/name.txt"):
            with self.subTest(unsafe=unsafe), self.assertRaises(self.fs.FileProblem):
                self.move(unsafe)
        self.assertEqual(self.source.read_bytes(), b"customer")

    def test_junction_cannot_supply_source_or_target_parent(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private.txt").write_bytes(b"private")
        junction = self.directory / "junction"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                           check=True, capture_output=True)
        else:
            junction.symlink_to(outside, target_is_directory=True)
        self.addCleanup(junction.rmdir if os.name == "nt" else junction.unlink)
        with self.assertRaises(self.fs.FileProblem):
            self.fs.fingerprint(self.root, "junction/private.txt")
        with self.assertRaises(self.fs.FileProblem):
            self.move("junction/target.txt")
        self.assertFalse((outside / "target.txt").exists())

    def test_same_size_same_mtime_change_blocks_move(self):
        fingerprint = self.fs.fingerprint(self.root, self.source.name)
        initial = self.source.stat()
        self.source.write_bytes(b"changed!")
        os.utime(self.source, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        with self.assertRaises(self.fs.FileProblem) as raised:
            self.move("target.txt", fingerprint)
        self.assertEqual(raised.exception.code, "source_changed")
        self.assertEqual(self.source.read_bytes(), b"changed!")
        self.assertFalse((self.directory / "target.txt").exists())

    def test_move_never_overwrites_existing_target(self):
        target = self.directory / "target.txt"
        target.write_bytes(b"owner")
        with self.assertRaises(self.fs.FileProblem) as raised:
            self.move(target.name)
        self.assertEqual(raised.exception.code, "target_exists")
        self.assertEqual(target.read_bytes(), b"owner")
        self.assertEqual(self.source.read_bytes(), b"customer")

    def test_move_creates_reviewed_subdirectories(self):
        before = self.fs.fingerprint(self.root, self.source.name)
        self.move("customer/2026/target.txt", before)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.fs.fingerprint(self.root, "customer/2026/target.txt"), before)
        self.assertEqual((self.directory / "customer" / "2026" / "target.txt").read_bytes(), b"customer")

    def test_recovery_does_not_infer_success_from_identical_copy(self):
        before = self.fs.fingerprint(self.root, self.source.name)
        target = self.directory / "target.txt"
        target.write_bytes(self.source.read_bytes())
        initial = self.source.stat()
        os.utime(target, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        self.source.unlink()
        self.assertEqual(self.fs.recover(self.root, self.source.name, self.root, target.name, before),
                         "ambiguous")

    def test_recovery_recognizes_move_with_same_file_identity(self):
        before = self.fs.fingerprint(self.root, self.source.name)
        self.assertEqual(self.fs.recover(self.root, self.source.name, self.root, "target.txt", before),
                         "not_applied")
        self.move("target.txt", before)
        self.assertEqual(self.fs.recover(self.root, self.source.name, self.root, "target.txt", before),
                         "applied")


    def test_directory_cannot_be_replaced_while_authorized_handle_is_held(self):
        if os.name != "nt":
            self.skipTest("Directory sharing is a Windows guarantee")
        folder = self.directory / "customer"
        folder.mkdir()
        document = folder / "source.txt"
        document.write_bytes(b"customer")
        with self.fs._parent(self.root, "customer/source.txt"):
            with self.assertRaises(PermissionError):
                self.directory.rename(self.base / "moved-root")

    def test_existing_file_without_delete_sharing_blocks_move(self):
        if os.name != "nt":
            self.skipTest("Delete sharing is a Windows guarantee")
        before = self.fs.fingerprint(self.root, self.source.name)
        with self.source.open("rb"), self.assertRaises(self.fs.FileProblem):
            self.move("target.txt", before)
        self.assertEqual(self.source.read_bytes(), b"customer")
        self.assertFalse((self.directory / "target.txt").exists())

    def test_hard_links_are_rejected_in_authorized_directory(self):
        link = self.directory / "linked.txt"
        os.link(self.source, link)
        with self.assertRaises(self.fs.FileProblem) as failure:
            self.fs.fingerprint(self.root, link.name)
        self.assertEqual(failure.exception.code, "unsafe_file")


if __name__ == "__main__":
    unittest.main()
