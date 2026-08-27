#!/usr/bin/env python3
"""扫描大项目下有 github remote 的子项目，建议加入 projects.json。

功能：
1. 递归扫描 individular-invest 下的子目录，找有 .git 的项目
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


def _clean_md(line: str) -> str:
    """去掉 markdown 链接/图片/强调/HTML 标记，返回纯文本。"""
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)  # 图片
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)  # 链接 -> 文本
    line = re.sub(r"[*_`]", "", line)
    line = re.sub(r"<[^>]+>", "", line)
    line = re.sub(r"\s+", " ", line).strip()
    return line


def _is_noisy_line(line: str) -> bool:
    """README 中不属于『描述段落』的行（标题/徽章/列表/表格/语言切换等）。"""
    s = line.strip()
    if not s:
        return True
    if s.startswith("#"):  # 标题
        return True
    if s.startswith("!"):  # 图片/徽章
        return True
    if s.startswith(">"):  # 引用块
        return True
    if s.startswith("|"):  # 表格
        return True
    if re.match(r"^[-*+]\s", s):  # 列表
        return True
    if "shields.io" in s or "img.shields" in s or "[![" in s:  # 状态徽章
        return True
    # 语言切换行：English | [中文]、Language:、语言:
    if re.search(r"(english|中文|language|lang)", s, re.I) and ("|" in s or "[" in s or "README" in s) and len(s) < 80:
        return True
    return False


def guess_desc(repo_dir: Path) -> str:
    """从 README 首段或 manifest 的 description 猜测项目描述（更丰富、去噪）。"""
    # 1. README：取第一个『描述段落』（连续非噪声行合并，支持跨行描述）
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        p = repo_dir / name
        if not p.exists():
            continue
        text = read_file_safe(p, max_bytes=12000)
        buf: list[str] = []
        for raw in text.split("\n"):
            line = raw.strip()
            if _is_noisy_line(line):
                if buf:
                    break
                continue
            cleaned = _clean_md(line)
            if len(cleaned) < 8:
                if buf:
                    break
                continue
            buf.append(cleaned)
            if sum(len(x) for x in buf) > 200:  # 攒够约 200 字就停
                break
        if buf:
            desc = " ".join(buf).strip()
            # 按词边界截断，避免切断单词
            if len(desc) > 220:
                desc = desc[:220].rsplit(" ", 1)[0].rstrip(",.;: ") + "…"
            return desc
        break

    # 2. manifest description（兜底，干净的单句）
    for spec in [
        ("package.json", r'"description"\s*:\s*"([^"]+)"'),
        ("pyproject.toml", r'description\s*=\s*"([^"]+)"'),
        ("Cargo.toml", r'description\s*=\s*"([^"]+)"'),
        ("setup.py", r'description\s*=\s*"([^"]+)"'),
    ]:
        fp = repo_dir / spec[0]
        if fp.exists():
            m = re.search(spec[1], read_file_safe(fp))
            if m and len(m.group(1)) >= 10:
                return m.group(1)[:200]

    return repo_dir.name + " 项目"


def guess_highlights(repo_dir: Path) -> str:
    """从项目文件结构猜测技术亮点（覆盖面更广，永不出占位符）。"""
    highlights: list[str] = []
    files = {f.name for f in repo_dir.iterdir() if f.is_file()} if repo_dir.exists() else set()
    dirs = {d.name for d in repo_dir.iterdir() if d.is_dir()} if repo_dir.exists() else set()

    # ── 生态 / 语言 ──
    if (repo_dir / "pnpm-workspace.yaml").exists() or (repo_dir / "lerna.json").exists():
        highlights.append("pnpm monorepo")
    if (repo_dir / "go.mod").exists():
        highlights.append("Go")
    if (repo_dir / "Cargo.toml").exists():
        highlights.append("Rust")
        cargo = read_file_safe(repo_dir / "Cargo.toml")
        if "ratatui" in cargo:
            highlights.append("ratatui TUI")
        if "tokio" in cargo:
            highlights.append("异步(tokio)")
    if (repo_dir / "package.json").exists():
        if "pnpm-workspace.yaml" not in files:  # monorepo 已标注，避免重复
            highlights.append("Node.js")
        try:
            data = json.loads(read_file_safe(repo_dir / "package.json"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            dl = {k.lower(): v for k, v in deps.items()}
            if "react" in dl:
                highlights.append("React")
            if "next" in dl:
                highlights.append("Next.js")
            if "vue" in dl:
                highlights.append("Vue")
            if "typescript" in dl:
                highlights.append("TypeScript")
            if "vite" in dl:
                highlights.append("Vite")
            if "tailwindcss" in dl:
                highlights.append("Tailwind CSS")
            if "express" in dl or "fastify" in dl or "koa" in dl:
                highlights.append("Web 框架")
        except Exception:
            pass
    if (repo_dir / "pyproject.toml").exists() or (repo_dir / "setup.py").exists() or (repo_dir / "requirements.txt").exists():
        highlights.append("Python")

    # ── Skills 仓库特征 ──
    for spec in ["SKILL_SPEC.md", "SKILLSPEC.md"]:
        if (repo_dir / spec).exists():
            highlights.append("Skills 规范(SKILL_SPEC)")
            break
    if "skills" in dirs:
        highlights.append("提示词包集合")
    if "souls" in dirs:
        highlights.append("PSE 角色(souls)")

    # ── 基础设施 / DevOps ──
    if (repo_dir / "Dockerfile").exists() or (repo_dir / "docker-compose.yml").exists():
        highlights.append("Docker")
    if (repo_dir / "Makefile").exists():
        highlights.append("Make 构建")
    if ".github" in dirs:
        highlights.append("GitHub Actions CI")
    if (repo_dir / "docker-compose.yml").exists():
        highlights.append("容器编排")
    if any(f.startswith("LICENSE") for f in files):
        highlights.append("开源协议")

    # ── 文档 / 测试 / 前端 ──
    if "docs" in dirs:
        highlights.append("文档")
    if "tests" in dirs or "test" in dirs:
        highlights.append("测试覆盖")
    if "apps" in dirs or "web" in dirs or "frontend" in dirs:
        highlights.append("前后端分离(web)")
    if "README.zh.md" in files or "README.en.md" in files or "README.zh-CN.md" in files:
        highlights.append("中英双语")

    # ── 兜底：按根目录文件扩展名猜语言 ──
    if not highlights:
        exts: dict[str, int] = {}
        for f in repo_dir.iterdir():
            if f.is_file():
                exts[f.suffix] = exts.get(f.suffix, 0) + 1
        if exts.get(".rs", 0) > 0:
            highlights.append("Rust")
        elif (exts.get(".ts", 0) + exts.get(".tsx", 0)) > 0:
            highlights.append("TypeScript")
        elif exts.get(".py", 0) > 0:
            highlights.append("Python")
        elif exts.get(".go", 0) > 0:
            highlights.append("Go")
        elif exts.get(".md", 0) > 0:
            highlights.append("Markdown 内容")
        else:
            highlights.append("详见 README")

    # 去重并按出现顺序保留，最多 8 个
    return " + ".join(list(dict.fromkeys(highlights))[:8])


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
