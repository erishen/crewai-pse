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
