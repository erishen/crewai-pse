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


# ── 回归：K 风格「证据」章节的空代码块 ──
# 事故：strip_fictional_refs 用 cleaned.strip().strip("`").strip() 判空，
# ```js 空围栏剥完反引号后剩 "js" 被当成有效内容，空块泄漏到已发布文章，
# 正文里「这三个符号…」等指代句同步残留。仅无语言标签的围栏才会被丢弃。
def test_find_empty_code_blocks_detects_lang_tag_only():
    assert len(sanitize.find_empty_code_blocks("```js\n```\n")) == 1
    assert len(sanitize.find_empty_code_blocks("```python\n\n```\n")) == 1
    assert sanitize.find_empty_code_blocks("```js\nconst x = 1;\n```\n") == []


def test_strip_fictional_refs_drops_empty_fenced_block_with_lang_tag():
    art = (
        "前置论述\n\n"
        "```js\n"
        "const jobId = makeGhostJob(taskName);\n"
        "```\n\n"
        "后置论述\n"
    )
    out = sanitize.strip_fictional_refs(art, ["makeGhostJob", "jobId"])
    assert "```" not in out, out
    assert "前置论述" in out and "后置论述" in out
    assert sanitize.count_code_blocks(out) == 0


def test_strip_fictional_refs_keeps_partial_block():
    art = "```js\nconst a = keepMe();\nconst b = makeGhostJob();\n```\n"
    out = sanitize.strip_fictional_refs(art, ["makeGhostJob"])
    assert "keepMe" in out
    assert "makeGhostJob" not in out
    assert sanitize.count_code_blocks(out) == 1


def test_has_reasoning_leak():
    assert sanitize.has_reasoning_leak("Thought: let me think")
    assert not sanitize.has_reasoning_leak("普通正文没有思维链")


def test_strip_reasoning_leaks():
    out = sanitize.strip_reasoning_leaks("前言\nThought: 我应该先读源码\n正文继续")
    assert "Thought" not in out


def test_dsml_title_blocked():
    """原生 token 形态的工具调用（DeepSeek 的 `<｜｜ calls>`）不得成为标题。"""
    assert _title_of(sanitize.sanitize_frontmatter(_art("title: <｜｜ calls>"), "FB")) == "FB"


def test_dsml_invoke_title_blocked():
    assert (
        _title_of(
            sanitize.sanitize_frontmatter(
                _art('title: <｜｜ invoke name="read_file">'), "FB"
            )
        )
        == "FB"
    )


def test_has_tool_call_leak():
    """泄漏检测器：只认原生 token 形态，不误伤正文里对 read_file 的正常讨论。"""
    assert sanitize.has_tool_call_leak('<｜｜ invoke name="read_file">')
    assert sanitize.has_tool_call_leak("<｜｜ calls>")
    assert not sanitize.has_tool_call_leak("正文讨论 read_file 工具的实现细节")
    assert not sanitize.has_tool_call_leak("普通正文 <em>html</em> 也不是泄漏")


def test_strip_empty_code_blocks_removes_fence_only_blocks():
    """只剩 ```lang 的空围栏是假证据：必须在落盘前删掉，而不是留到核查轮再删。"""
    art = (
        "## 落地\n\n先看启动入口：\n\n```bash\n```\n\n"
        "真实片段如下：\n\n```js\nconst port = 3000;\n```\n"
    )
    out, removed = sanitize.strip_empty_code_blocks(art)
    assert removed == 1
    assert "```bash" not in out
    assert "const port = 3000;" in out
    assert "\n\n\n" not in out


def test_style_instruction_not_used_as_title():
    """Writer 会把「按要求强制」的风格说明抄进标题，必须回落成正常标题。"""
    art = (
        "---\n"
        "title: **H. 数据/评测驱动型**（按要求强制）。\n"
        "description: 从源码出发复盘 nsbp-lume 的 SSR 中继决策。\n"
        "---\n\n"
        "## 待证假设\n\n正文。\n"
    )
    out = sanitize.sanitize_frontmatter(art, desc_fallback="nsbp-lume 的演进")
    title = re.search(r"^title:\s*(.*)$", out, re.M).group(1)
    assert "按要求强制" not in title
    assert "评测驱动型" not in title


def test_title_fallback_is_a_title_not_a_sentence():
    """兜底标题必须被压成短语：整句项目描述（76 字、带仓库路径）不能直接当标题。"""
    desc = ("React 服务端渲染(SSR)+ Lume 引擎,对齐姊妹项目 lume-crm 的标准范式:"
            "work/lume/lume 里的 examples/react-ssr.lume 链路。")
    t = sanitize.as_title(desc)
    assert len(t) <= 46
    assert t.endswith(("引擎", "范式", "链路")) or "，" not in t
    assert "examples/react-ssr.lume" not in t
    assert sanitize.title_fallback("---\ntitle: x\n---\n\n## 落地改造\n", desc) == t
