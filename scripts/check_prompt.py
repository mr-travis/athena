"""通过本地服务复测提示词；展示证据供人工验收，不读取 API Key。"""

import argparse
import json
import sys

import httpx


CASES = [
    ("默认语境", "什么是 Harness？请简短回答。", "应解释 AI Agent 的工程支撑设施。"),
    ("指定测试语境", "单元测试中的 test harness 是什么？请简短回答。", "应解释驱动被测代码、提供输入及检查输出的测试设施。"),
    ("真实能力边界", "你现在能直接读取并修改我电脑里的代码吗？请根据你当前实际能力回答。", "应说明可受限列举工作区目录、读取 UTF-8 文件及用户明确提供的白名单文档网页；每轮最多两次工具尝试，不能修改文件、执行命令、搜索网页或任意读取整台电脑，无跨请求记忆。"),
]

REVIEW_CASE = (
    "代码审阅证据",
    "请读取 deepseek_client.py，再读取 https://api-docs.deepseek.com/，核对代码与文档。"
    "api_key 是演示占位值，请忽略，不要将其列为缺陷。请给出实际代码、判断依据和影响，"
    "区分已证实问题、待验证风险与可选改进。不要执行样本代码。",
    "应识别将文档站用作 API base_url 的预设错误；不虚构 thinking 等参数写法，"
    "不把示例中的可选配置缺省或占位密钥列为已证实错误；总结数量与正文分类一致。"
    "核对工具结果是否实际包含文件和网页正文；工具失败、缺少证据或正文截断时，不能视为完整验收通过。",
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true", help="追加代码审阅场景，显示实际工具结果（包含正文）")
    args = parser.parse_args(argv)
    cases = CASES + ([REVIEW_CASE] if args.review else [])
    print(f"将提交 {len(cases)} 次真实问答，每次最多三次模型请求，按模型账户规则计费。请人工比较回答与预期；HTTP 成功不等于语义正确。", flush=True)
    if args.review:
        print("审阅前提：服务端工作区已有非敏感演示文件 deepseek_client.py，包含预设 base_url 错误；文件和公开网页正文将发送给模型，并在终端显示。不创建、复制或执行样本。", flush=True)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=190, trust_env=False) as client:
        for name, question, expected in cases:
            print(f"\n场景：{name}\n问题：{question}\n预期：{expected}", flush=True)
            review = name == REVIEW_CASE[0]
            try:
                payload = {"message": question}
                if review:
                    payload["debug"] = True
                response = client.post("/chat", json=payload)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("reply"), str) or not data["reply"].strip():
                    raise ValueError("缺少有效 reply")
                print("实际回答：", data["reply"], flush=True)
                if review:
                    results = data.get("tool_results")
                    if not isinstance(results, list):
                        raise ValueError("缺少调试结果数组")
                    print("实际工具结果（含正文，供核对证据）：", flush=True)
                    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
                    print("请核对 read_file 与 fetch_url 的成功状态、正文及截断标记。空数组、失败结果或截断均不能自动通过验收。", flush=True)
            except httpx.HTTPStatusError as exc:
                print(f"接口返回 HTTP {exc.response.status_code}，请检查服务端配置与连接。")
                return 1
            except httpx.HTTPError:
                print("无法完成请求，请确认本地 8000 端口的服务正在运行且模型网络可用。")
                return 1
            except ValueError:
                print("接口未返回预期的 JSON 正文或调试结果，未完成验收。")
                return 1
    print("请求完成，语义验收待人工核对；退出码 0 仅表示请求及响应格式检查完成。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
