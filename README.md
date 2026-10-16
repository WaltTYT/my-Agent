# MyAgent

项目仓库：[ztt0216/MyAgent](https://github.com/ztt0216/MyAgent)。

MyAgent 是一个基于 Python 实现的通用 **Agent Runtime**。它不是简单的命令行聊天工具，而是一个可运行、可阅读、可扩展的本地 runtime：统一编排模型推理、工具调用、权限控制、长期记忆、Skills、自进化、MCP 外部工具、子 Agent 和会话恢复。

项目重点是 **Harness**。模型只负责推理和提出工具调用意图，真正的环境操作由 Runtime 统一做权限判断、工具执行、结果回写、上下文压缩和经验沉淀。它适合学习通用 Agent 的底层机制，也适合作为个人 Agent、项目分析助手或领域 Agent 的二次开发基础。

## 核心亮点

- **通用 Agent Runtime**：统一编排模型、工具、权限、上下文、Memory、Skills 和外部扩展。
- **完整 Agent Loop**：模型请求、tool call 解析、权限检查、工具执行、tool result 回写、继续推理、会话保存形成闭环。
- **OpenAI / Anthropic 双协议**：支持 OpenAI-compatible 和 Anthropic-compatible 接口。
- **工具系统与权限控制**：支持读写文件、精确编辑、搜索、Shell、Skill 调用、子 Agent 和 MCP 工具；Plan Mode 下阻断写操作和 Shell。
- **Skills 体系**：通过项目级和用户级 `SKILL.md` 保存可复用方法，支持检索、调用、inline / fork 执行和版本化演化。
- **长期 Memory**：按项目路径 hash 隔离记忆，保存用户偏好、项目背景、历史决策和参考资料。
- **Skills 自进化**：从用户反馈中自动抽取可复用规则，新增或合并到 `SKILL.md`。
- **MCP 外部工具扩展**：自研 stdio JSON-RPC MCP Client，把外部 MCP Server 工具包装为 `mcp__server__tool`。
- **子 Agent**：支持 `explore`、`plan`、`general` 以及自定义子 Agent。
- **会话恢复和上下文压缩**：自动保存 session，支持 `--resume`、`/compact`，并对大工具结果做截断或持久化。

## 项目架构

![MyAgent 总体架构](wiki/assets/architecture/01-overall-architecture.svg)

核心运行链路：

```text
用户输入
  -> agents/main.py
  -> Agent.chat()
  -> 构建 Prompt / 检索 Skills / 预取 Memory / 初始化 MCP
  -> 调用 OpenAI-compatible 或 Anthropic-compatible 模型
  -> 模型返回文本或 tool call
  -> Runtime 做权限检查
  -> 执行工具 / Skill / MCP / 子 Agent
  -> tool result 回写模型
  -> 保存 Session
  -> 后台执行 Skill usage tracking 和 online skill evolution
```

## 目录结构

```text
MyAgent/
├── agents/
│   ├── main.py                    # CLI 入口、REPL、参数解析
│   ├── agent.py                   # Agent Runtime、模型调用、工具调度、上下文压缩
│   ├── tools.py                   # 内置工具和权限系统
│   ├── prompt.py                  # System prompt 动态构建
│   ├── skills.py                  # Skills 加载、检索、执行、创建和演化封装
│   ├── online_skill_evolution.py  # 在线 Skill 抽取和 add/merge/discard 决策
│   ├── skill_evolution.py         # Skill 落盘、版本快照、审计统计
│   ├── memory.py                  # 长期记忆系统
│   ├── mcp_client.py              # MCP stdio JSON-RPC 客户端
│   ├── subagent.py                # 子 Agent 配置
│   ├── session.py                 # 会话保存与恢复
│   └── ui.py                      # 终端 UI 输出
├── .bear/
│   ├── skills/                    # 项目级 Skills
│   └── skill-evolution/           # Skills 自进化审计产物
├── tests/                         # 权限、文件工具、Skills 检索的单元测试
├── data/                          # 评测数据说明（benchmark 需自行下载）
├── wiki/                          # 项目文档中心
├── Dockerfile
├── requirements.txt
└── README.md
```

## 快速启动

### 1. 准备环境

推荐环境：

- Python 3.11+
- macOS / Linux
- Git
- ripgrep，可选但推荐
- 一个 OpenAI-compatible 或 Anthropic-compatible 模型接口

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置 `.env`

项目会自动读取当前目录或父目录中的 `.env`。

Anthropic-compatible 示例：

```env
APIKEY=sk-your-api-key
API=https://your-host/anthropic
MODEL=claude-sonnet-4-6
```

OpenAI-compatible 示例：

```env
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://your-host/v1
MODEL=gpt-4o
```

也可以使用通用变量：

```env
APIKEY=sk-your-api-key
API=https://your-host/v1
MODEL=deepseek-chat
```

协议判断规则：

- `API` 或 `--api-base` 路径包含 `/anthropic` 时，按 Anthropic-compatible 调用。
- 否则有 OpenAI base URL 时，按 OpenAI-compatible 调用。
- `--model` 会覆盖 `.env` 中的 `MODEL`。

### 3. 启动 REPL

```bash
python3 -m agents.main
```

启动后直接输入任务，例如：

```text
阅读这个项目，告诉我 agent loop 是怎么跑起来的
```

### 4. 执行一次性任务

```bash
python3 -m agents.main "总结这个项目的目录结构和核心模块"
```

### 5. 使用 Plan Mode

Plan Mode 适合重构、复杂修复和多文件修改。它会先只读分析和写计划，用户审批后再执行。

```bash
python3 -m agents.main --plan "分析 Skills 检索逻辑应该如何优化"
```

REPL 中也可以输入：

```text
/plan
```

### 6. 恢复最近会话

```bash
python3 -m agents.main --resume
```

## Skills 自进化

MyAgent 的核心特色是 **Skills 自进化**。它可以从用户明确反馈中抽取未来可复用的规则，并自动新增或合并到项目级或用户级 `SKILL.md`。

### 1. 开启自动自进化

默认 `BEAR_AUTO_SKILL_EVOLUTION` 是开启的。为了明确配置，建议在 `.env` 中写：

```env
BEAR_AUTO_SKILL_EVOLUTION=1
BEAR_AUTO_SKILL_TARGET=project
```

含义：

- `BEAR_AUTO_SKILL_EVOLUTION=1`：启用在线 Skill 自进化。
- `BEAR_AUTO_SKILL_TARGET=project`：自动新增的 Skill 写入当前项目 `.bear/skills/`。

如果希望沉淀为所有项目共享的个人 Skill：

```env
BEAR_AUTO_SKILL_TARGET=user
```

对应路径：

```text
project: <project>/.bear/skills/<skill_name>/SKILL.md
user:    ~/.bear/skills/<skill_name>/SKILL.md
```

### 2. 用允许写入的权限模式启动

后台自动写入 Skill 需要当前权限模式允许写文件。推荐使用：

```bash
BEAR_AUTO_SKILL_EVOLUTION=1 \
BEAR_AUTO_SKILL_TARGET=project \
python3 -m agents.main --accept-edits
```

也可以使用更激进的模式：

```bash
python3 -m agents.main --yolo
```

不建议长期默认使用 `--yolo`，因为它会跳过确认。日常推荐 `--accept-edits`。

### 3. 给出可复用反馈

自进化不是把每一句话都写成 Skill。它只适合沉淀稳定、明确、未来同类任务仍适用的规则。

不太适合沉淀：

```text
帮我写一篇 500 字政府报告。
```

适合沉淀：

```text
以后写政府报告、工作汇报、调研材料时，默认先给可直接使用的初稿，不要连续追问；结构按“标题、背景、主要情况、问题分析、工作举措、下一步计划”组织，语言要正式克制。
```

适合演化已有 Skill：

```text
以后这类政府报告还要加入关键数据指标，不能只写概念性表述。
```

### 4. 自进化链路如何工作

```text
第 N 轮用户任务
  -> Agent 输出结果
  -> 保存 pending extraction window
第 N+1 轮用户反馈
  -> 合并进上一轮 window
  -> online_ingest()
  -> Extractor 抽取候选 Skill
  -> Maintainer 判断 add / merge / discard
  -> create_skill_file() 或 evolve_skill_file()
  -> 写入 SKILL.md
  -> 记录 provenance、usage stats 和版本快照
```

核心文件：

```text
agents/agent.py
agents/online_skill_evolution.py
agents/skills.py
agents/skill_evolution.py
```

审计产物：

```text
.bear/skill-evolution/usage.jsonl
.bear/skill-evolution/online_provenance.jsonl
.bear/skill-evolution/online_skill_provenance.json
.bear/skill-evolution/skill_usage_stats.json
.bear/skill-evolution/history/
```

## 更多文档

- [从0到1学习了解项目](wiki/从0到1学习了解项目.md)
- [架构设计](wiki/架构设计.md)
- [核心源码阅读指南](wiki/核心源码阅读指南.md)
- [技术亮点](wiki/技术亮点.md)
- [Skills自进化逻辑与实现思路](wiki/Skills自进化逻辑与实现思路.md)
- [评测部分](wiki/评测部分.md)

## 开发与测试

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

测试不调用模型，覆盖权限模式（plan / acceptEdits / dontAsk）、危险命令检测、权限规则优先级、编辑前必须先读和 mtime 校验、Skill 加载优先级与 BM25 检索。CI 在 Python 3.11 和 3.12 上运行。

欢迎提 Issue 和 Pull Request。提 PR 前请确保 `python -m pytest -q` 通过。

## License

[MIT](LICENSE)
