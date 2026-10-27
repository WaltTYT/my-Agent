# Lily

一个可进化的智能体 CLI。基于 MIT 许可的原型项目二次开发，支持 Anthropic / OpenAI 兼容接口（当前配置为智谱 GLM-4）。

## 特性

- **对话式编码代理**：读写文件、执行命令、搜索代码，工具调用全程可视化
- **只读规划模式**：`/plan` 先出方案再动手，方案需人工批准后才执行
- **技能系统**：`/skills` 复用工作流，`/skill-create` 沉淀新技能，支持自动技能进化
- **长期记忆**：按项目隔离的记忆库，自动召回相关条目注入对话
- **子代理**：内置 Explore / Plan / general-purpose 代理，支持 `.lily/agents/*.md` 自定义
- **MCP 扩展**：通过 `.lily/settings.json` 挂载外部 MCP 服务
- **会话管理**：自动保存、`--resume` 恢复、`/compact` 压缩上下文
- **费用统计**：实时显示 token 消耗与成本，支持预算上限

## 快速开始

### 1. 环境

```powershell
conda create -n my-agent python=3.11
conda activate my-agent
pip install anthropic openai python-dotenv rich tqdm
```

### 2. 配置 API

在项目根目录创建 `.env`：

```ini
# 智谱 GLM（OpenAI 兼容接口）
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
MODEL=glm-4-flash
# 会话中可用 /model 查看和切换；候选列表由 MODELS 指定（逗号分隔）
MODELS=glm-4.7-flash,glm-4.6v-flash,glm-4-flash

# 也支持 Anthropic 兼容端点（如 DeepSeek）：
# ANTHROPIC_API_KEY=...
# ANTHROPIC_BASE_URL=...
# MODEL=...
```

`OPENAI_BASE_URL` 存在且不以 `/anthropic` 结尾时自动走 OpenAI 协议。

### 3. 启动

```powershell
.\start.bat                    # 双击运行也可以
.\start.bat --resume           # 恢复上次会话
.\start.bat "帮我写个周报"      # 一次性提问
```

或手动：`conda activate my-agent && python -m agents.main`

## REPL 命令

| 命令 | 说明 |
|---|---|
| `/plan` | 进入只读规划工作流 |
| `/plan-exit` | 退出计划模式 |
| `/plans` | 查看所有任务计划；`/plans <文件名>` 查看具体计划 |
| `/plans-create <任务>` | 立即创建按任务命名的计划文件 |
| `/skills` | 列出可复用技能 |
| `/skill-create <名称>` | 创建技能 |
| `/skill-stats` | 查看技能进化统计 |
| `/memory` | 列出长期记忆 |
| `/model` | 查看当前模型和候选列表；`/model <名称>` 切换模型 |
| `/compact` | 压缩当前上下文 |
| `exit` | 退出会话 |

## 项目结构

```
agents/
├── main.py                    # CLI 入口与 REPL 循环
├── agent.py                   # 对话主循环、工具调度、计划模式
├── prompt.py                  # 系统提示词组装
├── tools.py                   # 内置工具（文件/命令/搜索）与权限确认
├── ui.py                      # rich 终端渲染（欢迎面板、工具卡片、提示符）
├── session.py / session_memory.py  # 会话持久化与逐条记录
├── memory.py                  # 长期记忆存取与召回
├── skills.py                  # 技能发现与加载
├── skill_evolution.py         # 离线技能进化
├── online_skill_evolution.py / online_skill_eval.py  # 在线技能进化与评测
├── subagent.py                # 子代理定义与调度
├── mcp_client.py              # MCP 服务连接
└── frontmatter.py             # YAML frontmatter 解析
tests/                         # pytest 测试套件
```

## 数据存储位置

| 数据 | 路径 |
|---|---|
| 项目会话 | `.lily/sessions/`（项目内） |
| 全局会话 | `~/.lily-data/sessions/` |
| 长期记忆 | `~/.lily-memory/projects/<项目hash>/memory/` |
| 技能 / 规则 / MCP 配置 | `.lily/skills`、`.lily/rules`、`.lily/settings.json`（支持用户级 `~/.lily/` 同名目录） |
| 工具结果缓存 | `~/.lily-data/tool-results/` |
| 计划文件 | `memory/plans/` |

## 测试

```powershell
conda activate my-agent
python -m pytest tests -q
```

## 许可

MIT。包含原项目（Bear Code / MyAgent）的版权授予，详见 [LICENSE](LICENSE)。
