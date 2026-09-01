"""frontmatter 解析与字段注入的单测。"""
from pipeline import frontmatter


def test_extract_frontmatter_roundtrip():
    art = "---\ntitle: X\ndate: 2026-09-01\n---\n\n# 标题\n\n正文\n"
    fm, body = frontmatter.extract_frontmatter(art)
    assert fm.startswith("---")
    assert "title: X" in fm
    assert body.strip().startswith("# 标题")


def test_extract_frontmatter_none():
    fm, body = frontmatter.extract_frontmatter("没有 frontmatter 的正文")
    assert fm == ""
    assert "没有 frontmatter" in body


def test_inject_frontmatter_description():
    art = "---\ntitle: X\n---\n\n正文\n"
    out = frontmatter.inject_frontmatter_description(art, "一段描述")
    assert "description:" in out and "一段描述" in out


def test_inject_frontmatter_excerpt():
    art = "---\ntitle: X\n---\n\n正文\n"
    out = frontmatter.inject_frontmatter_excerpt(art, "摘要")
    assert "excerpt:" in out and "摘要" in out


def test_fix_frontmatter_slug():
    art = "---\ntitle: X\nslug: foo\n---\n"
    out = frontmatter.fix_frontmatter_slug(art, "-en")
    assert "slug: foo-en" in out


def test_strip_outer_fence():
    assert frontmatter.strip_outer_fence("```md\nhi\n```") == "hi"
    assert frontmatter.strip_outer_fence("hi") == "hi"
