"""独立目录工具的真实临时文件验证，不读取用户目录或调用模型。"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

with patch("dotenv.load_dotenv"):
    import file_tools


class DirectoryToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        root_patch = patch.object(file_tools, "WORKSPACE_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_root_sorted_and_nonrecursive_without_reading_contents(self):
        (self.root / "z.txt").write_bytes(b"\xff\x00")
        (self.root / "notes").mkdir()
        (self.root / "notes/nested.txt").touch()
        (self.root / "a.txt").touch()
        with patch("file_tools.os.read", side_effect=AssertionError("不应读取正文")):
            self.assertEqual(file_tools.list_directory(), {
                "path": ".", "entries": [{"name": "a.txt", "type": "file"},
                                         {"name": "notes", "type": "directory"},
                                         {"name": "z.txt", "type": "file"}],
            })

    def test_subdirectory_and_normalized_relative_path(self):
        (self.root / "notes").mkdir()
        (self.root / "notes/中文.txt").touch()
        self.assertEqual(file_tools.list_directory("./notes/"), {
            "path": "notes", "entries": [{"name": "中文.txt", "type": "file"}],
        })

    def test_empty_directory(self):
        self.assertEqual(file_tools.list_directory("."), {"path": ".", "entries": []})

    def test_invalid_and_outside_paths(self):
        sibling = self.base / "workspace-other"
        sibling.mkdir()
        for path in ("", "  ", "\x00", "..", "notes/../notes", "../workspace-other", str(sibling)):
            with self.subTest(path=repr(path)):
                with self.assertRaises(file_tools.WorkspacePermissionError):
                    file_tools.list_directory(path)
        with self.assertRaises(TypeError):
            file_tools.list_directory(None)

    def test_missing_workspace_or_target_does_not_create(self):
        with self.assertRaises(FileNotFoundError):
            file_tools.list_directory("missing")
        missing = self.base / "not-created"
        with patch.object(file_tools, "WORKSPACE_ROOT", missing):
            with self.assertRaises(FileNotFoundError):
                file_tools.list_directory()
        self.assertFalse(missing.exists())

    def test_regular_file_is_not_a_directory(self):
        (self.root / "file.txt").touch()
        with self.assertRaises(file_tools.WorkspacePermissionError):
            file_tools.list_directory("file.txt")

    def test_hidden_symlink_and_special_entries_are_filtered(self):
        (self.root / "normal").touch()
        (self.root / ".hidden").touch()
        (self.root / ".private").mkdir()
        (self.root / "internal-link").symlink_to(self.root / "normal")
        (self.root / "external-link").symlink_to(self.base)
        (self.root / "broken-link").symlink_to(self.base / "missing")
        os.mkfifo(self.root / "pipe")
        self.assertEqual(file_tools.list_directory()["entries"], [{"name": "normal", "type": "file"}])

    def test_target_and_ancestor_symlinks_are_rejected(self):
        (self.root / "notes").mkdir()
        (self.root / "notes/sub").mkdir()
        (self.root / "internal").symlink_to(self.root / "notes")
        (self.root / "external").symlink_to(self.base)
        (self.root / "broken").symlink_to(self.base / "missing")
        for path in ("internal", "internal/sub", "external", "broken"):
            with self.subTest(path=path):
                with self.assertRaises(file_tools.WorkspacePermissionError):
                    file_tools.list_directory(path)

    def test_root_symlink_is_rejected(self):
        link = self.base / "root-link"
        link.symlink_to(self.root)
        with patch.object(file_tools, "WORKSPACE_ROOT", link):
            with self.assertRaises(file_tools.WorkspacePermissionError):
                file_tools.list_directory()

    def test_100_visible_entries_succeed_101_fail_without_partial_result(self):
        for index in range(100):
            (self.root / f"file-{index:03d}").touch()
        (self.root / ".hidden").touch()
        (self.root / "ignored-link").symlink_to(self.base)
        self.assertEqual(len(file_tools.list_directory()["entries"]), 100)
        (self.root / "extra").mkdir()
        with self.assertRaises(file_tools.DirectoryTooLargeError):
            file_tools.list_directory()

    def test_directory_replaced_with_link_at_open_is_rejected(self):
        target = self.root / "notes"
        target.mkdir()
        real_open = os.open
        def replace(name, flags, *, dir_fd=None):
            if name == "notes":
                target.rmdir()
                target.symlink_to(self.base)
            return real_open(name, flags, dir_fd=dir_fd)
        with patch("file_tools.os.open", side_effect=replace):
            with self.assertRaises(file_tools.WorkspacePermissionError):
                file_tools.list_directory("notes")

    def test_descriptor_is_closed_when_scan_fails(self):
        real_open = os.open
        real_close = os.close
        with patch("file_tools.os.open", wraps=real_open) as opened, patch("file_tools.os.close", wraps=real_close) as closed:
            with patch("file_tools.os.scandir", side_effect=PermissionError("不可列举")):
                with self.assertRaises(PermissionError):
                    file_tools.list_directory()
            self.assertEqual(opened.call_count, 1)
            closed.assert_called_once()
            # open 已返回的描述符确实关闭，而非仅调用了一个不同的 close。
            with self.assertRaises(OSError):
                os.fstat(closed.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
