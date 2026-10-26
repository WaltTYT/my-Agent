# Agent.md — Lily 项目 Agent 工作规范

本文件约束和指导下列 Agent（Trae / Lily 自身 / 其他协作 AI）在本项目中的行为。执行任何任务前先阅读本文。

## 1. 项目概述

- **项目名称**：Lily（原 MyAgent / Bear Code 的二次开发分支，界面与提示词已统一为 Lily）
- **定位**：可进化的智能体 CLI（终端编码代理），基于 Anthropic / OpenAI 兼容接口
- **当前模型**：智谱 GLM-4（OpenAI 兼容端点，配置见 `.env`）

### 技术栈

| 技术 | 版本 | 用途 |
|---|---|---|
| Python | 3.11.15（conda 环境 `my-agent`） | 运行时 |
| anthropic | 2.3.0 | Anthropic 协议模型调用 |
| openai | 3.37.0 | OpenAI 兼容协议模型调用（GLM 走此通道） |
| rich | 15.0.0 | 终端 UI 渲染 |
| tqdm | 4.70.1 | 进度显示 |
| python-dotenv | 1.2.4 | `.env` 配置加载 |
| pytest | [待确认版本] | 测试框架 |

### 架构模式

单体 CLI 应用：`main.py`（REPL 入口）→ `agent.py`（对话主循环 + 工具调度）→ `tools.py`（内置工具层），外围为记忆（memory）、技能（skills）、子代理（subagent）、MCP（mcp_client）、会话持久化（session）模块。无数据库，全部状态存文件系统。

## 2. Agent 规则（核心约束）

### 2.1 基本原则

- 每一次执行任务时，都必须先检查用户配置和项目记忆中的偏好设置
- 按要求执行，不多不少
- 除非绝对必要，否则绝不创建文件
- 优先编辑现有文件，而非创建新文件
- 除非明确要求，否则绝不主动创建文档文件（*.md 或 README）
- 提交或推送代码变更前需要明确获得用户许可
- 没有用户明确允许，绝不推送项目到 GitHub
- 没有用户明确允许，绝不删除项目中的任何文件

### 2.2 任务开始前

- 检查可用技能：`<available_skills>` 中是否有相关技能
- 查看记忆上下文：用户偏好与项目记忆
- 先规划后执行：复杂多步骤任务使用 `TodoWrite`
- 提出澄清问题：需求不明确时用 `AskUserQuestion`，不要猜

### 2.3 任务执行规则

- **探索**：先读代码再发言；定向查找用 Glob/Grep，跨模块理解用 Explore 代理
- **规划**：多文件改动或方案不唯一时，先给出计划征得同意
- **实现**：按现有模块边界改动，新功能优先挂到既有扩展点（新工具 → `tools.py`；新命令 → `main.py` REPL 分发；新规则 → `.lily/rules/`）
- **验证**：改动后必须运行 `python -m pytest tests -q`（当前基线 91 passed）；涉及 UI 的改动重启 REPL 目测确认

### 2.4 代码风格规则

- **除非用户明确要求，否则不添加任何注释**（现有中文注释是历史修复遗留，勿扩散）
- 遵循现有 4 空格缩进、snake_case 函数、PascalCase 类的约定
- 面向用户的新文案一律简体中文（项目已全中文化）；发给模型的工具描述保持英文
- 保持代码整洁和可维护，改动最小化

### 2.5 安全指南

- API 密钥只存 `.env`，绝不写入代码、日志、提交历史；`.env` 必须在 `.gitignore` 中
- `tools.py` 的命令执行有危险模式拦截（rm -rf、mkfs 等），新增执行类工具必须保留/添加确认门槛
- 文件写入工具已固定 `encoding="utf-8"`，禁止再引入无编码的文本读写
- 绝对路径写入不再被重映射到项目内（已修复），涉及用户目录操作需经权限确认

## 3. 项目结构

