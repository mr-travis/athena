"""受控工作区只读工具，可独立调用，也供问答流程使用。"""

from contextlib import ExitStack
import errno
import os
from pathlib import Path
import stat

from settings import WORKSPACE_ROOT

MAX_FILE_BYTES = 64 * 1024
MAX_DIRECTORY_ENTRIES = 100


class WorkspacePermissionError(PermissionError):
    """路径不符合工作区访问规则。"""


class FileTooLargeError(ValueError):
    """文件超过读取上限。"""


class InvalidTextError(ValueError):
    """内容不符合 UTF-8 文本约定。"""


class DirectoryTooLargeError(ValueError):
    """目录中的可见条目超过返回上限。"""


def _relative_path(path: str, *, allow_root: bool = False) -> Path:
    """两个工具共用相同路径规则；列目录额外允许 '.'。"""
    if not isinstance(path, str):
        raise TypeError("文件路径必须是字符串。")
    if not path or not path.strip() or "\x00" in path:
        raise WorkspacePermissionError("请提供有效的工作区相对路径。")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkspacePermissionError("只允许工作区相对路径，且不能包含 '..'。")
    if not relative.parts and not allow_root:
        raise WorkspacePermissionError("请提供具体文件的相对路径。")
    return relative


def _open_at(stack: ExitStack, name, flags: int, directory=None) -> int:
    """安全打开并登记关闭描述符，禁止通过解析链接绕过路径规则。"""
    try:
        descriptor = os.open(name, flags, dir_fd=directory)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise WorkspacePermissionError("路径包含符号链接或无效的目录。") from None
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError("工作区或指定路径不存在。") from None
        if exc.errno in (errno.EACCES, errno.EPERM):
            raise WorkspacePermissionError("没有权限访问指定路径。") from None
        raise
    stack.callback(os.close, descriptor)
    return descriptor


def _open_directory(stack: ExitStack, parts: tuple[str, ...]) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = _open_at(stack, WORKSPACE_ROOT, flags)
    for part in parts:
        directory = _open_at(stack, part, flags, directory)
    return directory


def list_directory(path: str = ".") -> dict:
    """列举工作区目录一层，最多 100 个普通文件/目录，跳过隐藏及链接。"""
    relative = _relative_path(path, allow_root=True)
    with ExitStack() as stack:
        directory = _open_directory(stack, relative.parts)
        entries = []
        # scandir 使用已安全打开的目录，而非重新按字符串路径访问。
        with os.scandir(directory) as iterator:
            for entry in iterator:
                if entry.name.startswith("."):
                    continue
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                except FileNotFoundError:
                    # 扫描过程中消失的条目跳过，不尝试追踪新的位置。
                    continue
                if stat.S_ISREG(mode):
                    kind = "file"
                elif stat.S_ISDIR(mode):
                    kind = "directory"
                else:
                    continue
                entries.append({"name": entry.name, "type": kind})
                if len(entries) > MAX_DIRECTORY_ENTRIES:
                    raise DirectoryTooLargeError("目录超过 100 个可见条目的上限，请指定更小的子目录。")
    entries.sort(key=lambda entry: entry["name"])
    return {"path": relative.as_posix(), "entries": entries}


def read_file(path: str) -> str:
    """读取工作区内至多 64 KiB 的 UTF-8 普通文件，不跟随符号链接。"""
    relative = _relative_path(path)

    # 目录描述符固定访问起点；每一级都禁止跟随链接，避免先检查再打开的间隙。
    with ExitStack() as stack:
        directory = _open_directory(stack, relative.parts[:-1])

        # NONBLOCK 防止打开管道时阻塞；之后通过 fstat 拒绝所有非普通文件。
        descriptor = _open_at(
            stack, relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, directory
        )
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            raise IsADirectoryError("目标是目录，请提供具体文件路径。")
        if not stat.S_ISREG(info.st_mode):
            raise WorkspacePermissionError("只允许读取普通文件。")
        if info.st_size > MAX_FILE_BYTES:
            raise FileTooLargeError("文件超过 65,536 字节的读取上限。")

        # 即使文件在检查后增长，也只读取上限加一个字节；循环处理短读取。
        data = bytearray()
        while len(data) <= MAX_FILE_BYTES:
            chunk = os.read(descriptor, MAX_FILE_BYTES + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_FILE_BYTES:
            raise FileTooLargeError("文件超过 65,536 字节的读取上限。")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise InvalidTextError("文件不是有效的 UTF-8 文本。") from None
        if "\x00" in text:
            raise InvalidTextError("文件包含 NUL 字符，不符合文本要求。")
        return text
