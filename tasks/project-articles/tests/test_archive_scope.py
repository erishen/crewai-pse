"""archive.py 的合法项目口径：projects.json ∪ 磁盘上有正文的项目。

归档执行器曾只认 projects.json ∪ projects-published.json，`legacy` 这类
正文还在磁盘、却不在任何 json 里的项目会被一句「用法」错误直接拒掉；而
sidecar 运行时守卫同时又会放进 schema 公布的名字 —— 三处口径不一致导致
归档串台。这里固定「有正文即合法」这一条，并守住 key 的推导规则。
"""
import sys
from pathlib import Path

import pytest

# archive.py 在 tasks/project-articles/ 下，tests/ 的同级目录
sys.path.insert(0, str(Path(__file__).parent.parent))
import archive  # noqa: E402


def _make_tree(tmp_path, names):
    for rel in names:
        p = tmp_path / "articles" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("body\n", encoding="utf-8")
    return tmp_path


def test_on_disk_keys_from_both_langs(monkeypatch, tmp_path):
    _make_tree(
        tmp_path,
        [
            "zh/resolve_lume-zh.md",
            "en/resolve_lume-en.md",
            "zh/legacy-zh.md",
            "en/en_only-en.md",
            "juejin/should_ignore-zh.md",  # 非 zh/en 目录，忽略
            "zh/no_suffix.md",  # 无 -zh.md 后缀，忽略
        ],
    )
    monkeypatch.setattr(archive, "WP_TOOLS_DIR", tmp_path)
    keys = archive._on_disk_keys()
    assert {"resolve-lume", "legacy", "en-only"} <= keys
    assert "should-ignore" not in keys
    assert "no-suffix" not in keys


def test_on_disk_keys_empty_without_wp_dir(monkeypatch):
    monkeypatch.setattr(archive, "WP_TOOLS_DIR", Path(""))
    assert archive._on_disk_keys() == set()


def test_load_projects_includes_body_only_project(monkeypatch, tmp_path):
    _make_tree(tmp_path, ["zh/legacy-zh.md"])
    monkeypatch.setattr(archive, "WP_TOOLS_DIR", tmp_path)
    projects = archive._load_projects()
    assert "legacy" in projects
    # json 里已有项目的元数据不能被空字典覆盖
    assert "repo" in projects["resolve-lume"]
