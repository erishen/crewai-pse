"""frontmatter 标题泄漏门禁 + 思维链清洗的单测。

这些是上一轮修复 + 本轮回拆分后最该锁死的逻辑：
- ReAct 工具调用串（Action: read_file / read_file(...) 等）不得成为标题
- 思维链标记（I need to / Observation: 等）不得成为标题
- 第一人称 + 意图动词（I will analyze）不得成为标题
- 正常标题（含 My RAG Stack / How I Built ...）不得被误伤
"""
import re

from pipeline import sanitize

BODY = "\n## 源头方法\n\n正文讨论 read_file 工具的实现。\n"


def _art(title_line: str) -> str:
    return f"---\n{title_line}\ndate: 2026-09-01\n---\n{BODY}"


def _title_of(out: str) -> str:
    m = re.search(r"^title:(.*)$", out, re.M)
    return m.group(1).strip()


def test_react_tool_string_blocked():
    assert _title_of(sanitize.sanitize_frontmatter(_art("title: Action: read_file"), "FB")) == "FB"


def test_action_input_blocked():
    assert _title_of(sanitize.sanitize_frontmatter(_art('title: Action Input: {"path": "a.py"}'), "FB")) == "FB"


def test_bare_tool_call_blocked():
    assert _title_of(sanitize.sanitize_frontmatter(_art('title: read_file({"path": "src/a.py"})'), "FB")) == "FB"


def test_observation_blocked():
    assert _title_of(sanitize.sanitize_frontmatter(_art("title: Observation: 42 files found"), "FB")) == "FB"


def test_reasoning_marker_blocked():
    assert _title_of(
        sanitize.sanitize_frontmatter(_art("title: I need to read the source code first to ground my outline in"), "FB")
    ) == "FB"


def test_first_person_intent_blocked():
    assert _title_of(sanitize.sanitize_frontmatter(_art("title: I will analyze the repo"), "FB")) == "FB"


def test_legit_zh_preserved():
    t = "firefly-studio 架构复盘：一个桌面数字人应用为何从第一天起就是 Electron + C++"
    assert _title_of(sanitize.sanitize_frontmatter(_art(f"title: {t}"), "FB")) == t


def test_legit_my_rag_stack_preserved():
    # 回归：旧规则会误伤 'My RAG Stack for 2026'
    t = "My RAG Stack for 2026"
    assert _title_of(sanitize.sanitize_frontmatter(_art(f"title: {t}"), "FB")) == t


def test_legit_how_i_built_preserved():
    t = "How I Built My Own RAG Pipeline"
    assert _title_of(sanitize.sanitize_frontmatter(_art(f"title: {t}"), "FB")) == t


def test_title_fallback_to_heading():
    art = "---\ntitle: Action: read_file\ndate: 2026-09-01\n---\n\n## 这是二级标题\n\n正文\n"
    assert _title_of(sanitize.sanitize_frontmatter(art, "")) == "这是二级标题"


def test_title_fallback_empty():
    art = "---\ntitle: Action: read_file\ndate: 2026-09-01\n---\n\n正文无标题\n"
    assert _title_of(sanitize.sanitize_frontmatter(art, "")) == ""


def test_description_leak_deleted():
    art = "---\ntitle: 正常标题\ndescription: Action: read_file\ndate: 2026-09-01\n---\n\n正文\n"
    out = sanitize.sanitize_frontmatter(art, "")
    assert "description:" not in out


def test_merge_drafts():
    merged = sanitize.merge_drafts(["# A\n\nx", "# B\n\ny"])
    assert "x" in merged and "y" in merged


def test_has_reasoning_leak():
    assert sanitize.has_reasoning_leak("Thought: let me think")
    assert not sanitize.has_reasoning_leak("普通正文没有思维链")


def test_strip_reasoning_leaks():
    out = sanitize.strip_reasoning_leaks("前言\nThought: 我应该先读源码\n正文继续")
    assert "Thought" not in out
