"""工作区工具的模型声明、参数校验与分发；文件访问规则由 file_tools 落实。"""

import json

from web_tools import fetch_url

from file_tools import DirectoryTooLargeError, FileTooLargeError, InvalidTextError, list_directory, read_file


# 分发与日志共用的固定白名单；增加工具时需同时实现声明和分发分支。
WORKSPACE_TOOL_NAMES = frozenset({"read_file", "list_directory", "fetch_url"})

WORKSPACE_TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取服务端配置的工作区内一个 UTF-8 普通文本文件，最多 64 KiB；不允许符号链接，路径相对于工作区。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "工作区相对路径，如 notes/demo.txt"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": "列举工作区内目录的一层名称与类型，最多 100 个可见条目；不递归、不读正文，过滤隐藏、符号链接和特殊文件。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "工作区相对目录路径，省略时为 '.'（根目录）"}},
            "additionalProperties": False,
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "读取用户明确提供的 api-docs.deepseek.com HTTPS 文档链接，仅 HTML/纯文本；不搜索、不执行 JavaScript，正文最多 12000 字符。",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "用户提供的完整纯 HTTPS URL，不能是 Markdown 链接"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
}]


def execute_workspace_tool(name: str, arguments: str) -> dict:
    """仅分发白名单工具；参数校验和文件权限校验分别落实。"""
    def error(kind, message):
        return {"ok": False, "error_type": kind, "message": message}

    if name not in WORKSPACE_TOOL_NAMES:
        return error("UnknownTool", "只允许调用 read_file、list_directory 或 fetch_url。")
    try:
        params = json.loads(arguments)
    except (ValueError, TypeError, RecursionError):
        return error("InvalidArguments", "工具参数必须是 JSON 对象。")
    if name == "fetch_url":
        if not isinstance(params, dict) or set(params) != {"url"} or not isinstance(params["url"], str):
            return error("InvalidArguments", "工具参数必须且只能包含 url 字符串。")
        return fetch_url(params["url"])
    if name == "list_directory" and params == {}:
        params = {"path": "."}
    if not isinstance(params, dict) or set(params) != {"path"} or not isinstance(params["path"], str):
        return error("InvalidArguments", "工具参数必须且只能包含 path 字符串。")
    try:
        if name == "list_directory":
            return {"ok": True, **list_directory(params["path"])}
        content = read_file(params["path"])
    except FileNotFoundError:
        return error("FileNotFound", "工作区或指定文件不存在，请核对相对路径。")
    except IsADirectoryError:
        return error("IsDirectory", "目标是目录，请提供具体文件路径。")
    except PermissionError:
        return error("PermissionDenied", "路径不允许访问：仅限工作区内符合工具类型的文件或目录，禁止越界和符号链接。")
    except DirectoryTooLargeError:
        return error("DirectoryTooLarge", "目录超过 100 个可见条目，请指定更小的子目录。")
    except FileTooLargeError:
        return error("FileTooLarge", "文件超过 65,536 字节的读取上限。")
    except InvalidTextError:
        return error("InvalidText", "文件必须是有效的 UTF-8 文本，且不能包含 NUL 字符。")
    except UnicodeError:
        return error("InvalidArguments", "文件路径包含无法编码的字符。")
    except OSError:
        # 不把宿主机绝对路径、系统异常详情传给模型。
        return error("ReadFailed" if name == "read_file" else "ListFailed", "文件或目录访问失败，请检查状态。")
    return {"ok": True, "path": params["path"], "content": content}
