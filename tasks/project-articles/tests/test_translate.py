"""英文翻译完整性检查的单测（截断识别）。"""
from pipeline import translate


def test_detects_mid_sentence_truncation():
    zh = "## 复盘\n\n正文很长。" * 20
    en = "## Review\n\nThe text stops right in the middle of a sentence about the original `exists"
    assert translate.looks_truncated(zh, en)


def test_detects_missing_sections():
    zh = "\n## 一\n\nA\n\n## 二\n\nB\n\n## 三\n\nC\n"
    en = "\n## One\n\nA\n"
    assert translate.looks_truncated(zh, en) is not None


def test_accepts_complete_translation():
    zh = "\n## 一\n\nA\n\n## 二\n\nB\n"
    en = "\n## One\n\n" + "A" * 40 + ".\n\n## Two\n\n" + "B" * 40 + ".\n"
    assert translate.looks_truncated(zh, en) is None
