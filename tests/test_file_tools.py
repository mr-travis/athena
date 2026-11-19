"""真实临时文件验证访问边界，不读取项目密钥、不调用模型。"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

with patch("dotenv.load_dotenv"):
    import file_tools


class FileToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "workspace"
        self.root.mkdir()
        root_patch = patch.object(file_tools, "WORKSPACE_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_utf8_nested_empty_and_line_endings(self):
        (self.root / "notes").mkdir()
        (self.root / "notes/demo.txt").write_bytes("你好，Athena\r\n".encode())
        (self.root / "empty").touch()
        self.assertEqual(file_tools.read_file("notes/demo.txt"), "你好，Athena\r\n")
        self.assertEqual(file_tools.read_file("empty"), "")

    def test_invalid_paths(self):
        for path in ("", "  ", ".", "\x00", "../secret", "notes/../file", str(self.base / "secret")):
            with self.subTest(path=repr(path)):
                with self.assertRaises(file_tools.WorkspacePermissionError):
                    file_tools.read_file(path)
        with self.assertRaises(TypeError):
            file_tools.read_file(None)

    def test_missing_workspace_does_not_create_or_fall_back(self):
        missing = self.base / "not-created"
        with patch.object(file_tools, "WORKSPACE_ROOT", missing):
            with self.assertRaises(FileNotFoundError):
                file_tools.read_file("read_tool_demo.txt")
        self.assertFalse(missing.exists())

    def test_similarly_named_sibling_is_outside(self):
        sibling = self.base / "workspace-other"
        sibling.mkdir()
        (sibling / "secret").write_text("outside", encoding="utf-8")
        with self.assertRaises(file_tools.WorkspacePermissionError):
            file_tools.read_file("../workspace-other/secret")

    def test_symlinks_internal_external_directory_and_broken(self):
        (self.root / "normal").write_text("inside", encoding="utf-8")
        (self.base / "secret").write_text("outside", encoding="utf-8")
        links = {"internal": self.root / "normal", "external": self.base / "secret",
                 "directory": self.base, "broken": self.base / "missing"}
        for name, target in links.items():
            (self.root / name).symlink_to(target)
        for path in ("internal", "external", "directory/secret", "broken"):
            with self.subTest(path=path):
                with self.assertRaises(file_tools.WorkspacePermissionError):
                    file_tools.read_file(path)

    def test_workspace_root_symlink_is_rejected(self):
        link = self.base / "root-link"
        link.symlink_to(self.root, target_is_directory=True)
        with patch.object(file_tools, "WORKSPACE_ROOT", link):
            with self.assertRaises(file_tools.WorkspacePermissionError):
                file_tools.read_file("file")

    def test_missing_directory_and_fifo(self):
        with self.assertRaises(FileNotFoundError):
            file_tools.read_file("missing")
        (self.root / "folder").mkdir()
        with self.assertRaises(IsADirectoryError):
            file_tools.read_file("folder")
        os.mkfifo(self.root / "pipe")
        with self.assertRaises(file_tools.WorkspacePermissionError):
            file_tools.read_file("pipe")

    def test_encoding_and_nul(self):
        for content in (b"\xff", b"text\x00text"):
            (self.root / "invalid").write_bytes(content)
            with self.assertRaises(file_tools.InvalidTextError):
                file_tools.read_file("invalid")

    def test_size_boundary_uses_bytes(self):
        content = "你".encode() * 21845 + b"x"
        self.assertEqual(len(content), file_tools.MAX_FILE_BYTES)
        target = self.root / "limit"
        target.write_bytes(content)
        self.assertEqual(file_tools.read_file("limit"), content.decode())
        target.write_bytes(content + b"x")
        with self.assertRaises(file_tools.FileTooLargeError):
            file_tools.read_file("limit")

    def test_growth_after_stat_is_bounded(self):
        target = self.root / "growing"
        target.write_bytes(b"x")
        real_fstat = os.fstat
        real_read = os.read
        def grow(descriptor):
            info = real_fstat(descriptor)
            target.write_bytes(b"x" * (file_tools.MAX_FILE_BYTES + 100))
            return info
        with patch("file_tools.os.fstat", side_effect=grow), patch("file_tools.os.read", wraps=real_read) as read:
            with self.assertRaises(file_tools.FileTooLargeError):
                file_tools.read_file("growing")
            self.assertEqual(read.call_args.args[1], file_tools.MAX_FILE_BYTES + 1)

    def test_replacement_with_symlink_at_open_is_rejected(self):
        target = self.root / "file"
        target.write_text("inside", encoding="utf-8")
        outside = self.base / "secret"
        outside.write_text("outside", encoding="utf-8")
        real_open = os.open
        def replace(name, flags, *, dir_fd=None):
            if name == "file":
                target.unlink()
                target.symlink_to(outside)
            return real_open(name, flags, dir_fd=dir_fd)
        with patch("file_tools.os.open", side_effect=replace):
            with self.assertRaises(file_tools.WorkspacePermissionError):
                file_tools.read_file("file")


if __name__ == "__main__":
    unittest.main()
