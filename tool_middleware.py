"""工具执行记录中间件：统一记录分发前后状态、耗时并脱敏意外异常。"""

import json
import logging
from time import perf_counter

from fastapi import HTTPException

from workspace_tools import WORKSPACE_TOOL_NAMES, execute_workspace_tool


# 独立终端日志，只输出 JSON；不交给上层 logger 重复处理。
TOOL_LOGGER = logging.getLogger("athena.tools")
if not TOOL_LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    TOOL_LOGGER.addHandler(handler)
TOOL_LOGGER.setLevel(logging.INFO)
TOOL_LOGGER.propagate = False


def log_tool_event(request_id, step, name, status, duration_ms=0, error_type=None):
    """只构造允许的字段；未知工具名使用固定占位符，避免注入日志。"""
    event = {
        "request_id": request_id,
        "step": step,
        "tool": name if isinstance(name, str) and name in WORKSPACE_TOOL_NAMES else "unknown",
        "status": status,
        "duration_ms": duration_ms,
    }
    if error_type is not None:
        event["error_type"] = error_type
    TOOL_LOGGER.info(json.dumps(event, ensure_ascii=False))


def run_logged_tool(request_id, step, name, arguments):
    """记录一次分发尝试；绝不输出参数、结果正文或原始异常。"""
    started = perf_counter()
    log_tool_event(request_id, step, name, "started")
    try:
        result = execute_workspace_tool(name, arguments)
    except Exception:
        log_tool_event(request_id, step, name, "failed",
                       round((perf_counter() - started) * 1000, 3), "InternalToolError")
        # 不将含潜在敏感数据的原异常交给 ASGI 打印堆栈。
        raise HTTPException(status_code=500, detail="工具内部执行失败。") from None
    kind = result.get("error_type")
    status = "succeeded" if result["ok"] else "failed"
    if kind in ("UnknownTool", "InvalidArguments", "PermissionDenied"):
        status = "rejected"
    log_tool_event(request_id, step, name, status,
                   round((perf_counter() - started) * 1000, 3), kind)
    return result

