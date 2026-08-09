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
    """Replace an existing `heading` section (up to next \n## or EOF) with `block`."""
    pat = re.compile(re.escape(heading) + r".*?(?=\n## |\Z)", re.S)
    if pat.search(text):
        return pat.sub(block.rstrip("\n") + "\n", text)
    return None  # not present


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
        # EN
        en_path = os.path.join(d, "README.md")
        if os.path.isfile(en_path) and g["en"]:
            block = make_block(EN_HEADING, g["en"], titles)
            txt = open(en_path, encoding="utf-8").read()
            new = replace_section(txt, EN_HEADING, block)
            if new is None:
                new = insert_section(txt, block)
            if new != txt:
                if not dry:
                    open(en_path, "w", encoding="utf-8").write(new)
                changed.append(en_path)
        # ZH
        zh_path = find_zh_readme(d)
        if zh_path and g["zh"]:
            block = make_block(ZH_HEADING, g["zh"], titles)
            txt = open(zh_path, encoding="utf-8").read()
            new = replace_section(txt, ZH_HEADING, block)
            if new is None:
                new = insert_section(txt, block)
            if new != txt:
                if not dry:
                    open(zh_path, "w", encoding="utf-8").write(new)
                changed.append(zh_path)

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
