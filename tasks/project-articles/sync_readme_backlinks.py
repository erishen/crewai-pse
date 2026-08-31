#!/usr/bin/env python3
"""
Sync "Related Articles" backlinks into each project's local README,
driven entirely by projects-published.json (the single source of truth).

Design notes:
- Reads projects-published.json, which maps project -> {source_dir, published:{zh,en:{link,wp_id}}}.
- Groups entries by `source_dir` so a repo with multiple articles (e.g. autogen-pse
  has 2) gets them all listed in one README.
- Edits the LOCAL repo at individular-invest/<source_dir> directly (NO git clone).
- EN README (README.md) gets only EN article links; ZH README (README.zh.md /
  README.zh-CN.md) gets only ZH links.
- Idempotent: re-running reproduces the same content; only writes when changed.
- Article anchor text (title) is looked up from the article source files by wp_id,
  so the JSON stays schema-clean (no title field needed).

Usage:
  python3 sync_readme_backlinks.py [--dry]
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))  # individular-invest
JSON_PATH = os.path.join(HERE, "projects-published.json")
ARTICLES_DIR = os.path.join(ROOT, "personal", "personal-site", "wordpress-tools", "articles")

EN_HEADING = "## Related Articles"
ZH_HEADING = "## 相关文章"


def load_published():
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_title_map():
    """wp_id -> title, scanned from article source frontmatter."""
    titles = {}
    for lang in ("zh", "en"):
        d = os.path.join(ARTICLES_DIR, lang)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".md"):
                continue
            txt = open(os.path.join(d, fn), encoding="utf-8").read()
            mw = re.search(r"^wp_id:\s*(\d+)", txt, re.M)
            mt = re.search(r"^title:\s*(.+)$", txt, re.M)
            if mw and mt:
                titles[int(mw.group(1))] = mt.group(1).strip().strip('"').strip("'")
    return titles


def group_by_source_dir(pub):
    """source_dir -> {zh:[(title,link)], en:[(title,link)]} sorted by wp_id."""
    groups = {}
    for proj, info in pub.items():
        sd = info.get("source_dir")
        if not sd:
            continue
        g = groups.setdefault(sd, {"zh": [], "en": []})
        for lang in ("zh", "en"):
            p = info["published"].get(lang)
            if p:
                g[lang].append((p["wp_id"], p["link"]))
    for g in groups.values():
        for lang in ("zh", "en"):
            g[lang].sort()  # tuples (wp_id, link) sort by wp_id
    return groups


def find_zh_readme(d):
    for name in ("README.zh.md", "README.zh-CN.md"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def replace_section(text, heading, block):
    """Replace the `heading` section (heading line + its following blank/'- ' list
    lines) with `block`, preserving any other trailing content in the section.

    Bugs fixed vs. old greedy pattern (`heading.*?(?=\\n## |\\Z)`):
    - now anchored at line start with (?!#) so '### Related Articles' (a level-3
      subsection, e.g. under '## Articles & Resources') is NOT matched;
    - only consumes blank lines and list items right after the heading, so things
      like a '<div align=center>🇨🇳 中文文档</div>' nav line at the section end
      survive the rewrite.
    Returns None when the section is absent.
    """
    m = re.search(r"^" + re.escape(heading) + r"(?!#)[^\n]*\n", text, re.M)
    if not m:
        return None
    tail = re.compile(r"(?:[ \t]*\n|- [^\n]*\n)*").match(text, m.end())
    end = tail.end()
    rest = text[end:]
    # 章节后仍有内容时补一个空行分隔；位于文件末尾则不加，保证 re-run 幂等
    glue = "\n" if rest.strip() else ""
    return text[: m.start()] + block + glue + rest


def insert_section(text, block):
    """Insert block before '## License' if present, else append at end."""
    block = block.rstrip("\n") + "\n"
    m = re.search(r"\n## License", text)
    if m:
        idx = m.start() + 1
        return text[:idx] + block + text[idx:]
    return text.rstrip("\n") + "\n\n" + block


def make_block(heading, items, titles):
    # 注意：标题后不加空行，与已发布到 GitHub 的 README 格式保持一致
    # （线上是 `## Related Articles` 直接接列表项，无空行；加空行会导致 re-run 时产生 diff）
    lines = [heading]
    for wp, link in items:
        title = titles.get(wp, link)
        lines.append(f"- [{title}]({link})")
    return "\n".join(lines) + "\n"


def sync_file(path, heading, items, titles, dry, changed):
    """Sync one README: replace its article section, or insert if absent.

    Skip insertion when the links already appear elsewhere in the file (e.g. a
    hand-maintained subsection like '### Related Articles' under a resources
    section) — re-inserting would duplicate the same links.
    """
    block = make_block(heading, items, titles)
    txt = open(path, encoding="utf-8").read()
    new = replace_section(txt, heading, block)
    if new is None:
        if all(link in txt for _, link in items):
            return
        new = insert_section(txt, block)
    if new != txt:
        if not dry:
            open(path, "w", encoding="utf-8").write(new)
        changed.append(path)


def process(dry):
    pub = load_published()
    titles = build_title_map()
    groups = group_by_source_dir(pub)
    changed = []

    for sd, g in groups.items():
        d = os.path.join(ROOT, sd)
        if not os.path.isdir(d):
            print(f"[SKIP] {sd}: local dir not found")
            continue
        en_path = os.path.join(d, "README.md")
        if os.path.isfile(en_path) and g["en"]:
            sync_file(en_path, EN_HEADING, g["en"], titles, dry, changed)
        zh_path = find_zh_readme(d)
        if zh_path and g["zh"]:
            sync_file(zh_path, ZH_HEADING, g["zh"], titles, dry, changed)

    if dry:
        print(f"[DRY] would change {len(changed)} file(s)")
    else:
        print(f"[DONE] changed {len(changed)} file(s)")
    for p in changed:
        print("   ", os.path.relpath(p, ROOT))
    return changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    process(args.dry)
