"""文本读写必须显式声明编码。

Windows 上 `Path.read_text()` / `write_text()` 不传 encoding 时，会跟随系统 ANSI
代码页（中文环境是 cp936/GBK），而项目里的 SKILL.md、记忆文件、会话 JSON 都是按
UTF-8 存的。默认编码一不一致，轻则中文乱码，重则直接 UnicodeDecodeError。

Linux 和 macOS 默认就是 UTF-8，所以这类问题在 CI 上不会暴露，只在 Windows 本机出现。
这个测试用 AST 扫描全部源码，把"漏写 encoding"变成一条会失败的测试，避免回归。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("agents", "tests")

# 需要显式 encoding 的文本读写入口。
TEXT_IO_CALLS = {"read_text", "write_text", "open"}

# open() 以二进制模式调用时本来就不需要 encoding。
BINARY_MODES = {"rb", "wb", "ab", "xb", "rb+", "r+b", "br", "w+b", "a+b"}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _has_encoding_kwarg(node: ast.Call) -> bool:
    return any(keyword.arg == "encoding" for keyword in node.keywords)


def _binary_mode(node: ast.Call) -> bool:
    """open(path, "rb") 的第二个位置参数是字符串常量时，判断是否为二进制模式。"""
    if _call_name(node) != "open":
        return False
    mode_arg = None
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode_arg = keyword.value.value
    if mode_arg is None and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        mode_arg = node.args[1].value
    if not isinstance(mode_arg, str):
        return False
    return "b" in mode_arg


def _find_missing_encoding(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) not in TEXT_IO_CALLS:
            continue
        if _has_encoding_kwarg(node) or _binary_mode(node):
            continue
        offenders.append(node.lineno)
    return sorted(offenders)


def test_all_text_io_declares_encoding():
    """agents/ 和 tests/ 下不允许出现未声明 encoding 的文本读写。"""
    violations = []
    for folder in SOURCE_DIRS:
        for source in sorted((ROOT / folder).glob("*.py")):
            for lineno in _find_missing_encoding(source):
                violations.append(f"{source.relative_to(ROOT)}:{lineno}")
    assert not violations, (
        "以下文本读写缺少 encoding，Windows 上会退回系统 ANSI 代码页："
        + ", ".join(violations)
    )
