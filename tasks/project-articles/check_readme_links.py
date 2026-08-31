#!/usr/bin/env python3
"""
READ-ONLY check: every published article link must appear in its project's
README (EN links -> README.md, ZH links -> README.zh.md / README.zh-CN.md),
driven by projects-published.json. Companion to sync_readme_backlinks.py:
run `sync --dry` then this, or use it in CI / pre-commit sanity passes.

Exit code: 0 all good, 1 issues found.

Known acceptable gap: projects without a ZH README at all are reported as
"ZH README 不存在" — sync never creates those files; fix by writing a ZH
README or (if truly EN-only) accept the report line.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))  # individular-invest
JSON_PATH = os.path.join(HERE, "projects-published.json")

EN_README = "README.md"
ZH_READMES = ("README.zh.md", "README.zh-CN.md")


def read_if(path):
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    return None


def main():
    with open(JSON_PATH, encoding="utf-8") as f:
        published = json.load(f)

    groups = {}
    for key, entry in published.items():
        sd = entry.get("source_dir")
        if not sd:
            print(f"[WARN] {key}: no source_dir")
            continue
        g = groups.setdefault(sd, {"zh": [], "en": []})
        for lang in ("zh", "en"):
            link = (entry.get("published") or {}).get(lang, {}).get("link")
            if link:
                g[lang].append((key, link))
            else:
                print(f"[WARN] {key}: missing {lang} link")

    problems = 0
    ok_count = 0
    for sd in sorted(groups):
        g = groups[sd]
        if not os.path.isdir(os.path.join(ROOT, sd)):
            print(f"[MISS] {sd}: local dir not found")
            problems += 1
            continue
        file_problems = []
        for lang, fnames in (("en", [EN_README]), ("zh", list(ZH_READMES))):
            if not g[lang]:
                continue
            found = None
            for fn in fnames:
                txt = read_if(os.path.join(ROOT, sd, fn))
                if txt is not None:
                    found = (fn, txt)
                    break
            if found is None:
                file_problems.append(f"{lang.upper()} README missing ({' / '.join(fnames)})")
                continue
            fn, txt = found
            for key, link in g[lang]:
                if link not in txt:
                    file_problems.append(f"{lang.upper()} link absent in {fn}: {key} {link}")
        if file_problems:
            problems += 1
            print(f"[FAIL] {sd}")
            for p in file_problems:
                print(f"       - {p}")
        else:
            ok_count += 1

    print(f"\n{len(groups)} project(s): {ok_count} OK, {problems} with issues")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
