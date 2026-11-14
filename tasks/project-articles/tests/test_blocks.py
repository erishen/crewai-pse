"""FAQ / TL;DR 区块提取与归一化的单测。

注意：FAQ 用的是 [faq]...[/faq] 短代码格式（非 markdown 标题），
count_faq_blocks 依赖 FAQ_BLOCK_RE 匹配该短代码。
"""
from pipeline import blocks

FAQ = (
    "[faq]\n问：是什么？\n答：是一个工具。\n[/faq]\n\n"
    "[faq]\n问：怎么用？\n答：运行命令。\n[/faq]\n"
)

TLDR = "## TL;DR\n\n- 这是全局结论摘要的第一条要点\n- 第二条\n\n# 标题\n\n正文\n"


def test_count_faq_blocks():
    assert blocks.count_faq_blocks(FAQ) == 2
    assert blocks.count_faq_blocks("无 FAQ 正文") == 0


def test_normalize_faq_blocks_preserves_count():
    out = blocks.normalize_faq_blocks(FAQ, "zh")
    assert blocks.count_faq_blocks(out) == 2


def test_count_tldr_bullets():
    assert blocks.count_tldr_bullets(TLDR) == 2


def test_normalize_tldr_keeps_bullets():
    out = blocks.normalize_tldr(TLDR, "zh")
    assert blocks.count_tldr_bullets(out) == 2


class _FakeLLM:
    """最小可用的 OpenAI 兼容桩：按顺序吐出预设回复。"""

    class _Msg:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _FakeLLM._Msg(content)

    class _Resp:
        def __init__(self, content):
            self.choices = [_FakeLLM._Choice(content)]

    class _Completions:
        def __init__(self, replies):
            self.replies = list(replies)

        def create(self, **kwargs):
            return _FakeLLM._Resp(self.replies.pop(0) if self.replies else "")

    def __init__(self, replies):
        self.chat = type("Chat", (), {"completions": _FakeLLM._Completions(replies)})()


ONE_QA = "[faq]\n问：它解决什么问题？\n答：把 SSR 后端与端口分开。\n[/faq]"
FOUR_QA = "\n".join(
    f"[faq]\n问：问题{i}？\n答：答案{i}。\n[/faq]" for i in range(1, 5)
)


def test_auto_generate_faq_retries_until_four_items():
    """只写出 1 条时不能算成功（实跑就是 1 条被放行），必须重试取到 4 条。"""
    llm = _FakeLLM([ONE_QA, FOUR_QA])
    out = blocks.auto_generate_faq(
        "## 正文\n\n这里讲端口归属与中继。", {"desc": "项目描述"}, "主线", llm, "m"
    )
    assert blocks.count_faq_blocks(out) == 4


def test_auto_generate_faq_keeps_best_effort_when_always_short():
    """三次都写不满 4 条：沿用现有区块而不是整体丢弃（有 1 条总比没有强）。"""
    llm = _FakeLLM([ONE_QA, ONE_QA, ONE_QA])
    out = blocks.auto_generate_faq(
        "## 正文\n\n内容。", {"desc": "项目描述"}, "主线", llm, "m"
    )
    assert blocks.count_faq_blocks(out) == 1


def test_auto_generate_tldr_builds_bullets():
    llm = _FakeLLM(["## 速览\n- 两个进程：Lume 引擎持端口\n- 后端只监听本地 socket\n- 登录路径不经中继"])
    block = blocks.auto_generate_tldr(
        "## 落地\n\n两个进程。", {"desc": "项目描述"}, "主线", llm, "m"
    )
    assert block.startswith("## 速览（TL;DR）")
    assert blocks.TLDR_BLOCK_RE.search(block)
    assert block.count("- ") == 3


def test_auto_generate_tldr_rejects_too_few_bullets():
    llm = _FakeLLM(["- 只有一条要点\n- 第二条"])
    assert blocks.auto_generate_tldr("正文。", {"desc": "d"}, "", llm, "m") == ""
