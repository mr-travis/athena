"""配置测试不加载本地 .env，不创建真实用户工作区。"""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

with patch("dotenv.load_dotenv"):
    from settings import get_workspace_root


class WorkspaceSettingsTests(unittest.TestCase):
    def test_default_follows_installing_user_home(self):
        for home in (Path("/Users/travis"), Path("/Users/another-user")):
            with self.subTest(home=str(home)), patch.dict(os.environ, {}, clear=True), patch("settings.Path.home", return_value=home):
                self.assertEqual(get_workspace_root(), home / "athena-workspace")

    def test_absolute_override(self):
        with patch.dict(os.environ, {"ATHENA_WORKSPACE_ROOT": "/tmp/another-workspace"}, clear=True):
            self.assertEqual(get_workspace_root(), Path("/tmp/another-workspace"))

    def test_tilde_expands_to_current_user(self):
        with patch.dict(os.environ, {"HOME": "/tmp/test-user", "ATHENA_WORKSPACE_ROOT": "~/projects"}, clear=True):
            self.assertEqual(get_workspace_root(), Path("/tmp/test-user/projects"))

    def test_invalid_config_fails_instead_of_falling_back(self):
        for value in ("", "  ", "workspace", "../workspace", "/tmp/../workspace", "bad\x00path"):
            with self.subTest(value=repr(value)):
                # os.environ 不接受 NUL；在 getenv 边界模拟该值。
                with patch("settings.os.getenv", return_value=value):
                    with self.assertRaises(ValueError):
                        get_workspace_root()


if __name__ == "__main__":
    unittest.main()
