"""验收脚本的请求与证据展示测试；不访问服务或真实模型。"""

from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

import httpx

from scripts import check_prompt


class CheckPromptTests(unittest.TestCase):
    def run_script(self, argv, handler):
        output = io.StringIO()
        client = httpx.Client(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler))
        with patch.object(check_prompt.httpx, "Client", return_value=client), redirect_stdout(output):
            code = check_prompt.main(argv)
        return code, output.getvalue()

    def test_default_sends_three_questions_without_debug(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"reply": "示例回答"})

        code, output = self.run_script([], handler)
        self.assertEqual(code, 0)
        self.assertEqual(requests, [{"message": case[1]} for case in check_prompt.CASES])
        self.assertIn("语义验收待人工核对", output)

    def test_review_displays_failed_and_truncated_evidence_without_claiming_pass(self):
        requests = []
        results = [
            {"step": 1, "tool": "read_file", "result": {"ok": False, "error_type": "FileNotFoundError"}},
            {"step": 2, "tool": "fetch_url", "result": {"ok": True, "content": "截断正文样例", "truncated": True}},
        ]

        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            data = {"reply": "示例回答"}
            if payload.get("debug"):
                data["tool_results"] = results
            return httpx.Response(200, json=data)

        code, output = self.run_script(["--review"], handler)
        self.assertEqual(code, 0)
        self.assertEqual(len(requests), 4)
        self.assertEqual(requests[-1], {"message": check_prompt.REVIEW_CASE[1], "debug": True})
        self.assertTrue(all("debug" not in item for item in requests[:-1]))
        self.assertIn("FileNotFoundError", output)
        self.assertIn("截断正文样例", output)
        self.assertIn('"truncated": true', output)
        self.assertIn("语义验收待人工核对", output)

    def test_http_and_invalid_responses_stop_following_requests(self):
        for status, body in [(502, {"detail": "连接失败"}), (200, []), (200, {"reply": " "})]:
            with self.subTest(status=status, body=body):
                requests = []

                def handler(request):
                    requests.append(request)
                    return httpx.Response(status, json=body)

                code, output = self.run_script([], handler)
                self.assertEqual(code, 1)
                self.assertEqual(len(requests), 1)
                self.assertNotIn("请求完成，", output)

    def test_review_requires_debug_array(self):
        code, output = self.run_script(["--review"], lambda request: httpx.Response(200, json={"reply": "示例回答"}))
        self.assertEqual(code, 1)
        self.assertIn("未完成验收", output)


if __name__ == "__main__":
    unittest.main()
