"""将 ARTICLES_DIR 中的文章归档到 wordpress-tools/articles/{zh,en}/。

用法:
    python archive.py <项目名>

独立于发布流程，可在发布后按需手动执行。
"""

import json
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE.parent.parent / ".env")
ARTICLES_DIR = Path(os.getenv("ARTICLES_DIR", ""))
WP_TOOLS_DIR = Path(os.getenv("WP_TOOLS_DIR", ""))
PROJECTS_FILE = BASE / "projects.json"


def _load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        print(f"❌ 找不到项目配置文件: {PROJECTS_FILE}")
        sys.exit(1)
    with open(PROJECTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def main():
    projects = _load_projects()
    if len(sys.argv) < 2 or sys.argv[1] not in projects:
        print("用法: python archive.py <项目名>")
        print(f"可用项目: {', '.join(projects.keys())}")
        sys.exit(1)

    project_key = sys.argv[1]
    slug = project_key.replace("-", "_")

    if not WP_TOOLS_DIR or not WP_TOOLS_DIR.exists():
        print("❌ 请设置 WP_TOOLS_DIR 环境变量指向 wordpress-tools 目录")
        sys.exit(1)

    if not ARTICLES_DIR or not ARTICLES_DIR.exists():
        print("❌ ARTICLES_DIR 未设置或目录不存在")
        sys.exit(1)

    archived = 0
    for lang in ("zh", "en"):
        src = ARTICLES_DIR / lang / f"{slug}.md"
        if not src.exists():
            print(f"  ⏭️  {lang} 文章不存在，跳过")
            continue
        dest_dir = WP_TOOLS_DIR / "articles" / lang
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{slug}.md"
        shutil.move(src, dest)
        print(f"  📁 {lang} 已归档 → {dest}")
        archived += 1

    # 清理空的 pse 子目录
    for lang in ("zh", "en"):
        lang_dir = ARTICLES_DIR / lang
        if lang_dir.exists() and not any(lang_dir.iterdir()):
            lang_dir.rmdir()
    if ARTICLES_DIR.exists() and not any(ARTICLES_DIR.iterdir()):
        ARTICLES_DIR.rmdir()

    print(f"\n📊 归档完成: {archived} 篇")


if __name__ == "__main__":
    main()
