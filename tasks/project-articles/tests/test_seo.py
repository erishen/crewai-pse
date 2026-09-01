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
