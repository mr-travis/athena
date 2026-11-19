"""人工组合验收样本：仅供读取和审阅，不执行，不包含真实密钥。"""

from openai import OpenAI

client = OpenAI(
    api_key="DEMO_KEY_DO_NOT_USE",
    base_url="https://api-docs.deepseek.com",
)

response = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=[{"role": "user", "content": "你好，请介绍自己。"}],
    stream=False,
)

print(response.choices[0].message.content)
