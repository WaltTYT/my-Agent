"""运行时数据目录的统一入口。

Lily 生成的数据（会话记录、压缩摘要、长期记忆、计划文件、超大工具输出、技能进化
统计）全部放在同一个数据根目录下，默认是项目内的 `memory/`。改动前这些内容散在
`~/.lily/`、`~/.lily-data/`、`~/.lily-memory/` 和项目 `.lily/` 四处，这里统一收拢。

根目录可以用环境变量 `LILY_DATA_DIR` 覆盖。注意该目录会写入完整对话原文，默认已在
`.gitignore` 里忽略；显式指向其他路径时，需要自行确认那个位置不会被提交。

配置和技能不算生成数据，仍然留在 `~/.lily/` 与项目 `.lily/` 下：技能文件是要入库
的源码，设置文件是用户手写的。
"""

from __future__ import annotations

import os
from pathlib import Path

# 覆盖数据根目录用的环境变量名。
DATA_DIR_ENV = "LILY_DATA_DIR"

# 未设置 LILY_DATA_DIR 时，数据放在当前工作目录下的这个文件夹里。
DEFAULT_DIR_NAME = "memory"


def data_root() -> Path:
    """返回数据根目录，不存在时创建。"""
    configured = os.environ.get(DATA_DIR_ENV, "").strip()
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path.cwd() / DEFAULT_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sub(dir_name: str) -> Path:
    """返回根目录下的子目录，不存在时创建。"""
    d = data_root() / dir_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def sessions_dir() -> Path:
    """完整会话记录，每个会话一个 `{session_id}.json`。"""
    return _sub("sessions")


def folded_sessions_dir() -> Path:
    """上下文压缩产生的会话摘要，文件名与所属会话同 id。"""
    return _sub(os.path.join("sessions", "folded"))


def long_term_dir() -> Path:
    """长期记忆条目和 MEMORY.md 索引。"""
    return _sub(os.path.join("long-term", "memory"))


def plans_dir() -> Path:
    """`/plan` 生成的计划 Markdown。"""
    return _sub("plans")


def tool_results_dir() -> Path:
    """超过阈值、被从上下文里挪出去的大段工具输出。"""
    return _sub("tool-results")


def skill_evolution_dir() -> Path:
    """技能使用统计与进化历史。"""
    return _sub("skill-evolution")