```
my-Agent/                      # 项目根（Lily）
├── agents/                    # 核心源码（唯一 Python 包，16 个模块）
│   ├── main.py                # CLI 入口与 REPL 命令分发
│   ├── agent.py               # 对话循环、工具调度、计划模式
│   ├── tools.py               # 内置工具、权限确认、路径解析
│   ├── prompt.py              # 系统提示词组装（含中文回复指令）
│   ├── ui.py                  # rich 终端渲染（品牌名 Lily 在此）
│   ├── memory.py              # 长期记忆
│   ├── session.py / session_memory.py  # 会话持久化
│   ├── paths.py               # 运行时数据目录统一入口
│   ├── skills.py / skill_evolution.py / online_skill_*.py  # 技能系统与进化
│   ├── subagent.py            # 子代理
│   ├── mcp_client.py          # MCP 接入
│   └── frontmatter.py         # YAML frontmatter 解析
├── tests/                     # pytest 套件（conftest 含 Windows HOME 重定向）
├── .lily/                     # 项目级配置与技能：skills / rules / settings.json
├── memory/                    # 运行时数据根目录（不入库）：sessions / long-term / plans / tool-results / skill-evolution
├── .env                       # 模型密钥与端点（不入库）
├── start.bat                  # 一键启动脚本
└── LICENSE / README.md
```

## 4. 关键配置文件

| 文件 | 用途 |
|---|---|
| `.env` | 模型密钥、BASE_URL、MODEL；缺失则无法启动对话 |
| `start.bat` | 激活 conda `my-agent` 并启动 `python -m agents.main`；必须 CRLF 换行、避免 UTF-8 中文注释（cmd 解析会截断） |
| `.lily/settings.json` | 项目级 MCP 服务配置 |
| `.github/workflows/ci.yml` | GitHub Actions 流水线 |
| `agents/prompt.py` | 系统提示词模板（品牌名、语言指令在此） |

## 5. 安全基线

- 认证：无用户体系；信任本地终端使用者
- 密钥策略：仅环境变量注入（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`），代码不落盘不回显
- 输入验证：工具参数经 `tools.py` schema 校验；危险命令模式拦截
- 权限模型：写文件 / 执行命令 / 计划批准均需 REPL 内 y/n 确认

## 6. 测试指南

```powershell
conda activate my-agent
python -m pytest tests -q        # 基线：91 passed
```

- 测试不联网（模型调用被 fake 替身替换，见 tests/）
- Windows 注意：tests/conftest.py 已同时重定向 `HOME` 与 `USERPROFILE`，新增涉及用户目录的测试沿用该 fixture
- 无前端；UI 验证以人工运行 REPL 为准

## 7. 部署 / 启动

- 依赖服务：无（纯本地 CLI）
- 启动：`.\start.bat`（或 `conda activate my-agent && python -m agents.main`）
- 可选参数：`--resume` 恢复会话；`"一次性提问"` 非交互模式
- 环境要求：Python 3.11+、上表依赖装于 conda 环境 `my-agent`（勿装用户目录）

## 8. 版本控制

- **远程仓库**：`origin https://github.com/WaltTYT/my-Agent`（用户自有仓库，已从原项目 ztt0216/MyAgent 切换）
- **当前分支**：`main`
- **推送策略**：必须获得用户明确许可才 push；本地 commit 也先征求同意

### 8.1 提交格式规范（最重要）

| 类型 | 格式 | 说明 | 示例 |
|------|------|------|------|
| 初始化项目 | `init(模块 : 初始化描述)` | 初始化结构、配置文件 | `init(all : 初始化默认模板文件)` |
| 增加功能 | `add(模块 : 功能描述)` | 新功能、新依赖、新文件 | `add(tools : 添加联网搜索工具)` |
| 修复bug | `fix(模块 : 修复描述)` | 修复问题 | `fix(tools : 修复Windows写文件GBK乱码)` |
| 删除内容 | `delete(模块 : 删除描述)` | 删除文件、移除死代码 | `delete(docs : 移除旧wiki链接)` |
| 重构 | `refactor(模块 : 重构描述)` | 不改功能的优化 | `refactor(agent : 拆分对话循环)` |
| 文档 | `docs(模块 : 文档描述)` | 更新文档 | `docs(README : 更新部署说明)` |

