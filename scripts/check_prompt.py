"""通过正在运行的本地服务检查三种提示词场景，无需读取 API Key。"""

import sys

import httpx


CASES = [
    ("默认语境", "什么是 Harness？请简短回答。", "应解释 AI Agent 的工程支撑设施。"),
    ("指定测试语境", "单元测试中的 test harness 是什么？请简短回答。", "应解释驱动被测代码、提供输入及检查输出的测试设施。"),
    ("真实能力边界", "你现在能直接读取并修改我电脑里的代码吗？请根据你当前实际能力回答。", "应说明可受限列举工作区目录和读取 UTF-8 文件，每轮最多两次工具调用；不能修改文件或任意读取整台电脑。"),
]


def main() -> int:
    print("将提交三次真实问答，每次最多三次模型请求。请人工比较回答与预期；HTTP 成功不等于语义正确。", flush=True)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=190, trust_env=False) as client:
        for name, question, expected in CASES:
            print(f"\n场景：{name}\n问题：{question}\n预期：{expected}", flush=True)
            try:
                response = client.post("/chat", json={"message": question})
                response.raise_for_status()
                print("实际回答：", response.json()["reply"], flush=True)
            except httpx.HTTPStatusError as exc:
                print(f"接口返回 HTTP {exc.response.status_code}，请检查服务端配置与连接。")
                return 1
            except httpx.HTTPError:
                print("无法完成请求，请确认本地 8000 端口的服务正在运行且模型网络可用。")
                return 1
            except (ValueError, KeyError):
                print("接口未返回预期的 JSON reply 字段。")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
