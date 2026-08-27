#!/usr/bin/env python3
"""扫描大项目下有 github remote 的子项目，建议加入 projects.json。

功能：
1. 递归扫描 individuular-invest 下的子目录，找有 .git 的项目
2. 检查 git remote origin 是否指向 github.com
3. 过滤掉 _archived、github（第三方克隆）、node_modules、.venv 等目录
4. 对比已有 projects.json，找出新增项目
5. 读取 README/package.json/pyproject.toml 生成 desc 和 highlights 建议
6. 输出可直接复制到 projects.json 的 JSON 片段

用法：python discover_projects.py [--add]
  --add  直接把新项目写入 projects.json（需确认）
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
ROOT = BASE.parent.parent  # crewai-pse 根
BIG_ROOT = ROOT.parent.parent  # individuular-invest 根
PROJECTS_FILE = BASE / "projects.json"
EXCLUDED_FILE = BASE / "projects-excluded.json"
PUBLISHED_FILE = BASE / "projects-published.json"

# 跳过的目录名
SKIP_DIRS = {
    "_archived", "github", "node_modules", ".venv", "venv",
    "__pycache__", ".git", "dist", "build", ".next", ".cache",
    "articles", "tasks", "skills",
}

# 跳过的路径前缀（相对 BIG_ROOT）
SKIP_PREFIXES = ("personal/_archived/", "github/")


def check_repo_visibility(repo_name: str) -> tuple[str, bool]:
    """检查 GitHub 仓库可见性，返回 (visibility, reachable)。

    visibility: "public" / "private" / "unknown"
    reachable: 仓库是否存在且可访问
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", repo_name, "--json", "visibility,isPrivate"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            vis = data.get("visibility", "unknown").lower()
            return vis, True
        # 404 或无权限
        if "Could not resolve" in result.stderr or "Not Found" in result.stderr:
            return "not_found", False
        return "unknown", False
    except FileNotFoundError:
        # gh CLI 未安装
        return "gh_not_installed", True
    except Exception:
        return "error", False


def has_github_remote(repo_dir: Path) -> str | None:
    """检查项目是否有 github remote，返回 remote URL 或 None。"""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        url = result.stdout.strip()
        if "github.com" in url:
            return url
    except Exception:
        pass
    return None


def extract_repo_name(remote_url: str) -> str:
    """从 git remote URL 提取 repo 名（owner/repo）。"""
    # git@github.com:owner/repo.git
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", remote_url)
    return m.group(1) if m else ""


def read_file_safe(path: Path, max_bytes: int = 8000) -> str:
    """安全读取文件，返回前 max_bytes 字节。"""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
    except Exception:
        return ""


def guess_desc(repo_dir: Path) -> str:
    """从 README 或配置文件猜测项目描述。"""
    # 1. README
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        p = repo_dir / name
        if p.exists():
            text = read_file_safe(p)
            # 取第一个非标题、非空、非HTML标签、非图片的行作为描述
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#") or line.startswith("!"):
                    continue
                if line.startswith("<") or line.startswith(">"):
                    continue
                if line.startswith("[!") or line.startswith("!["):
                    continue
                if "中文" in line and "English" in line and len(line) < 20:
                    continue
                if len(line) < 10:
                    continue
                # 清理 markdown 标记
                line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
                line = re.sub(r"[*_`]", "", line)
                line = re.sub(r"<[^>]+>", "", line)
                if len(line.strip()) >= 10:
                    return line.strip()[:120]
            break

    # 2. package.json
    pkg = repo_dir / "package.json"
    if pkg.exists():
        try:
            data = json.loads(read_file_safe(pkg))
            if data.get("description"):
                return data["description"][:120]
            if data.get("name"):
                return f"{data['name']} 项目"
        except Exception:
            pass

    # 3. pyproject.toml
    pyproj = repo_dir / "pyproject.toml"
    if pyproj.exists():
        text = read_file_safe(pyproj)
        m = re.search(r'description\s*=\s*"([^"]+)"', text)
        if m:
            return m.group(1)[:120]

    return repo_dir.name + " 项目"


def guess_highlights(repo_dir: Path) -> str:
    """从项目文件结构猜测技术亮点。"""
    highlights = []

    # 检查技术栈文件
    files = {f.name.lower() for f in repo_dir.iterdir() if f.is_file()} if repo_dir.exists() else set()

    if "package.json" in files:
        highlights.append("Node.js")
    if "pyproject.toml" in files or "setup.py" in files or "requirements.txt" in files:
        highlights.append("Python")
    if "pom.xml" in files or "build.gradle" in files:
        highlights.append("Java")
    if "go.mod" in files:
        highlights.append("Go")
    if "Cargo.toml" in files:
        highlights.append("Rust")
    if "docker-compose.yml" in files or "Dockerfile" in files:
        highlights.append("Docker")
    if "Makefile" in files:
        highlights.append("Makefile 构建")

    # 检查子目录
    try:
        dirs = {d.name.lower() for d in repo_dir.iterdir() if d.is_dir()}
    except Exception:
        dirs = set()

    if "src" in dirs:
        highlights.append("src/ 源码结构")
    if "tests" in dirs or "test" in dirs:
        highlights.append("测试覆盖")
    if ".github" in dirs:
        highlights.append("GitHub Actions CI")
    if "docs" in dirs:
        highlights.append("文档")

    # 检查框架特征
    pkg = repo_dir / "package.json"
    if pkg.exists():
        try:
            data = json.loads(read_file_safe(pkg))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "react" in deps:
                highlights.append("React")
            if "next" in deps or "next" in str(deps):
                highlights.append("Next.js")
            if "vue" in deps:
                highlights.append("Vue")
            if "typescript" in deps:
                highlights.append("TypeScript")
            if "fastapi" in str(deps).lower():
                highlights.append("FastAPI")
        except Exception:
            pass

    if not highlights:
        highlights.append("待补充技术亮点")

    return " + ".join(highlights[:6])