### 8.2 提交格式规则

- **模块标识**：用具体模块名（`tools`、`agent`、`ui`、`tests`、`README`）或 `all`
- **分隔符**：`:` 两侧各一个空格
- **多提交**：`&&` 连接，前后各一个空格，如 `add(ui : 品牌改名Lily) && docs(README : 重写项目说明)`
- 一次提交只做一类事；bug 修复与功能新增分开

## 9. 已知约束

- conda 环境目录 `D:\study\miniconda\envs\my-agent` 需管理员权限才能写入（已通过 icacls 授权，pip 正常）
- `.bat` 脚本必须 CRLF + 纯 ASCII 注释，否则 cmd 解析截断
- 运行时数据统一落在项目内 `memory/`（默认根目录），子目录为 `sessions/`、`sessions/folded/`、`long-term/`、`plans/`、`tool-results/`、`skill-evolution/`；路径全部由 `agents/paths.py` 提供，不要在模块里再拼 `Path.home()`
- 数据根目录可用环境变量 `LILY_DATA_DIR` 覆盖；指向跨项目共享目录时，不同项目的会话与记忆会混在一起，需要自行分开
- `read_file` 支持 `offset`/`limit` 分页，默认 400 行、上限 2000 行。整份读大文件会撞上 `_persist_large_result` 的 30 KB 阈值，旧文案又邀请模型回读落盘文件，形成自我维持的循环（实测一次对话烧掉 64 万 input token、$2.02）。落盘提示语现已禁止整份回读，且读回 tool-results 文件时不再二次落盘
- 计划模式下发给模型的工具由 `get_plan_mode_tool_definitions()` 过滤，只留只读工具与计划文件写入；`enter_plan_mode` / `exit_plan_mode` 虽是 deferred 但在此模式下强制发送，否则模型无法退出计划模式
- `anthropic` / `openai` 在 `agent.py` 中为就地导入（合计约 4 秒），新增顶层导入前先确认是否真的需要
- REPL 提供 `/remember` 与 `/forget`：前者让用户不依赖模型自觉即可强制落盘记忆（不调模型，解析完全确定），后者按文件名或关键词匹配，歧义时只列候选不删除
- 长期记忆通过 `save_memory` / `delete_memory` 工具写入 `memory/long-term/`，属于 `EDIT_TOOLS`（计划模式下被拒）；文件名由 `_memory_filename()` 推导，保留 Unicode 字符并在校验失败时补哈希，避免不同中文名称塌缩成同一个文件互相覆盖
- `MEMORY.md` 是生成索引：写入、删除和手动增删记忆文件后由 `refresh_memory_index_if_stale()` 在组装 system prompt 时自愈，禁止手工编辑
- 配置与技能不属于生成数据，仍在 `~/.lily/`（全局）和项目 `.lily/`（`skills/` 需入库）
- 2026-10 历史迁移：数据曾散在 `~/.lily-data/`、`~/.lily-memory/`、`~/.lily/plans/` 和项目 `.lily/sessions/`，旧位置已停用，本机 4 个会话文件已搬入 `memory/sessions/`
- 原 `data/`、`wiki/` 目录已被用户移走，代码无依赖；`.env.example` 已删除，可从 git 历史恢复
- `requirements.txt` 是 CI 与 Dockerfile 的硬依赖，必须留在仓库根目录，不能挪进 `.doc/` 等未入库位置
- GLM-4 上下文有限，长会话依赖 `/compact` 与自动压缩

## 10. Agent 自检清单

完成任务前逐项验证：

- [ ] 代码遵循项目约定（4 空格、snake_case、无新增注释）
- [ ] 未暴露任何机密或凭证（密钥只在 .env）
- [ ] 所有用户可见文案为简体中文，模型侧描述为英文
- [ ] `python -m pytest tests -q` 全部通过（基线 91）
- [ ] 涉及文件读写的改动显式 encoding="utf-8"
- [ ] 变更最小化且聚焦，未顺手重构无关代码
- [ ] 提交遵循 8.1 格式，且已获得用户提交/推送许可
