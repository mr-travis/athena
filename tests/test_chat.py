"""离线验证真实 FastAPI 路由；替换模型客户端，不读取密钥、不访问模型。"""

import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

# 导入应用时也禁止加载本地 .env，测试不依赖用户的真实密钥。
with patch("dotenv.load_dotenv"):
    import main
    import workspace_tools
    import tool_middleware

from fastapi.testclient import TestClient
from openai import APIConnectionError, APIStatusError, APITimeoutError
from openai.types.chat import ChatCompletion


def completion(content=None, calls=None, finish_reason=None):
    """使用真实 SDK 类型，验证工具消息字段与 API 协议匹配。"""
    return ChatCompletion.model_validate({
        "id": "offline-completion", "created": 0, "model": "offline-model", "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": finish_reason or ("tool_calls" if calls else "stop"),
                     "message": {"role": "assistant", "content": content, "tool_calls": calls}}],
    })


def tool_call(arguments='{"path":"notes/demo.txt"}', name="read_file", call_id="read-1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class ChatTests(unittest.TestCase):
    def setUp(self):
        quiet = patch.object(tool_middleware.TOOL_LOGGER, "handlers", [logging.NullHandler()])
        quiet.start()
        self.addCleanup(quiet.stop)
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-only"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.factory = patch("main.OpenAI")
        self.client = self.factory.start().return_value
        self.addCleanup(self.factory.stop)
        self.create = self.client.chat.completions.create
        self.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="这是离线测试回复"))]
        )
        self.api = TestClient(main.app)
        self.addCleanup(self.api.close)

    def test_debug_returns_actual_batch_results_without_changing_logs(self):
        url = "https://api-docs.deepseek.com/"
        page = {"ok": True, "url": url, "content": "private-page", "truncated": True}
        self.create.side_effect = [completion(calls=[tool_call(), tool_call(json.dumps({"url": url}), "fetch_url", "web-2")]), completion("完成")]
        with patch("workspace_tools.read_file", return_value="private-file"), patch("workspace_tools.fetch_url", return_value=page):
            with self.assertLogs("athena.tools", level="INFO") as logs:
                response = self.api.post("/chat", json={"message": "核对", "debug": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"reply": "完成", "tool_results": [
            {"step": 1, "tool": "read_file", "result": {"ok": True, "path": "notes/demo.txt", "content": "private-file"}},
            {"step": 2, "tool": "fetch_url", "result": page},
        ]})
        for value in [url, "private-page", "private-file"]:
            self.assertNotIn(value, str(logs.output))
        self.assertEqual(self.create.call_count, 2)
        self.assertEqual(self.create.call_args.kwargs["tool_choice"], "none")
        self.assertNotIn("debug", self.create.call_args.kwargs)

    def test_debug_failure_result_and_request_isolation(self):
        failure = {"ok": False, "error_type": "DNSFailed", "message": "域名解析失败。"}
        self.create.side_effect = [completion(calls=[tool_call('{"url":"https://api-docs.deepseek.com/"}', "fetch_url", "web-1")]), completion("读取失败"), completion("普通回答"), completion("无工具")]
        with patch("workspace_tools.fetch_url", return_value=failure):
            first = self.api.post("/chat", json={"message": "读网页", "debug": True})
        self.assertEqual(first.json()["tool_results"], [{"step": 1, "tool": "fetch_url", "result": failure}])
        self.assertEqual(self.api.post("/chat", json={"message": "普通问题", "debug": False}).json(), {"reply": "普通回答"})
        self.assertEqual(self.api.post("/chat", json={"message": "普通问题", "debug": True}).json(), {"reply": "无工具", "tool_results": []})

    def test_debug_requires_boolean(self):
        for value in ["true", 1, None, {}]:
            with self.subTest(value=value):
                self.assertEqual(self.api.post("/chat", json={"message": "问答", "debug": value}).status_code, 422)
        self.create.assert_not_called()

    def test_web_tool_success_feedback_and_private_logs(self):
        url = "https://api-docs.deepseek.com/"
        result = {"ok": True, "url": url, "content": "private-page-body", "truncated": True}
        self.create.side_effect = [completion(calls=[tool_call(json.dumps({"url": url}), "fetch_url", "web-1")]), completion("正文已截断")]
        with patch("workspace_tools.fetch_url", return_value=result) as fetch:
            with self.assertLogs("athena.tools", level="INFO") as captured:
                self.assertEqual(self.api.post("/chat", json={"message": "读取网页"}).status_code, 200)
        fetch.assert_called_once_with(url)
        message = self.create.call_args_list[1].kwargs["messages"][-1]
        self.assertEqual(message["tool_call_id"], "web-1")
        self.assertEqual(json.loads(message["content"]), result)
        events = [json.loads(record.getMessage()) for record in captured.records]
        self.assertEqual([(e["tool"], e["status"]) for e in events], [("fetch_url", "started"), ("fetch_url", "succeeded")])
        self.assertNotIn(url, str(captured.output))
        self.assertNotIn("private-page-body", str(captured.output))

    def test_web_invalid_arguments_do_not_fetch(self):
        with patch("workspace_tools.fetch_url") as fetch:
            for arguments in ['{}', '[]', 'null', 'not-json', '{"url":1}', '{"url":"x","path":"y"}']:
                with self.subTest(arguments=arguments):
                    result = workspace_tools.execute_workspace_tool("fetch_url", arguments)
                    self.assertEqual(result["error_type"], "InvalidArguments")
            fetch.assert_not_called()

    def test_web_failure_shares_budget_with_file_tool(self):
        failure = {"ok": False, "error_type": "DNSFailed", "message": "域名解析失败。"}
        self.create.side_effect = [completion(calls=[tool_call('{"url":"https://api-docs.deepseek.com/"}', "fetch_url", "web-1")]),
                                   completion(calls=[tool_call(call_id="read-2")]), completion("网页失败，文件成功")]
        with patch("workspace_tools.fetch_url", return_value=failure) as fetch, patch("workspace_tools.read_file", return_value="text") as read:
            self.assertEqual(self.api.post("/chat", json={"message": "读取网页，失败则读文件"}).status_code, 200)
        fetch.assert_called_once()
        read.assert_called_once()
        self.assertEqual(json.loads(self.create.call_args_list[1].kwargs["messages"][3]["content"]), failure)
        final = self.create.call_args_list[2].kwargs
        self.assertEqual(final["tool_choice"], "none")
        self.assertNotIn("tools", final)

    def test_user_message_reaches_model_and_reply_is_returned(self):
        response = self.api.post("/chat", json={"message": "  什么是 Harness？  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"reply": "这是离线测试回复"})
        self.assertEqual(
            self.create.call_args.kwargs["messages"][1],
            {"role": "user", "content": "什么是 Harness？"},
        )
        self.client.close.assert_called_once()
        self.assertEqual(self.create.call_count, 1)
        self.assertEqual(self.create.call_args.kwargs["tool_choice"], "auto")

    def test_invalid_input_does_not_call_model(self):
        for payload in [{}, {"message": ""}, {"message": " \n\t "},
                        {"message": None}, {"message": 123}, {"message": "x" * 10001}]:
            with self.subTest(payload_type=type(payload.get("message")).__name__):
                self.assertEqual(self.api.post("/chat", json=payload).status_code, 422)
        self.create.assert_not_called()

    def test_missing_key_returns_503(self):
        os.environ["DEEPSEEK_API_KEY"] = ""
        self.assertEqual(self.api.post("/chat", json={"message": "你好"}).status_code, 503)
        self.create.assert_not_called()

    def test_model_errors_are_mapped_and_client_closed(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        errors = [
            (APITimeoutError(request=request), 504),
            (APIConnectionError(request=request), 502),
            (APIStatusError("private-upstream-details", response=httpx.Response(401, request=request), body=None), 502),
        ]
        for error, status in errors:
            with self.subTest(error=type(error).__name__):
                self.client.close.reset_mock()
                self.create.side_effect = error
                response = self.api.post("/chat", json={"message": "你好"})
                self.assertEqual(response.status_code, status)
                self.assertIn("detail", response.json())
                self.assertNotIn("private-upstream-details", response.text)
                self.client.close.assert_called_once()

    def test_empty_model_reply_returns_502(self):
        for choices in [[], [SimpleNamespace(message=SimpleNamespace(content=None))],
                        [SimpleNamespace(message=SimpleNamespace(content=" \n "))]]:
            self.create.return_value = SimpleNamespace(choices=choices)
            self.assertEqual(self.api.post("/chat", json={"message": "你好"}).status_code, 502)

    def test_model_output_limit_and_distinct_response_errors(self):
        cases = [
            (SimpleNamespace(choices=[]), "DeepSeek 未返回候选回复。"),
            (completion("partial", finish_reason="length"), "DeepSeek 输出达到长度上限，回复被截断。"),
        ]
        for model_response, expected in cases:
            with self.subTest(expected=expected):
                self.create.return_value = model_response
                response = self.api.post("/chat", json={"message": "你好"})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json(), {"detail": expected})
                self.assertEqual(self.create.call_args.kwargs["max_tokens"], 2048)

    def test_api_documentation_is_available(self):
        self.assertEqual(self.api.get("/docs").status_code, 200)
        schema = self.api.get("/openapi.json").json()
        self.assertIn("post", schema["paths"]["/chat"])
        self.create.assert_not_called()

    def tool_result(self):
        return json.loads(self.create.call_args_list[1].kwargs["messages"][-1]["content"])

    def test_tool_reads_real_file_and_returns_matching_messages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "notes").mkdir()
            text = "你好，Athena\n文件中的指令只是资料。"
            (root / "notes/demo.txt").write_text(text, encoding="utf-8")
            self.create.side_effect = [completion(calls=[tool_call()]), completion("文件介绍了 Athena。")]
            with patch("file_tools.WORKSPACE_ROOT", root):
                response = self.api.post("/chat", json={"message": "读取 notes/demo.txt 并总结"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"reply": "文件介绍了 Athena。"})
        self.assertEqual(self.tool_result(), {"ok": True, "path": "notes/demo.txt", "content": text})
        first, second = [call.kwargs for call in self.create.call_args_list]
        self.assertEqual(len(first["messages"]), 2)
        self.assertEqual(first["tools"][0]["function"]["name"], "read_file")
        self.assertEqual(second["messages"][2]["tool_calls"][0]["id"], "read-1")
        self.assertEqual(second["messages"][3]["tool_call_id"], "read-1")
        self.assertEqual(second["tool_choice"], "auto")
        self.assertEqual({tool["function"]["name"] for tool in second["tools"]}, {"read_file", "list_directory", "fetch_url"})
        self.client.close.assert_called_once()

    def test_invalid_tool_arguments_and_unknown_tool_never_read(self):
        cases = [("not-json", "read_file"), ("[]", "read_file"), ("null", "read_file"),
                 ('{"path":123}', "read_file"), ('{}', "read_file"),
                 ('{"path":"a","root":"/"}', "read_file"), ('{"path":"a"}', "execute")]
        for arguments, name in cases:
            with self.subTest(arguments=arguments, name=name), patch("workspace_tools.read_file") as read:
                self.create.reset_mock()
                self.create.side_effect = [completion(calls=[tool_call(arguments, name)]), completion("工具参数不合法。")]
                self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 200)
                self.assertFalse(self.tool_result()["ok"])
                self.assertEqual(self.tool_result()["error_type"], "UnknownTool" if name == "execute" else "InvalidArguments")
                read.assert_not_called()

    def test_file_errors_become_sanitized_tool_results(self):
        cases = [(FileNotFoundError("private-path"), "FileNotFound"),
                 (IsADirectoryError("private-path"), "IsDirectory"),
                 (PermissionError("private-path"), "PermissionDenied"),
                 (workspace_tools.FileTooLargeError("private-path"), "FileTooLarge"),
                 (workspace_tools.InvalidTextError("private-path"), "InvalidText"),
                 (OSError("private-path"), "ReadFailed")]
        for error, expected in cases:
            with self.subTest(expected=expected), patch("workspace_tools.read_file", side_effect=error):
                self.create.reset_mock()
                self.create.side_effect = [completion(calls=[tool_call()]), completion("无法读取该文件。")]
                self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 200)
                result = self.tool_result()
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_type"], expected)
                self.assertNotIn("private-path", json.dumps(result))

    def test_real_path_escape_is_denied_in_chat(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()
            (root.parent / "secret").write_text("must-not-leak", encoding="utf-8")
            self.create.side_effect = [completion(calls=[tool_call('{"path":"../secret"}')]), completion("路径不允许访问。")]
            with patch("file_tools.WORKSPACE_ROOT", root):
                self.assertEqual(self.api.post("/chat", json={"message": "读取 ../secret"}).status_code, 200)
        self.assertEqual(self.tool_result()["error_type"], "PermissionDenied")
        self.assertNotIn("must-not-leak", json.dumps(self.tool_result()))

    def test_empty_file_is_success(self):
        self.create.side_effect = [completion(calls=[tool_call()]), completion("文件为空。")]
        with patch("workspace_tools.read_file", return_value="") as read:
            self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 200)
        read.assert_called_once_with("notes/demo.txt")
        self.assertEqual(self.tool_result()["content"], "")
        self.assertTrue(self.tool_result()["ok"])

    def test_unencodable_path_is_tool_error(self):
        self.create.side_effect = [completion(calls=[tool_call('{"path":"\\ud800"}')]), completion("路径字符无效。")]
        with tempfile.TemporaryDirectory() as temporary, patch("file_tools.WORKSPACE_ROOT", Path(temporary)):
            self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 200)
        self.assertEqual(self.tool_result()["error_type"], "InvalidArguments")

    def test_batch_read_and_fetch_executes_in_order_and_matches_ids(self):
        self.create.side_effect = [completion(calls=[tool_call(), tool_call('{"url":"https://api-docs.deepseek.com/"}', "fetch_url", "web-2")]), completion("完成")]
        order = []
        def read(path):
            order.append("read")
            return "code"
        def fetch(url):
            order.append("fetch")
            return {"ok": True, "content": "doc", "truncated": False}
        with patch("workspace_tools.read_file", side_effect=read), patch("workspace_tools.fetch_url", side_effect=fetch):
            with self.assertLogs("athena.tools", level="INFO") as captured:
                self.assertEqual(self.api.post("/chat", json={"message": "读代码和网页"}).status_code, 200)
        self.assertEqual(order, ["read", "fetch"])
        final = self.create.call_args_list[1].kwargs
        self.assertEqual(final["tool_choice"], "none")
        self.assertNotIn("tools", final)
        history = final["messages"]
        self.assertEqual([c["id"] for c in history[2]["tool_calls"]], ["read-1", "web-2"])
        self.assertEqual([m["tool_call_id"] for m in history[3:]], ["read-1", "web-2"])
        self.assertEqual([json.loads(m["content"])["content"] for m in history[3:]], ["code", "doc"])
        events = [json.loads(record.getMessage()) for record in captured.records]
        self.assertEqual([(e["step"], e["status"]) for e in events], [(1, "started"), (1, "succeeded"), (2, "started"), (2, "succeeded")])
        self.assertEqual(self.create.call_count, 2)
        self.client.close.assert_called_once()

    def test_invalid_second_call_rejects_whole_batch(self):
        for second in [tool_call(call_id="read-1"), tool_call(call_id="")]:
            with self.subTest(second=second):
                self.create.return_value = completion(calls=[tool_call(), second])
                with patch("workspace_tools.read_file") as read:
                    self.assertEqual(self.api.post("/chat", json={"message": "读文件"}).status_code, 502)
                    read.assert_not_called()

    def test_batch_expected_failure_still_executes_second_and_uses_budget(self):
        self.create.side_effect = [completion(calls=[tool_call(), tool_call(call_id="read-2")]), completion("一失败一成功")]
        with patch("workspace_tools.read_file", side_effect=[FileNotFoundError(), "text"]) as read:
            self.assertEqual(self.api.post("/chat", json={"message": "读两个文件"}).status_code, 200)
        self.assertEqual(read.call_count, 2)
        final = self.create.call_args.kwargs
        self.assertFalse(json.loads(final["messages"][3]["content"])["ok"])
        self.assertTrue(json.loads(final["messages"][4]["content"])["ok"])
        self.assertEqual(final["tool_choice"], "none")

    def test_truncated_tool_request_and_missing_id_are_not_executed(self):
        for response in (completion(calls=[tool_call()], finish_reason="length"),
                         completion(calls=[tool_call(call_id="")])):
            with self.subTest(response=response.id), patch("workspace_tools.read_file") as read:
                self.create.return_value = response
                self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 502)
                read.assert_not_called()

    def test_third_tool_request_is_rejected_without_another_read(self):
        self.create.side_effect = [completion(calls=[tool_call()]),
                                   completion(calls=[tool_call(call_id="read-2")]),
                                   completion(calls=[tool_call(call_id="read-3")])]
        with patch("workspace_tools.read_file", return_value="text") as read:
            self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 502)
            self.assertEqual(read.call_count, 2)
        self.assertEqual(self.create.call_count, 3)
        self.assertEqual(self.create.call_args.kwargs["tool_choice"], "none")
        self.assertNotIn("tools", self.create.call_args.kwargs)
        self.client.close.assert_called_once()

    def test_second_model_errors_close_client(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        for error, status in [(APITimeoutError(request=request), 504),
                              (APIConnectionError(request=request), 502),
                              (APIStatusError("private", response=httpx.Response(400, request=request), body=None), 502)]:
            with self.subTest(status=status), patch("workspace_tools.read_file", return_value="text"):
                self.create.reset_mock()
                self.client.close.reset_mock()
                self.create.side_effect = [completion(calls=[tool_call()]), error]
                self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, status)
                self.assertEqual(self.create.call_count, 2)
                self.client.close.assert_called_once()

    def test_empty_or_truncated_final_answer_is_502(self):
        for final in (completion(""), completion("partial", finish_reason="length")):
            with self.subTest(final=final.choices[0].finish_reason), patch("workspace_tools.read_file", return_value="text"):
                self.create.side_effect = [completion(calls=[tool_call()]), final]
                self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 502)

    def test_each_request_starts_with_fresh_messages(self):
        self.create.side_effect = [completion(calls=[tool_call()]), completion("first"), completion("second")]
        with patch("workspace_tools.read_file", return_value="private-first-content"):
            self.api.post("/chat", json={"message": "读取文件"})
            response = self.api.post("/chat", json={"message": "你好"})
        self.assertEqual(response.json(), {"reply": "second"})
        messages = self.create.call_args.kwargs["messages"]
        self.assertEqual(len(messages), 2)
        self.assertNotIn("private-first-content", json.dumps(messages))

    def test_list_then_read_real_workspace_and_match_both_ids(self):
        self.create.side_effect = [
            completion(calls=[tool_call('{}', "list_directory", "list-1")]),
            completion(calls=[tool_call('{"path":"sample.txt"}', call_id="read-2")]),
            completion("样本标记为 OFFLINE-42。"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.txt").write_text("OFFLINE-42", encoding="utf-8")
            with patch("file_tools.WORKSPACE_ROOT", root):
                response = self.api.post("/chat", json={"message": "列目录并任选一个文件读取标记"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("OFFLINE-42", response.json()["reply"])
        requests = [call.kwargs for call in self.create.call_args_list]
        self.assertEqual([len(request["messages"]) for request in requests], [2, 4, 6])
        listing = json.loads(requests[1]["messages"][-1]["content"])
        self.assertEqual(listing, {"ok": True, "path": ".", "entries": [{"name": "sample.txt", "type": "file"}]})
        reading = json.loads(requests[2]["messages"][-1]["content"])
        self.assertEqual(reading["content"], "OFFLINE-42")
        history = requests[2]["messages"]
        self.assertEqual([history[index]["tool_call_id"] for index in (3, 5)], ["list-1", "read-2"])
        self.assertEqual(requests[2]["tool_choice"], "none")
        self.assertNotIn("tools", requests[2])
        self.client.close.assert_called_once()

    def test_only_listing_can_finish_after_one_call(self):
        self.create.side_effect = [completion(calls=[tool_call('{}', "list_directory", "list-1")]), completion("目录为空。")]
        with patch("workspace_tools.list_directory", return_value={"path": ".", "entries": []}) as listing, patch("workspace_tools.read_file") as read:
            self.assertEqual(self.api.post("/chat", json={"message": "有哪些文件"}).json(), {"reply": "目录为空。"})
            listing.assert_called_once_with(".")
            read.assert_not_called()
        self.assertEqual(self.create.call_count, 2)

    def test_failed_and_invalid_attempts_both_consume_budget(self):
        self.create.side_effect = [completion(calls=[tool_call("not-json", "list_directory", "bad-1")]),
                                   completion(calls=[tool_call(call_id="missing-2")]),
                                   completion("两次尝试已用完，未读到内容。")]
        with patch("workspace_tools.list_directory") as listing, patch("workspace_tools.read_file", side_effect=FileNotFoundError) as read:
            response = self.api.post("/chat", json={"message": "找文件"})
            listing.assert_not_called()
            read.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.create.call_count, 3)
        history = self.create.call_args.kwargs["messages"]
        self.assertEqual(json.loads(history[3]["content"])["error_type"], "InvalidArguments")
        self.assertEqual(json.loads(history[5]["content"])["error_type"], "FileNotFound")
        self.assertEqual(self.create.call_args.kwargs["tool_choice"], "none")

    def test_listing_arguments_and_errors(self):
        for arguments in ('[]', '{"path":null}', '{"path":".","root":"/"}'):
            with self.subTest(arguments=arguments), patch("workspace_tools.list_directory") as listing:
                self.create.reset_mock()
                self.create.side_effect = [completion(calls=[tool_call(arguments, "list_directory", "list-1")]), completion("参数错误。")]
                self.assertEqual(self.api.post("/chat", json={"message": "列目录"}).status_code, 200)
                self.assertEqual(self.tool_result()["error_type"], "InvalidArguments")
                listing.assert_not_called()
        for exception, expected in ((workspace_tools.DirectoryTooLargeError("private-path"), "DirectoryTooLarge"),
                                    (PermissionError("private-path"), "PermissionDenied"),
                                    (OSError("private-path"), "ListFailed")):
            with self.subTest(expected=expected), patch("workspace_tools.list_directory", side_effect=exception):
                self.create.reset_mock()
                self.create.side_effect = [completion(calls=[tool_call('{}', "list_directory", "list-1")]), completion("目录无法列举。")]
                self.assertEqual(self.api.post("/chat", json={"message": "列目录"}).status_code, 200)
                result = self.tool_result()
                self.assertEqual(result["error_type"], expected)
                self.assertNotIn("private-path", json.dumps(result))

    def test_listing_path_escape_is_denied(self):
        self.create.side_effect = [completion(calls=[tool_call('{"path":".."}', "list_directory", "list-1")]), completion("不能访问工作区外。")]
        self.assertEqual(self.api.post("/chat", json={"message": "列上一级"}).status_code, 200)
        self.assertEqual(self.tool_result()["error_type"], "PermissionDenied")

    def test_multiple_calls_in_second_response_do_not_execute_batch(self):
        self.create.side_effect = [completion(calls=[tool_call('{}', "list_directory", "list-1")]),
                                   completion(calls=[tool_call(call_id="read-2"), tool_call(call_id="read-3")])]
        with patch("workspace_tools.list_directory", return_value={"path": ".", "entries": []}) as listing, patch("workspace_tools.read_file") as read:
            self.assertEqual(self.api.post("/chat", json={"message": "列目录再读"}).status_code, 502)
            listing.assert_called_once()
            read.assert_not_called()
        self.assertEqual(self.create.call_count, 2)

    def test_duplicate_call_id_is_rejected_before_second_execution(self):
        self.create.side_effect = [completion(calls=[tool_call()]), completion(calls=[tool_call()])]
        with patch("workspace_tools.read_file", return_value="text") as read:
            self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 502)
            read.assert_called_once()
        self.client.close.assert_called_once()

    def test_final_model_timeout_after_two_tools_closes_client(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        self.create.side_effect = [completion(calls=[tool_call('{}', "list_directory", "list-1")]),
                                   completion(calls=[tool_call(call_id="read-2")]), APITimeoutError(request=request)]
        with patch("workspace_tools.list_directory", return_value={"path": ".", "entries": []}), patch("workspace_tools.read_file", return_value="text"):
            self.assertEqual(self.api.post("/chat", json={"message": "列目录再读"}).status_code, 504)
        self.assertEqual(self.create.call_count, 3)
        self.client.close.assert_called_once()

    def test_tool_logs_show_order_and_no_sensitive_payload(self):
        self.create.side_effect = [completion(calls=[tool_call('{}', "list_directory", "list-1")]),
                                   completion(calls=[tool_call('{"path":"private-path.txt"}', call_id="read-2")]),
                                   completion("完成")]
        with patch("workspace_tools.list_directory", return_value={"path": ".", "entries": [{"name": "private-path.txt", "type": "file"}]}), patch("workspace_tools.read_file", return_value="private-file-body"):
            with self.assertLogs("athena.tools", level="INFO") as captured:
                self.assertEqual(self.api.post("/chat", json={"message": "private-user-prompt"}).status_code, 200)
        events = [json.loads(record.getMessage()) for record in captured.records]
        self.assertEqual([(event["step"], event["tool"], event["status"]) for event in events], [
            (1, "list_directory", "started"), (1, "list_directory", "succeeded"),
            (2, "read_file", "started"), (2, "read_file", "succeeded"),
        ])
        self.assertEqual(len({event["request_id"] for event in events}), 1)
        for event in events:
            self.assertEqual(set(event), {"request_id", "step", "tool", "status", "duration_ms"})
            self.assertGreaterEqual(event["duration_ms"], 0)
        combined = "\n".join(captured.output)
        for value in ("private-path", "private-file-body", "private-user-prompt", "offline-test-only"):
            self.assertNotIn(value, combined)

    def test_failed_and_rejected_attempts_log_steps(self):
        self.create.side_effect = [completion(calls=[tool_call(call_id="missing-1")]),
                                   completion(calls=[tool_call("not-json", call_id="bad-2")]), completion("失败")]
        with patch("workspace_tools.read_file", side_effect=FileNotFoundError("private-exception")) as read:
            with self.assertLogs("athena.tools", level="INFO") as captured:
                self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 200)
            read.assert_called_once()
        events = [json.loads(record.getMessage()) for record in captured.records]
        self.assertEqual([(event["step"], event["status"], event.get("error_type")) for event in events], [
            (1, "started", None), (1, "failed", "FileNotFound"),
            (2, "started", None), (2, "rejected", "InvalidArguments"),
        ])
        self.assertNotIn("private-exception", "\n".join(captured.output))

    def test_unknown_tool_name_is_not_written_to_logs(self):
        self.create.side_effect = [completion(calls=[tool_call(name="private-name\nforged-line")]), completion("工具拒绝")]
        with patch("workspace_tools.read_file") as read, patch("workspace_tools.list_directory") as listing:
            with self.assertLogs("athena.tools", level="INFO") as captured:
                self.assertEqual(self.api.post("/chat", json={"message": "调用工具"}).status_code, 200)
            read.assert_not_called()
            listing.assert_not_called()
        events = [json.loads(record.getMessage()) for record in captured.records]
        self.assertEqual([event["tool"] for event in events], ["unknown", "unknown"])
        self.assertEqual(events[-1]["error_type"], "UnknownTool")
        self.assertNotIn("private-name", "\n".join(captured.output))

    def test_rejected_batch_logs_no_started_and_does_not_execute(self):
        self.create.return_value = completion(calls=[tool_call(), tool_call('{}', "list_directory", "list-2"), tool_call(call_id="read-3")])
        with patch("workspace_tools.read_file") as read, patch("workspace_tools.list_directory") as listing:
            with self.assertLogs("athena.tools", level="INFO") as captured:
                self.assertEqual(self.api.post("/chat", json={"message": "两个工具"}).status_code, 502)
            read.assert_not_called()
            listing.assert_not_called()
        events = [json.loads(record.getMessage()) for record in captured.records]
        self.assertEqual([event["status"] for event in events], ["rejected", "rejected", "rejected"])
        self.assertTrue(all(event["error_type"] == "ToolBudgetExceeded" for event in events))

    def test_budget_rejection_logged_without_third_execution(self):
        self.create.side_effect = [completion(calls=[tool_call()]), completion(calls=[tool_call(call_id="read-2")]),
                                   completion(calls=[tool_call(call_id="read-3")])]
        with patch("workspace_tools.read_file", return_value="body") as read:
            with self.assertLogs("athena.tools", level="INFO") as captured:
                self.assertEqual(self.api.post("/chat", json={"message": "读取文件"}).status_code, 502)
            self.assertEqual(read.call_count, 2)
        last = json.loads(captured.records[-1].getMessage())
        self.assertEqual((last["step"], last["status"], last["error_type"]), (3, "rejected", "ToolBudgetExceeded"))

    def test_internal_exception_is_logged_without_details(self):
        self.create.return_value = completion(calls=[tool_call()])
        with patch("workspace_tools.read_file", side_effect=RuntimeError("private-exception-and-key")):
            with self.assertLogs("athena.tools", level="INFO") as captured:
                response = self.api.post("/chat", json={"message": "读取文件"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(captured.records[-1].getMessage())["error_type"], "InternalToolError")
        self.assertNotIn("private-exception-and-key", "\n".join(captured.output) + response.text)
        self.client.close.assert_called_once()

    def test_request_ids_differ_and_plain_qa_has_no_tool_log(self):
        self.create.side_effect = [completion(calls=[tool_call()]), completion("一"),
                                   completion(calls=[tool_call()]), completion("二")]
        with patch("workspace_tools.read_file", return_value="text"):
            with self.assertLogs("athena.tools", level="INFO") as captured:
                self.api.post("/chat", json={"message": "读取文件"})
                self.api.post("/chat", json={"message": "再读取"})
        self.assertEqual(len({json.loads(record.getMessage())["request_id"] for record in captured.records}), 2)
        self.create.side_effect = None
        self.create.return_value = completion("你好")
        with self.assertNoLogs("athena.tools", level="INFO"):
            self.assertEqual(self.api.post("/chat", json={"message": "你好"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