def scan_projects() -> list[dict]:
    """扫描所有有 github remote 的子项目。"""
    found = []
    for git_dir in BIG_ROOT.rglob(".git"):
        if not git_dir.is_dir():
            continue
        repo_dir = git_dir.parent

        # 计算相对路径
        try:
            rel = repo_dir.relative_to(BIG_ROOT)
        except ValueError:
            continue
        rel_str = str(rel)

        # 跳过规则
        if any(rel_str.startswith(p) for p in SKIP_PREFIXES):
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        # 只扫描 4 层以内
        if len(rel.parts) > 4:
            continue

        remote = has_github_remote(repo_dir)
        if not remote:
            continue

        repo_name = extract_repo_name(remote)
        found.append({
            "path": rel_str,
            "repo": repo_name or f"erishen/{repo_dir.name}",
            "remote": remote,
            "dir": repo_dir,
        })

    return found


def load_json_keys(path: Path) -> set[str]:
    """加载 JSON 文件的顶层 key 集合。"""
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")).keys())
        except Exception:
            pass
    return set()


def main():
    add_mode = "--add" in sys.argv

    # 加载已有 projects（三个文件都算"已处理"，避免重复建议）
    existing_keys = load_json_keys(PROJECTS_FILE)
    excluded_keys = load_json_keys(EXCLUDED_FILE)
    published_keys = load_json_keys(PUBLISHED_FILE)
    all_known = existing_keys | excluded_keys | published_keys

    existing = {}
    if PROJECTS_FILE.exists():
        existing = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))

    print(f"📂 扫描根目录: {BIG_ROOT}")
    print(f"📋 已有项目: {len(existing_keys)} 个 (projects.json)")
    print(f"🚫 已排除: {len(excluded_keys)} 个 (projects-excluded.json)")
    print(f"✅ 已发布: {len(published_keys)} 个 (projects-published.json)")
    print(f"🔒 合计已处理: {len(all_known)} 个")

    # 扫描
    found = scan_projects()
    print(f"\n🔍 发现有 github remote 的项目: {len(found)} 个")

    # 分类
    new_projects = []
    existing_projects = []
    for p in found:
        # 用目录名匹配（三个文件的 key 都是目录名）
        key = p["dir"].name
        if key in all_known:
            existing_projects.append(p)
        else:
            new_projects.append(p)

    print(f"\n🔍 发现有 github remote 的项目: {len(found)} 个")
    print(f"   已在三个配置文件中: {len(existing_projects)} 个")
    print(f"   新增候选: {len(new_projects)} 个")

    if not new_projects:
        print("\n✅ 没有新项目需要添加。")
        return

    # 生成建议条目（先检查远程可达性和可见性）
    print(f"\n{'='*60}")
    print("📝 建议新增的项目（可复制到 projects.json）：")
    print(f"{'='*60}\n")

    suggestions = {}
    unreachable = []
    private_repos = []
    for p in sorted(new_projects, key=lambda x: x["path"]):
        key = p["dir"].name

        # 检查远程仓库可达性和可见性
        visibility, reachable = check_repo_visibility(p["repo"])
        if not reachable:
            unreachable.append((key, p["repo"], p["path"]))
            print(f"⚠️  跳过 {key} — 远程仓库不可达 ({p['repo']})")
            continue
        if visibility == "private":
            private_repos.append((key, p["repo"], p["path"]))
            print(f"🔒 跳过私有仓库 {key} — {p['repo']}")
            continue
        if visibility == "public":
            print(f"🌐 公开仓库 {key} — {p['repo']}")

        desc = guess_desc(p["dir"])
        highlights = guess_highlights(p["dir"])
        source_dir = p["path"]

        entry = {
            "repo": p["repo"],
            "desc": desc,
            "highlights": highlights,
            "source_dir": source_dir,
        }
        suggestions[key] = entry

        print(f'"{key}": {{')
        print(f'  "repo": "{entry["repo"]}",')
        print(f'  "desc": "{entry["desc"]}",')
        print(f'  "highlights": "{entry["highlights"]}",')
        print(f'  "source_dir": "{entry["source_dir"]}"')
        print(f'}}')
        print()

    if unreachable:
        print(f"⚠️  远程不可达（本地配置了 origin 但 GitHub 上不存在/无权限）: {len(unreachable)} 个")
        for key, repo, path in unreachable:
            print(f"   - {key} ({repo}) → {path}")
        print()

    print(f"{'='*60}")
    print(f"共 {len(suggestions)} 个新项目建议（跳过 {len(private_repos)} 个私有，{len(unreachable)} 个不可达）")

    if add_mode:
        # 合并写入
        existing.update(suggestions)
        PROJECTS_FILE.write_text(
            json.dumps(existing, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )
        print(f"\n✅ 已写入 {PROJECTS_FILE}")
        print(f"   新增 {len(suggestions)} 个，总计 {len(existing)} 个项目")
    else:
        print("\n💡 运行 `python discover_projects.py --add` 直接写入 projects.json")
        print("   或手动复制上面的 JSON 片段到 projects.json")


if __name__ == "__main__":
    main()
