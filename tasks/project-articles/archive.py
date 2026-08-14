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
PUBLISHED_FILE = BASE / "projects-published.json"


def _load_projects() -> dict:
    pending = {}
    if PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, encoding="utf-8") as f:
            pending = json.load(f)
    published = {}
    if PUBLISHED_FILE.exists():
        with open(PUBLISHED_FILE, encoding="utf-8") as f:
            published = json.load(f)
    return {**published, **pending}


def main():
    projects = _load_projects()
    if len(sys.argv) < 2 or sys.argv[1] not in projects:
        print("用法: python archive.py <项目名>")
        print(f"可用项目: {', '.join(projects.keys())}")
        sys.exit(1)

    project_key = sys.argv[1]
    slug = project_key.replace("-", "_")
    slug_zh = f"{slug}-zh"
    slug_en = f"{slug}-en"

    if not WP_TOOLS_DIR or not WP_TOOLS_DIR.exists():
        print("❌ 请设置 WP_TOOLS_DIR 环境变量指向 wordpress-tools 目录")
        sys.exit(1)

    if not ARTICLES_DIR or not ARTICLES_DIR.exists():
        print("❌ ARTICLES_DIR 未设置或目录不存在")
        sys.exit(1)

    archived = 0
    for lang in ("zh", "en"):
        lang_slug = slug_zh if lang == "zh" else slug_en
        src = ARTICLES_DIR / lang / f"{lang_slug}.md"
        # 兼容旧文件名（无语言后缀）
        if not src.exists():
            legacy = ARTICLES_DIR / lang / f"{slug}.md"
            if legacy.exists():
                src = legacy
                lang_slug = slug
        if not src.exists():
            print(f"  ⏭️  {lang} 文章不存在，跳过")
            continue
        dest_dir = WP_TOOLS_DIR / "articles" / lang
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{lang_slug}.md"
        # 保留 pse/ 工作副本（生成/翻译/发布的唯一真值源），仅同步一份到
        # wordpress-tools/articles/ 供 juejin/segmentfault/wechat 构建使用。
        # 以前用 shutil.move 会把工作副本搬走，导致归档后 make translate / make publish
        # 读不到 pse/ 下的源文件而失败；改为 copy 后两者都可用，重跑 archive 亦会刷新副本。
        shutil.copy2(src, dest)
        print(f"  📁 {lang} 已归档（副本）→ {dest}")
        print(f"  🔒 工作副本保留于 {src}")
        archived += 1

    print(f"\n📊 归档完成: {archived} 篇（pse/ 工作副本已保留，可继续 make translate / make publish）")


if __name__ == "__main__":
    main()
