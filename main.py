"""Athena：单轮问答，最多两次受控目录列举或文件读取。"""

import json
import os
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from prompt import SYSTEM_PROMPT
from tool_middleware import log_tool_event, run_logged_tool
from workspace_tools import WORKSPACE_TOOL_NAMES, WORKSPACE_TOOLS


app = FastAPI(title="Athena", description="支持受控目录列举与文件读取的单轮问答接口", version="0.1.0")
MAX_TOOL_CALLS = 2


def model_message(response):
    """拒绝空响应和截断输出，避免执行不完整的工具请求。"""
    if not response.choices:
        raise HTTPException(status_code=502, detail="DeepSeek 未返回候选回复。")
    if getattr(response.choices[0], "finish_reason", None) == "length":
        raise HTTPException(status_code=502, detail="DeepSeek 输出达到长度上限，回复被截断。")
    return response.choices[0].message


def final_reply(message) -> str:
    if getattr(message, "tool_calls", None):
        raise HTTPException(status_code=502, detail="DeepSeek 超出本轮工具调用限制。")
    if not message.content or not message.content.strip():
        raise HTTPException(status_code=502, detail="DeepSeek 未返回有效正文，请稍后重试。")
    return message.content


class ChatRequest(BaseModel):
    # 先去除首尾空白，再校验长度；纯空白也会被拒绝。
    model_config = ConfigDict(str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=10000, description="你想问 Athena 的问题")
    debug: StrictBool = Field(default=False, description="开启后返回本轮实际工具结果，含读取的正文")


class ToolDebugResult(BaseModel):
    step: int
    tool: str
    result: dict[str, Any]


class ChatResponse(BaseModel):
    reply: str
    tool_results: list[ToolDebugResult] | None = None


def ask_deepseek(message: str, *, tool_results: list[ToolDebugResult] | None = None) -> str:
    """每轮至多三次模型请求、两次工具尝试（含失败）；结束后释放客户端。"""
    request_id = uuid4().hex
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="服务未配置 DEEPSEEK_API_KEY，请在本地 .env 中填写。")

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=60.0,
        max_retries=0,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]
    options = {
        "model": os.getenv("MAIN_MODEL", "deepseek-v4-pro"),
        "stream": False,
        "max_tokens": 2048,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    try:
        call_ids = set()
        used = 0
        for _ in range(MAX_TOOL_CALLS + 1):
            # 按实际工具尝试计数；模型请求次数与工具尝试次数分别控制。
            tool_options = {"tools": WORKSPACE_TOOLS, "tool_choice": "auto"} if used < MAX_TOOL_CALLS else {"tool_choice": "none"}
            response = client.chat.completions.create(**options, messages=messages, **tool_options)
            try:
                current = model_message(response)
            except HTTPException:
                if response.choices and getattr(response.choices[0].message, "tool_calls", None):
                    log_tool_event(request_id, used + 1, "unknown", "rejected", error_type="InvalidModelResponse")
                raise
            calls = getattr(current, "tool_calls", None)
            if not calls:
                return final_reply(current)
            if used == MAX_TOOL_CALLS:
                for call in calls:
                    name = getattr(getattr(call, "function", None), "name", None)
                    log_tool_event(request_id, used + 1, name, "rejected", error_type="ToolBudgetExceeded")
                return final_reply(current)
            # 执行前整批检查预算及协议，避免无效批次只执行了一部分。
            if len(calls) > MAX_TOOL_CALLS - used:
                for call in calls:
                    name = getattr(getattr(call, "function", None), "name", None)
                    log_tool_event(request_id, used + 1, name, "rejected", error_type="ToolBudgetExceeded")
                raise HTTPException(status_code=502, detail="本批工具调用超过剩余预算，均未执行。")
            batch_ids = set()
            for call in calls:
                function = getattr(call, "function", None)
                call_id = getattr(call, "id", None)
                if (getattr(call, "type", None) != "function"
                        or not isinstance(call_id, str) or not call_id
                        or call_id in call_ids or call_id in batch_ids
                        or not isinstance(getattr(function, "name", None), str)
                        or not isinstance(getattr(function, "arguments", None), str)):
                    for rejected in calls:
                        name = getattr(getattr(rejected, "function", None), "name", None)
                        log_tool_event(request_id, used + 1, name, "rejected", error_type="InvalidToolCall")
                    raise HTTPException(status_code=502, detail="DeepSeek 返回了无效或重复 ID 的工具调用，本批均未执行。")
                batch_ids.add(call_id)
            call_ids.update(batch_ids)
            tool_messages = []
            for call in calls:
                used += 1
                result = run_logged_tool(request_id, used, call.function.name, call.function.arguments)
                if tool_results is not None:
                    tool_results.append(ToolDebugResult(
                        step=used,
                        tool=call.function.name if call.function.name in WORKSPACE_TOOL_NAMES else "unknown",
                        result=result,
                    ))
                tool_messages.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            # 一条 assistant 消息保留整批请求，每个结果按 ID 单独回传。
            messages = messages + [{
                "role": "assistant", "content": current.content,
                "tool_calls": [{
                    "id": call.id, "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                } for call in calls],
            }] + tool_messages
    except APITimeoutError as exc:
        raise HTTPException(status_code=504, detail="DeepSeek 请求超时，请稍后重试。") from exc
    except APIConnectionError as exc:
        raise HTTPException(status_code=502, detail="无法连接 DeepSeek，请检查服务网络与模型地址。") from exc
    except APIStatusError as exc:
        # 不打印完整异常或请求，避免日志中出现敏感配置。
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek 返回 HTTP {exc.status_code}，请检查服务端密钥、余额及模型配置。",
        ) from exc
    finally:
        client.close()


@app.post("/chat", response_model=ChatResponse, response_model_exclude_none=True, summary="向 Athena 提问")
def chat(body: ChatRequest) -> ChatResponse:
    # 同步 SDK 配合同步路由，FastAPI 会在线程池中执行，避免阻塞事件循环。
    tool_results = [] if body.debug else None
    reply = ask_deepseek(body.message, tool_results=tool_results)
    return ChatResponse(reply=reply, tool_results=tool_results)


def main() -> int:
    """保留第一步的终端调用方式，便于学习对比。"""
    try:
        print(ask_deepseek("你好，请用一句话介绍你自己。"))
    except HTTPException as exc:
        print(exc.detail)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
