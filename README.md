# Athena · 雅典娜

基于 Harness 架构的 Vibe Coding 智能体项目。目标是将自然语言需求逐步转化为可运行、可验证的软件变更。本项目全过程使用gpt开发，不涉及任何古法编程

`/chat` 已接入受控目录列举、文件读取和白名单网页读取：每轮最多两次工具尝试（含失败）、三次模型请求，不保留跨请求历史。工作区由安装者配置，默认 `~/athena-workspace/`。工具尝试会输出终端 JSON 记录；当前 84 项离线测试通过；文件与网页组合已根据用户提供的真实回答及工具日志完成成功样例验收。另有独立 SQLite 业务运行记录模块，尚未接入 `/chat`。

第一次模型接入已完成；FastAPI 单轮问答接口也已通过离线测试和用户真实调用验收。协作遵循上层 `../AGENTS.md`：先讲解和对齐，再按一个小步骤实施、验证并记录。

HTTP 客户端依赖使用 `httpx[socks]`，支持安装环境已有的 SOCKS 代理；执行 `uv sync` 会同步安装 `socksio`。安装依赖后应重启已运行的服务，使其重新加载依赖。不会修改或清除系统代理设置。

## 当前工具代码分层

- `workspace_tools.py`：`WORKSPACE_TOOL_NAMES` 是分发与日志共用的不可变工具名白名单；`WORKSPACE_TOOLS` 向模型描述工具；`execute_workspace_tool()` 校验白名单和 JSON 参数、分发调用，将结果与预期异常转换为结构化反馈。
- `file_tools.py`：实现 `list_directory()` 与 `read_file()`，落实工作区路径、符号链接、文件类型、编码和大小/条目上限。
- `tool_middleware.py`：以普通函数包装工具分发，统一记录开始、终态和耗时；提供共用日志函数，保留意外工具异常的脱敏 HTTP 500 映射。
- `prompt.py`：集中保存身份、领域语境、当前能力、资料可信度和代码审阅规则。
- `main.py`：导入提示词，保留 HTTP 接口、模型调用循环、协议与次数控制及调试结果收集；执行前拒绝由循环决定，再调用共用日志函数记录。

工具声明不代替后端参数校验，提示词不代替文件访问检查。声明和分发已从入口提取，接口格式、两次工具尝试/三次模型请求及日志行为保持不变。2026-11-17：提取后全部 60 项离线测试通过，未重复调用真实模型。

## 项目资料
- 重要决定：[`docs/decisions.md`](docs/decisions.md)
- 学习进度：[`docs/progress.md`](docs/progress.md)

## 当前环境

- 本地开发环境：macOS。
- 项目目录：`/Users/travis/harnesscoding/athena`。
- 已创建 `.venv`，其中 Python 版本为 3.13.12。
- 本步使用现有 Python 3.13，最小依赖为 OpenAI SDK 与 python-dotenv；用户已在本地完成安装并运行成功。

## 环境使用与验证

在终端中进入项目并激活已有虚拟环境：

```bash
cd /Users/travis/harnesscoding/athena
source .venv/bin/activate
python --version
```

也可以不激活，直接使用项目解释器：

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python -c 'import sys; print(sys.version); print(sys.prefix)'
```

后一条命令已验证通过，解释器前缀指向项目的 `.venv`。退出已激活的环境使用 `deactivate`。

## 第一次模型调用（保留的终端入口）

本步只验证模型连接，尚不包含工具调用或 Agent 循环。

1. 在项目目录安装最小依赖：

```bash
cd /Users/travis/harnesscoding/athena
uv sync
```

2. 复制配置模板（仅首次操作；已有 `.env` 时不要覆盖）：

```bash
cp -n .env.example .env
```

在本地编辑 `.env`，填写 `DEEPSEEK_API_KEY`，不要将密钥发到聊天中。默认使用课程中的 `deepseek-v4-pro`，服务地址为 `https://api.deepseek.com`。已存在的环境变量优先于 `.env`。

3. 运行程序：

```bash
.venv/bin/python main.py
```

