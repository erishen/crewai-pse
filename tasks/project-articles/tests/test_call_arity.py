"""静态守卫：同文件内本地函数调用的「位置参数个数」不得超过定义上限。

为什么需要它：quarantine() 曾出现「定义收 2 个参数、5 个调用点各传 3 个」
的漂移，平时完全测不到 —— 因为那些调用全在失败分支（文章不合格被隔离）里，
只有真正触发隔离时才炸 TypeError，而那时生成已经跑了几分钟，白跑一轮。
类型检查器（mypy 等）没接入本仓，这里用 ast 补上这道最低限度的闸。

只判断「位置参数过多」这一种必然 TypeError 的情形，不判断参数过少
（默认值、关键字实参、跨模块 import 都会让判断失真，容易误报）。
"""
import ast
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent  # tasks/project-articles


def _collect_defs(tree):
    """模块级（含嵌套）函数定义 → 签名信息；同名多个定义不判断。"""
    defs = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = node.args
        pos = list(getattr(a, "posonlyargs", [])) + list(a.args)
        defs.setdefault(node.name, []).append(
            {
                "max_pos": len(pos),
                "has_vararg": a.vararg is not None,
                "has_kwarg": a.kwarg is not None,
                "lineno": node.lineno,
            }
        )
    return defs


def _find_too_many_args(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    defs = _collect_defs(tree)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        sigs = defs.get(node.func.id)
        if not sigs or len(sigs) > 1:
            continue
        sig = sigs[0]
        if sig["has_vararg"] or sig["has_kwarg"]:
            continue
        n_pos = len(node.args)
        if n_pos > sig["max_pos"]:
            hits.append(
                f"{path.name}:{node.lineno}  {node.func.id}() 传了 {n_pos} 个位置参数，"
                f"定义（第 {sig['lineno']} 行）最多接受 {sig['max_pos']} 个"
            )
    return hits


def _targets():
    files = [BASE / "run.py"]
    files += sorted((BASE / "pipeline").glob("*.py"))
    files += sorted(p for p in BASE.glob("*.py") if p.name != "conftest.py")
    return [f for f in files if f.exists()]


@pytest.mark.parametrize("path", _targets(), ids=lambda p: p.name)
def test_no_positional_arg_overflow(path):
    hits = _find_too_many_args(path)
    assert not hits, "调用参数个数与定义不匹配：\n  " + "\n  ".join(hits)
