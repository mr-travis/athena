"""安装者配置：与模型请求参数分开，在进程启动时加载。"""

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).parent / ".env")


def get_workspace_root() -> Path:
    """默认使用当前用户主目录；显式配置必须为绝对路径。"""
    configured = os.getenv("ATHENA_WORKSPACE_ROOT")
    if configured is None:
        return Path.home() / "athena-workspace"
    if not configured.strip() or "\x00" in configured:
        raise ValueError("ATHENA_WORKSPACE_ROOT 不能为空或包含 NUL 字符。")
    root = Path(configured).expanduser()
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("ATHENA_WORKSPACE_ROOT 必须为不含 '..' 的绝对路径。")
    # 不 resolve：保留链接路径，交由文件工具的 O_NOFOLLOW 拒绝。
    return root


WORKSPACE_ROOT = get_workspace_root()