预期结果：终端显示一句中文介绍。此命令使用当前问答流程，固定问候通常只需一次真实 API 请求，按账户规则计费。未填写密钥时应提示配置；网络或接口失败时应显示中文错误提示。

## 验证状态

- Python 语法与 TOML 解析通过。
- 通过离线替身验证：缺少密钥、成功回复、连接失败、超时、HTTP 401 的处理。
- 早先助手执行环境的 `uv sync` 因 PyPI DNS 解析失败；之后用户在本地报告安装完成。
- 2026-11-17：用户执行 `.venv/bin/python main.py` 并提供真实回复：“你好，我是 Athena，一名编程学习助手，很高兴帮助你解决编程问题！”本步真实调用验收通过，依据为用户运行反馈，助手未重复请求。

接口参考：[DeepSeek 官方首次调用文档](https://api-docs.deepseek.com/)。

## FastAPI 单轮问答接口

在项目目录安装新增依赖并启动服务：

```bash
cd /Users/travis/harnesscoding/athena
uv sync
.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

`main:app` 指 `main.py` 中的 FastAPI 实例 `app`；`--reload` 用于本地开发时在代码变化后重新加载。已有 `.env` 继续使用，不要覆盖。

浏览器打开 <http://127.0.0.1:8000/docs>，展开 `POST /chat`，点击 **Try it out**，填写：

```json
{"message": "请用简单的话解释什么是 Harness"}
```

点击 **Execute**，预期 HTTP 200，响应格式为 `{"reply": "模型的回答"}`。每次有效调用会请求真实模型；本接口是单轮、非流式，不保留历史。普通问答通常一次模型请求，涉及工具时最多三次，增加耗时与额度消耗。停止服务使用 `Ctrl+C`。直接运行 `python main.py` 仍是固定问候的终端演示，不会启动 HTTP 服务。

| 状态码 | 含义 |
|---|---|
| 200 | 返回模型回复 |
| 422 | message 缺失、非字符串、去空白后为空或超过 10000 字符 |
| 503 | 服务端没有配置 API Key |
| 504 | 模型请求超时 |
| 502 | 模型连接失败、上游 HTTP 报错、输出无效或截断、工具调用协议或次数不符合限制 |

错误响应使用 `{"detail": "错误说明"}`；输入校验错误由 FastAPI 返回结构化 detail。

## 接口离线测试

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python -m unittest discover -s tests -v
```

测试使用真实 FastAPI TestClient 和模拟模型客户端，不加载本地 `.env`、不调用真实模型。覆盖用户输入传递、回复、无效输入、缺少密钥、上游失败、空回复和接口文档。

2026-11-17：用户通过 `/docs` 提供真实模型回复；助手在当前项目环境运行上述命令，6 项测试全部通过。测试存在 Starlette 对 httpx 的弃用提示，但不影响本次通过。先前依赖安装阻塞已不影响接口测试。

系统提示词现位于 `prompt.py` 的 `SYSTEM_PROMPT` 常量中，由 `main.py` 导入。早期版本已补充项目语境：默认将 Harness 理解为 AI Agent Harness；用户明确询问 test harness 时按测试语境回答。同时要求模型如实描述当前单轮问答能力。提示词调整后 6 项接口回归测试通过；用户提供的三个真实场景回复经人工检查均符合预期。

使用 `--reload` 启动的服务会在代码保存后重新加载；未启用时需重启服务。可分别提问“什么是 Harness？”、“什么是 test harness？”和“你能直接修改我的本地文件吗？”，检查领域区分和能力边界。接口可用与回答是否准确需要分别评估，提示词不能保证模型始终正确。

## 独立只读文件工具

`file_tools.py` 提供 `read_file(path: str) -> str`。路径相对于服务端配置的工作区，与终端当前目录无关。默认使用当前用户主目录下的 `athena-workspace/`，在本机为 `/Users/travis/athena-workspace/`，其他安装者使用各自主目录，不写死 Travis 的路径。

可选：在本地 `.env` 设置 `ATHENA_WORKSPACE_ROOT` 为自己的绝对路径（支持 `~/` 展开）。环境变量优先于 `.env`；未设置使用默认值，空值、相对路径或包含 `..` 的配置会在启动时明确报错。配置由 `settings.py` 在进程启动时加载，修改后应重启服务；模型参数不能改变根目录。

例如，你本机的显式配置可以是：

```dotenv
ATHENA_WORKSPACE_ROOT=/Users/travis/athena-workspace
```

工作区应选择与 Athena 源码分开的目录。程序不自动创建或迁移目录；本步未创建外部目录、未改动已有 `.env`。目录不存在时普通问答仍可使用，文件读取返回不存在错误，不会回退到旧 `athena/workspace/`。可在自己的终端手动创建默认工作区：

```bash
mkdir -p "$HOME/athena-workspace"
```

后续检查本机独立目录已存在且为空；浏览器请求旧样本返回不存在，符合预期。助手向新目录写测试样本的提权申请被自动审批拒绝，尚需安装者自行准备非敏感样本后验证成功读取。

随后安装者准备了 `workspace_check.txt`，助手通过 OpenCLI 操作现有 `/docs`，真实请求返回 HTTP 200，正确给出文件中的 `ATHENA-EXTERNAL-8462`，新工作区成功读取样例验收通过。助手未重启服务。新增或修改工作区文本无需重启；修改 `.env`/环境变量后需重启；Python 代码在 `--reload` 模式下通常自动重载，未启用时需重启。

在工作区准备 `notes/demo.txt` 后，从项目目录调用：

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python -c 'from file_tools import read_file; print(read_file("notes/demo.txt"))'
```

示例文件需自行准备；不存在时会报 `FileNotFoundError`。工具不会创建或修改文件。

- 只接受相对路径；拒绝空路径、NUL 和 `..` 路径分量。
- 拒绝工作区根目录和目标路径各级的符号链接，只读取普通文件。
- 上限为 65,536 字节，超限报 `FileTooLargeError`，不返回截断内容。
- 严格 UTF-8 解码，并拒绝 NUL 字符；不符合时报 `InvalidTextError`。空文件返回空字符串，原换行保留。
- 路径权限问题报 `WorkspacePermissionError`；文件不存在和目标是目录分别报 `FileNotFoundError`、`IsADirectoryError`。

使用 macOS/POSIX 的目录描述符、`O_NOFOLLOW` 和 `O_NONBLOCK`，无需新依赖，未验证 Windows。依赖可信的项目目录及其上级目录；该工具不是操作系统沙箱，不阻止其他程序修改文件、不保证并发修改时内容快照一致，也不检测硬链接来源。工作区仅应放入允许读取的资料。

独立验证（测试自动创建并清理临时工作区，不读取真实工作区或密钥）：

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python -m unittest discover -s tests -p test_file_tools.py -v
```

2026-11-17：10 项工具测试通过；全部 16 项测试通过，包括原有 6 项接口回归测试。未调用真实模型，原有 Starlette/httpx 弃用提示仍存在。

## 独立目录列举工具

`file_tools.py` 提供 `list_directory(path: str = ".") -> dict`，使用同一安装者工作区配置；既可独立调用，也已接入 `/chat`。

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python -c 'import json; from file_tools import list_directory; print(json.dumps(list_directory(), ensure_ascii=False, indent=2))'
.venv/bin/python -m unittest discover -s tests -p test_directory_tools.py -v
```

传入 `"notes"` 可列工作区中的该子目录；默认 `"."` 表示根目录。返回示例：

```json
{"path":".","entries":[{"name":"workspace_check.txt","type":"file"}]}
```

- 只列当前一层，不读取正文；返回名称与类型 `file`/`directory`，按名称排序。
- 拒绝绝对路径、`..`、空路径、NUL；根目录与各级均不跟随符号链接。
- 跳过隐藏条目（点开头）、符号链接及特殊文件。此过滤不改变已有读取权限。
- 最多 100 个可见条目；第 101 个触发 `DirectoryTooLargeError`，不返回截断列表。
- 不存在报 `FileNotFoundError`；链接、无效目录或打开权限问题报 `WorkspacePermissionError`。扫描期间的系统权限错误仍可能以 `PermissionError` 等 `OSError` 抛出。

2026-11-17：12 项目录测试和全部 45 项回归测试通过；本机独立工作区实际列举得到上述示例。未调用模型、未新增依赖。当前为 macOS/POSIX 实现；条目上限不限制隐藏条目扫描时间，目录并发变化无快照保证，后续读取仍会重新校验权限。

## 三场景提示词复测

`/chat` 当前开放 `read_file` 与 `list_directory`。能力边界场景的预期已更新为支持受限列举和读取、每轮最多两次工具尝试；旧版本三场景验收记录不替代本版本真实复测。

保持服务运行，在另一个终端执行：

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python scripts/check_prompt.py
```

脚本会提交三次问答，显示问题、预期和实际回答；需要人工判断语义，不会将 HTTP 200 自动标为回答正确。无需在脚本中填写密钥。此前单次读取版本已由助手通过 OpenCLI 完成三场景真实复测；本次目录接入后更新了能力边界预期，完整三场景尚未重测。结果及措辞局限记录于 `docs/progress.md`。这是样例验证，不保证所有问题都回答正确。

## 模型调用文件工具（当前步骤）

### 工具执行记录

工具日志通过 `athena.tools` 输出到服务终端（标准错误），每行一个 JSON，不创建日志文件，不改变 `/chat` 的 `reply` 格式。相同问答使用相同的程序生成 `request_id`，不同问答各自生成 ID。

| 字段 | 含义 |
|---|---|
| request_id | 本轮问答的随机 ID，不使用模型提供的 ID |
| step | 工具尝试序号；被拒绝的第三次请求可显示 3，但没有执行第三次工具 |
| tool | read_file、list_directory、fetch_url 或固定 unknown 占位符 |
| status | started、succeeded、failed 或 rejected |
| duration_ms | 工具分发尝试耗时，毫秒；开始及执行前拒绝为 0，不含模型请求时间 |
| error_type | 失败或拒绝的固定类别，成功时省略 |

`started` 表示开始一次分发尝试，包含参数校验，不等于文件已实际读取；随后输出终态。文件不存在等为 `failed`，未知工具、非法参数或权限拒绝为 `rejected`。整批超预算、协议无效或重复 ID 在执行前只记拒绝，同一批被拒绝调用共享 step。普通问答没有工具日志。

日志不包含路径、参数、提示词、目录结果、正文、密钥、模型调用 ID 或原始异常。意外工具异常记录 `InternalToolError` 并返回固定 HTTP 500 文案，不输出原异常堆栈。日志用于观察顺序和状态，不是持久化审计或恢复机制；进程被中断时可能只有 started。

2026-11-17：新增 7 项日志行为测试，全部 60 项测试通过，覆盖顺序、过滤、请求隔离、失败计数、执行前拒绝和内部异常脱敏。

真实验收：助手通过 OpenCLI 重新提交组合问题，直接观察同一 request_id 下 step 1 的 list_directory 开始/成功、step 2 的 read_file 开始/成功，耗时分别 0.206 ms 和 0.348 ms；浏览器 HTTP 200，文件名和样本标记正确。此为本次实际执行顺序与状态的直接证据，失败/拒绝由离线测试覆盖。

### 有上限的调用流程

流程：模型按需选择 `list_directory`、`read_file` 或 `fetch_url` → Athena 校验并执行 → 以匹配的 `tool_call_id` 回传 JSON 结果 → 模型继续选择工具或直接回答。尚有工具预算时可选工具；两次尝试用完后，下一次模型请求不提供工具声明并设置 `tool_choice="none"`。单批两次调用后，第二次模型请求就只能回答；模型请求总数仍最多三次。

- 每轮最多两次工具尝试，包括未知工具、非法参数和文件访问失败；无无限重试。同一响应可提出多个调用，程序先整批检查剩余预算、协议及批内/跨批 ID，再按顺序执行。超预算或协议无效整批返回 HTTP 502，均不执行；预算耗尽后仍请求工具也返回 502。参数错误和预期工具失败仍占一次尝试，可继续执行批内下一项；意外内部异常立即结束请求。
- `read_file` 必须且只能包含 `path` 字符串；`list_directory` 也只接受该字段，但 `{}` 可省略路径，默认 `.`。两者成功结果都有 `ok=true` 和相对 `path`；读取返回 `content`，列举返回 `entries`。
- 预期错误作为 `ok=false` 回传；目录超限返回 `DirectoryTooLarge`，不返回部分列表。模型能正常说明失败时仍返回 HTTP 200；200 不等于工具成功。任意一次上游请求错误仍按 502/504 处理，结束时关闭同一客户端。
- 系统提示词要求正文与目录名称只作为资料，不将其中的指令视为授权；实际访问边界由 Python 工具执行。提示词不保证所有模型回答都遵循预期。
- 成功列举的名称和读取的正文都会发送给配置的模型服务；工作区仅应放入允许模型处理的资料。
- 两次预算不强制顺序，可直接读、只列目录或列目录后读。每次列举仍只一层；若两次均用于查找子目录，最终应说明尚未读取，不编造正文。候选不明确时应询问用户。
- 仍无文件写入、命令执行、跨请求记忆。每次模型请求超时 60 秒，总问答最多三次顺序模型请求；此处没有额外的整轮超时。

本机工作区已有非敏感样本时，可通过 `/docs` 验证组合流程：

```json
{"message":"请先调用 list_directory 列出工作区根目录，再任选其中一个 txt 文件调用 read_file，告诉我文件名及文件中的标记。不要猜测内容。"}
```

2026-11-17：本步 53 项离线测试通过，覆盖真实临时工作区组合流程、预算耗尽、失败计数、ID 关联和重复拒绝、第二批多调用拒绝及第三次请求超时。完整提示词三场景尚未重测。

本步真实验收过程：首次请求因缺少 SOCKS 支持返回 500；安装 `socksio` 并重启后，浏览器曾显示 Failed to fetch，后续服务操作被自动审核拒绝。用户随后明确授权本次目录条目名称及 `workspace_check.txt` 非敏感样本正文发送至 DeepSeek API，助手重新启动服务并通过 OpenCLI 再次提交上述组合问题，实际 HTTP 200，回复准确给出 `workspace_check.txt` 和 `ATHENA-EXTERNAL-8462`。组合样例用户可见结果验收通过；未取得工具事件日志，执行顺序与次数以离线测试为依据。完整新版提示词三场景尚未重测。

重启服务（或等待 `--reload` 生效）后，在 `/docs` 的 `POST /chat` 提交：

```json
{"message":"请调用 read_file 读取 read_tool_demo.txt，告诉我文件中的样本标记。"}
```

旧验收样本保留在源码目录的 `workspace/read_tool_demo.txt`，现不再属于默认授权工作区；本步未迁移它。要复测成功读取，需先自行将非敏感样本放入所配置工作区，文件名为 `read_tool_demo.txt`，预期回复包含 `OWL-7319`。还可分别询问普通 Harness 问题、读取不存在的文件及工作区外的非敏感路径，人工检查直接回答、失败说明与越界拒绝。模型可能直接拒绝越界请求而不调用工具，两种行为均不能泄露工作区外内容。真实调用按账户规则计费。

2026-11-17 工作区配置调整：33 项离线测试通过，覆盖不同安装者主目录默认值、绝对路径覆盖、`~` 展开、非法配置和工作区不存在时不创建、不回退。实际读取当前配置得到 `/Users/travis/athena-workspace`，该目录尚不存在；新配置下真实文件问答尚未复测。以下为调整前的验收历史。

2026-11-17：28 项离线测试全部通过（18 项接口测试、10 项文件工具测试）。真实验收使用临时样本通过真实 FastAPI 路由尝试请求 DeepSeek，返回 502 连接失败、工具执行 0 次，临时样本已清理；未认定真实接入成功。协议参考：[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。

随后用户在本地 `/docs` 提供真实响应：回复正确给出 `OWL-7319`，并描述标记在第三行、前两行为用途和限制，与样本一致。据用户运行反馈，成功读取样例验收通过；助手未重复请求。此反馈不包含工具事件日志，不能单独证明执行次数；次数与访问限制已由离线测试验证。真实错误场景和提示词复测仍待完成。

后续助手通过已连通的 OpenCLI 操作浏览器 `/docs`，提交真实请求并观察结果：`missing_demo.txt` 返回 HTTP 200，明确不存在、未编造正文；`../README.md` 返回 HTTP 200，明确超出工作区范围、未返回正文。两个错误样例的用户可见行为符合预期。未读取密钥文件，未修改代码；工具是否实际调用不能仅由最终回复确定，后端边界依据离线测试。新版提示词三场景仍待复测。

## 受限网页读取（已接入模型）

`web_tools.fetch_url(url)` 可独立调用，也已接入 `/chat`，与两个文件工具共用每轮两次尝试预算。参数必须且只能包含字符串 `url`。

- `safe_http.py` 使用标准库 `http.client`、socket、ssl：只允许 `api-docs.deepseek.com` 的 HTTPS 443 地址，拒绝账号密码、非 ASCII 未编码路径、控制字符及其他目标。
- 全部 DNS 结果必须是允许的公网地址；拒绝组播及 IPv6 映射/转换/隧道地址。TCP 连接固定使用已检查的第一个地址，TLS 使用原域名及系统信任库验证证书，不修改全局 DNS、不读取环境代理、不自动重试其他地址。
- 只接受 HTTP 200、不跟随重定向、不接受压缩响应，仅接受 HTML/纯文本。下载上限 1 MiB，超限失败；严格 UTF-8 并拒绝 NUL。连接与读取操作超时 10 秒；DNS 及整次调用没有严格总时限，持续缓慢传输也可能延长总耗时。
- HTML 使用标准库 HTMLParser 提取基本文本，跳过 script/style/noscript，保留基本换行；不执行 JavaScript，不保证提取文章主体。
- 成功结果为 `ok`、`url`、`content`、`truncated`；正文最多 12000 字符，超出截断。失败仅返回 `ok=false`、固定 `error_type` 与 `message`，不回传底层异常。
- 直接 HTTPS 连接不会使用现有代理；依赖代理的环境可能无法访问。白名单目前为代码常量，并非已实现的环境配置项。

离线验证（模拟 DNS 与网络连接，不请求真实网页）：

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python -m unittest discover -s tests -p test_web_tools.py -v
```

2026-11-17：新增 9 项测试，全部 69 项离线测试通过。真实网络连通、真实网页兼容性及系统证书链尚未验收；本步不需要安装依赖。

真实读取尝试（2026-11-17）：独立调用 `fetch_url("https://api-docs.deepseek.com/")` 返回 `ok=false`、`DNSFailed`。当前执行环境域名解析失败，未获得页面正文，不能确认真实 TCP/TLS 或网页兼容性通过。未切换代理或绕过地址校验。可在本机终端复现：

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python -c 'from web_tools import fetch_url; r = fetch_url("https://api-docs.deepseek.com/"); print({k: v for k, v in r.items() if k != "content"}); print(r.get("content", "")[:500])'
```

后续只读排查确认：当前执行环境连无需 DNS 的数字 IP socket 连接也返回 `PermissionError: Operation not permitted`，dig 在 socket bind 阶段被拒绝。因此主要阻塞为当前执行环境网络权限，不能据此认定宿主机 DNS 故障；不应通过修改工具的地址校验或硬编码目标 IP 解决。需在具备网络权限的本机终端执行上述命令继续验收。


### 网页工具接入验证（2026-11-17）

全部 72 项离线测试通过，新增 3 项覆盖结果回传、日志脱敏、参数校验和共享预算。用户本机已反馈独立网页读取成功；聊天粘贴存在链接格式转换，原始输入未精确还原。当前代码离线确认拒绝 Markdown 链接且不解析 DNS。助手环境最新复测仍返回 DNSFailed，真实模型调用网页工具尚未验收。本步未启动或重启服务；--reload 通常自动加载代码，否则需重启。无需新增依赖。


2026-11-17：支持同轮多工具请求的顺序执行，全部 74 项离线测试通过；未添加未经确认有效的 parallel_tool_calls 参数。已有未启用 --reload 的服务需停止后按原命令重新启动，再提交相同组合问题。修正后真实复测尚未完成。


2026-11-17 组合真实复测：用户提供同一 request_id 下 read_file（step 1，1.432 ms）及 fetch_url（step 2，1510.324 ms）的 started/succeeded 日志，POST /chat 为 200；回答识别样本把文档站误作 API base_url 的预设错误，并未将占位密钥认定为缺陷。成功样例通过；可选参数被称作“参数不完整”的措辞仍不够准确。日志证明顺序和状态，不能单独证明模型原始响应是否将调用放在同一批。助手执行环境网络权限 TODO 独立保留，未重复联网。


## 可选调试结果

POST /chat 默认仅返回 reply。请求设置严格布尔值 debug=true 后，额外返回 tool_results 数组，每项含 step、tool、result；网页正文为 result.content，截断标记为 result.truncated，文件正文和预期错误也保留。无工具调用时数组为空；debug=false 或省略时不返回数组。

```json
{"message":"请读取 deepseek_client.py，再读取 https://api-docs.deepseek.com/，核对代码与文档。", "debug":true}
```

结果只在本次请求内收集，不增加调用、不写正文日志。整体接口失败仍仅返回原有 detail，不附带部分结果。debug 会向调用方展示实际读取内容，不提供鉴权。已有无 --reload 服务需按原命令重启并刷新 /docs。2026-11-17：全部 77 项离线测试通过，调试展示尚未真实验收。


2026-11-17：每次模型请求的 max_tokens 从 512 提升为 2048，减少组合分析回复截断；仍不保证不会超限。空候选与 finish_reason=length 分别返回明确 502 文案。截断工具请求仍不执行。全部 78 项离线测试通过，调试真实响应仍待复测。--reload 服务保存代码后自动重载，重载完成后可提交原 debug=true 请求。


2026-11-17 调试真实验收：用户提供 HTTP 200 和完整 tool_results，确认文件正文、网页正文和 truncated=false 可见，调试展示成功。该轮模型却将未出现在文件中的参数写法称为错误，并把文档示例参数的缺省当成不兼容证据；本轮分析准确性未通过。接口和工具成功不代表回答正确，需结合原始结果人工核对。


## 工具执行记录中间件（2026-11-17）

`tool_middleware.py` 从 `main.py` 原样承接 logger 配置、`log_tool_event()` 和 `run_logged_tool()`。后者在调用 `execute_workspace_tool()` 前后记录状态和耗时，是当前第一个工具中间件；采用普通函数，不引入通用中间件框架。模型循环仍负责预算、协议与整批预检，未进入执行的拒绝批次也通过共用日志函数记录。

预期工具失败继续以结构化结果回传；意外内部异常仍终止请求并映射为固定 HTTP 500。接口、debug 结果、工具顺序、调用预算、日志字段和权限规则保持。当前中间件仍依赖 FastAPI 的 HTTPException，暂不扩展为独立于 HTTP 的运行层。

在项目目录执行 `.venv/bin/python -m unittest discover -s tests -q`：全部 78 项离线测试通过，已有 Starlette/httpx 弃用提示仍在。未重复请求真实模型或重启服务；助手终端网络权限 TODO 保留。

## 第七章第一步：独立业务运行记录

`business_store.py` 的 `LocalSqliteStore(db_path)` 使用标准库 SQLite，显式指定数据库路径；构造时创建父目录和缺失的 `runs` 表。导入模块不会创建数据库。每次操作独立打开连接，写入成功提交，异常回滚，结束后关闭连接。

| 方法 | 行为 |
|---|---|
| `create_run(run_id)` | 创建 `running` 记录，自动填写 UTC ISO 格式开始时间 |
| `finish_run(run_id, status)` | 将运行结束为 `succeeded` 或 `failed`，填写结束时间并保留开始时间 |
| `get_run(run_id)` | 返回含 `run_id/status/started_at/finished_at` 的字典，不存在返回 `None` |

空白或非字符串创建 ID、非法结束状态以及重复结束抛出 `ValueError`；结束不存在的记录抛出 `KeyError`；重复创建同一 ID 抛出 `sqlite3.IntegrityError`，不覆盖旧记录。使用参数化 SQL。这里的 `succeeded` 仅表示调用方记录的运行成功，不代表模型回答准确性通过验收。

在项目目录运行以下示例，仅使用自动清理的临时数据库：

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from business_store import LocalSqliteStore

with TemporaryDirectory() as directory:
    db_path = Path(directory) / "store.sqlite"
    store = LocalSqliteStore(db_path)
    store.create_run("demo-run")
    store.finish_run("demo-run", "succeeded")
    print(LocalSqliteStore(db_path).get_run("demo-run"))
PY
```

独立验证命令：

```bash
.venv/bin/python -m unittest discover -s tests -p test_business_store.py -v
```

2026-11-17：新增 6 项真实临时 SQLite 测试，覆盖创建、重新打开查询、成功/失败结束、重复 ID、非法输入、不存在记录及结束状态保护；全部 84 项离线测试通过，已有 Starlette/httpx 弃用提示仍在。

当前只保存业务运行状态，不保存用户消息、模型正文、工具内容或 Checkpoint。未接入 `/chat`、终端日志或配置，不会自动记录真实请求；未引入长期记忆、恢复执行、运行列表接口或跨线程共用连接。进程中断时已写入的 `running` 记录不会自动修复，后续集成再设计处理方式。


## 第六章第一步：独立系统提示词（2026-11-19）

`prompt.py` 保存稳定规则，按身份与中文表达、领域语境、当前能力和工具使用规则、资料可信度与安全边界、分析和代码审阅规则五部分组织。用户 `message` 仍单独作为本轮目标，工具结果仍通过 tool 消息提供；尚未接入 Skills 或任务分类器。

新增审阅要求：每个错误对应实际代码、依据及影响；区分已证实问题、待验证风险和可选改进；不把示例参数视为必填，不虚构代码写法，尊重忽略演示占位密钥的要求，证据不足明确说明，汇总数量与分类保持一致。提示词引导模型，权限和调用预算仍由 Python 代码保证。

离线回归：

```bash
cd /Users/travis/harnesscoding/athena
.venv/bin/python -m unittest discover -s tests -q
```

本步全部 84 项通过，已有 Starlette/httpx 弃用提示仍在。真实模型复测第一个 Harness 问题返回 HTTP 502（无法连接 DeepSeek），随后停止；新版本的默认语境、测试语境、能力边界及审阅效果均未验收。

服务加载新代码后，可运行 `.venv/bin/python scripts/check_prompt.py` 复测三种问答。该脚本仅显示回答供人工检查；能力边界还应符合当前支持用户明确提供的白名单网页读取。未启用 `--reload` 的服务需重启，本步未操作已有服务。

在原演示工作区已有 `deepseek_client.py` 的服务 `/docs` 中，再提交以下请求（演示文件仅供读取，不执行）：

```json
{"message":"请读取 deepseek_client.py，再读取 https://api-docs.deepseek.com/，核对代码与文档。api_key 是演示占位值，请忽略，不要将其列为缺陷。请给出实际代码、判断依据和影响，区分已证实问题、待验证风险与可选改进。", "debug":true}
```

人工验收：识别把文档站用作 API base_url 的预设错误；不虚构 thinking 等参数写法，不把缺少文档示例中的可选配置或占位密钥列为已证实错误，问题数量与分类须前后一致。网页获取失败或证据不足应明确说明，不能视为完整验收通过。离线测试不替代这项语义验收。
