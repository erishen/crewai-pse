"""SEO 注入（meta description / 系列内链）的单测。

series_links 依赖 projects-published.json，测试用 monkeypatch 注入临时文件，
避免与真实清单耦合；同时验证「非 -pse 项目不注入兄弟内链」的守卫。
"""
import json
import tempfile
from pathlib import Path

import pipeline.seo as seo

TLDR = "## TL;DR\n\n- 这是全局结论摘要的第一条要点\n- 第二条\n\n# 标题\n\n正文\n"


def test_auto_description_from_tldr():
    desc = seo.auto_description(TLDR, "zh")
    assert "全局结论摘要" in desc
    assert len(desc) <= 160


def test_auto_description_fallback_paragraph():
    art = "---\ntitle: X\n---\n\n第一段正文作为兜底摘要内容。\n\n第二段。\n"
    desc = seo.auto_description(art, "zh")
    assert "兜底摘要" in desc


def test_auto_description_prefers_excerpt_over_truncated_paragraph():
    # 无 TL;DR 时，曾有 bug：直接取首段 [:150] 硬切成半句（如「每一层的归属都」）。
    # 修复后：优先复用语料里已有的 excerpt（干净的 LLM 归纳），避免半句截断。
    art = (
        "---\ntitle: X\nexcerpt: 这是一句完整的归纳式列表摘要，讲的是容器边界按特权切分。\n---\n\n"
        "第一段正文非常长，长到如果硬切成 150 字一定会在半句的地方被截断而读不通，"
        "所以必须回退到 excerpt 而不是拿这一段来切。\n\n第二段。\n"
    )
    desc = seo.auto_description(art, "zh")
    assert "完整的归纳式列表摘要" in desc
    assert "硬切成" not in desc  # 没有回退到被截断的首段


def test_auto_description_fallback_is_sentence_safe():
    # 兜底首段若超长，应在句末标点处切断，而不是 [:n] 半句。
    art = (
        "---\ntitle: X\n---\n\n"
        "第一句结论很清楚。第二句也很完整，但它足够长以至于超过了一百五十字这个上限"
        "所以应当停在前面那个句号而不是在中间某个字戛然而止让搜索引擎抓到半句话。\n\n第二段。\n"
    )
    desc = seo.auto_description(art, "zh")
    assert desc.endswith("。")
    assert "第二段" not in desc
    assert len(desc) <= 150


def test_auto_description_en_long_sentence_no_word_cut():
    # 英文 excerpt 是一整句 >160 字、句号在 160 之外：不能断在单词中间（如 "...the"）。
    excerpt = (
        "The system splits container boundaries by privilege requirements rather than tech "
        "stack: the C runtime stays in the container and only runs the agent loop, the sidecar "
        "that needs to execute child processes, write to disk, and read repository files is "
        "moved to the host machine, and the frontend container uses a self-written Node reverse "
        "proxy to handle SSE dialect translation and request body shape conversion. Two pure "
        "function contracts can be unit-tested independently, and at startup it also validates "
        "whether the four tool lists have drifted."
    )
    art = "---\ntitle: X\nexcerpt: %s\n---\n\n%s\n\nbody\n" % (excerpt, excerpt)
    desc = seo.auto_description(art, "en")
    assert not desc.endswith("the")  # 没有断在单词中间
    assert len(desc) <= 160
    assert desc.endswith((":", ",", "."))  # 落在句末或次级断点，读得通


def test_series_links_non_pse_empty():
    assert seo.series_links("markdown-library", "zh") == []


def test_series_links_filters_self_and_nonpse(monkeypatch, tmp_path):
    data = {
        "crewai-pse": {"published": {"zh": {"link": "https://erishen.cn/crewai-pse"}}},
        "langgraph-pse": {"published": {"zh": {"link": "https://erishen.cn/langgraph-pse"}}},
        "markdown-library": {"published": {"zh": {"link": "https://erishen.cn/md"}}},
    }
    f = tmp_path / "published.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(seo, "PUBLISHED_FILE", f)
    links = seo.series_links("crewai-pse", "zh")
    keys = [k for k, _ in links]
    assert "crewai-pse" not in keys
    assert "langgraph-pse" in keys
    assert "markdown-library" not in keys  # 非 -pse 兄弟被排除


def test_inject_series_links_non_pse_unchanged():
    art = "# 标题\n\n正文\n"
    assert seo.inject_series_links(art, "markdown-library", "zh") == art


def test_inject_series_links_appends(monkeypatch, tmp_path):
    data = {"langgraph-pse": {"published": {"zh": {"link": "https://erishen.cn/lg"}}}}
    f = tmp_path / "published.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(seo, "PUBLISHED_FILE", f)
    art = "# 标题\n\n正文\n"
    out = seo.inject_series_links(art, "crewai-pse", "zh")
    assert "同系列文章" in out
    assert "https://erishen.cn/lg" in out
