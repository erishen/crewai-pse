"""CrewAI PSE — 用三角色 Crew 撰写项目技术文章（中文 → 自动翻译英文）。

用法:
    python run.py <项目名> [--publish]

加 --publish 可在生成后自动调用 wordpress-tools 发布到线上。
项目配置从同目录下的 projects.json 读取，敏感路径通过 .env 环境变量配置。
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from crewai import Task
from dotenv import load_dotenv

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent.parent / "src"))
load_dotenv(BASE.parent.parent / ".env")

from crewai_pse import create_crew  # noqa: E402
from crewai_pse.config import settings  # noqa: E402

# 从环境变量读取根目录和输出目录，不再硬编码
ROOT = Path(os.getenv("PSE_ROOT", Path(__file__).resolve().parent.parent.parent.parent.parent))
ARTICLES_DIR = Path(os.getenv("ARTICLES_DIR", str(ROOT / "articles" / "pse")))

# 从 projects.json 加载项目配置（已加入 .gitignore，不上传）
PROJECTS_FILE = BASE / "projects.json"


def _clean_code_block_whitespace(text: str) -> str:
    """清理代码块内仅包含空格的行（保留真正的空行）。"""
    lines = text.split("\n")
    in_code_block = False
    result = []
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
        elif in_code_block and line.strip() == "" and len(line) > 0:
            # 代码块内只包含空格的行 → 改为真正空行
            result.append("")
        else:
            result.append(line)
    return "\n".join(result)


def _load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        print(f"❌ 找不到项目配置文件: {PROJECTS_FILE}")
        print("请从 projects.json.example 复制并填写实际配置")
        sys.exit(1)
    with open(PROJECTS_FILE, encoding="utf-8") as f:
        projects = json.load(f)

    # schema 校验
    required_keys = {"repo", "desc", "highlights", "source_dir"}
    for name, cfg in projects.items():
        missing = required_keys - set(cfg.keys())
        if missing:
            print(f"❌ projects.json [{name}] 缺少字段: {', '.join(missing)}")
            sys.exit(1)
    return projects


def _verify_article(article: str, source_dir: Path) -> tuple[list[str], list[str]]:
    """程序化验证：grep 检查代码引用是否存在。返回 (虚构列表, 正确列表)。"""
    refs = set(re.findall(r"`([A-Za-z_][\w._]*(?:/[A-Za-z_][\w._]*)*)`", article))
    for match in re.finditer(r"```(?:python)?\s*\n(.*?)```", article, re.DOTALL):
        for line in match.group(1).split("\n"):
            m = re.match(r"^\s*(?:def|class)\s+(\w+)", line)
            if m:
                refs.add(m.group(1))

    fictitious = []
    verified = []
    for ref in sorted(refs):
        if len(ref) < 3 or ref.startswith("http"):
            continue
        # 文件路径 → 递归搜磁盘（搜 .py 和 .md 文件）
        if "/" in ref or ref.endswith(".py") or ref.endswith(".md"):
            found = any(
                f.name == ref.rsplit("/", 1)[-1]
                for ext in ("*.py", "*.md")
                for f in source_dir.rglob(ext)
                if ".venv" not in str(f) and "__pycache__" not in str(f)
            )
            if found:
                verified.append(f"{ref} (文件存在)")
            else:
                fictitious.append(ref)
            continue
        # 类名/函数名 → grep（排除注释行，搜 .py 和 .md 文件）
        found = False
        for ext in ("*.py", "*.md"):
            for f in source_dir.rglob(ext):
                if ".venv" in str(f) or "__pycache__" in str(f):
                    continue
                try:
                    for line in f.read_text(encoding="utf-8").split("\n"):
                        stripped = line.strip()
                        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                            continue
                        if ref in stripped:
                            found = True
                            verified.append(f"{ref} → {f.relative_to(source_dir)}")
                            break
                    if found:
                        break
                except Exception:
                    continue
            if found:
                break
        if not found:
            fictitious.append(ref)

    # 命令路径检查
    for cmd_match in re.finditer(r"`(pip\s+install[^`]+|python\s+-m\s+[^`]+|cd\s+[^`]+)`", article):
        cmd = cmd_match.group(1)
        path_refs = re.findall(r"\S+/\S+", cmd)
        for p in path_refs:
            if not (source_dir / p.lstrip("/")).exists() and not (source_dir.parent / p.lstrip("/")).exists():
                fictitious.append(f"[命令] {cmd.strip()[:60]} (路径不存在)")

    # 描述夸大检查
    EXAGGERATED = {
        "断点恢复": "Trace 只用于审计和提取结论，不支持从断点恢复执行",
        "上下文压缩": "代码中无上下文压缩/摘要机制",
    }
    for keyword, reason in EXAGGERATED.items():
        if keyword in article:
            fictitious.append(f"[夸大] {keyword} — {reason}")

    return fictitious, verified


def main():
    projects = _load_projects()
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_publish = "--publish" in flags

    if not args or args[0] not in projects:
        print("用法: python run.py <项目名> [--publish]")
        print(f"可用项目: {', '.join(projects.keys())}")
        sys.exit(1)

    project_key = args[0]
    p = projects[project_key]
    source_dir = ROOT / p["source_dir"]

    from openai import OpenAI
    fix_client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
    )
    fix_model = settings.OPENAI_MODEL.replace("openai/", "")

    crew = create_crew(task="project-articles")

    planner_task = Task(
        description=f"""撰写 {p['desc']}（{project_key}）的中文技术文章。

## 项目信息
- GitHub: {p['repo']}
- 核心卖点: {p['highlights']}
- 源码目录: {source_dir}

## 你的任务
1. 用 read_file 读取源码目录下的关键文件（README.md + 核心 .py 文件）
2. 基于源码提炼 2-3 个非显而易见的设计决策
3. 输出文章结构提纲（标题、每节要点、源码导航推荐文件）
4. 提纲末尾附上"交付完成"
""",
        expected_output="文章结构提纲",
        agent=crew.agents[0],
    )

    specialist_task = Task(
        description=f"""基于 Planner 的提纲，展开成完整中文技术文章。

⚠️ 源码在 {source_dir}，用 read_file 读取关键文件验证后再写。

## 你的任务
1. 先用 read_file 读取 README.md 和核心 .py 文件
2. 基于实际源码 + Planner 提纲撰写文章
3. 所有代码示例、类名、函数名、API 用法必须从源码中提取

## 文章结构
1. Front Matter (title, date: {date.today().isoformat()}, slug, categories: ["tech"], tags)
2. 引入
3. 核心设计（每节含：直觉→实际→为什么更好）
4. 代码示例（从源码摘取真实代码）
5. 源码导航表格（必须含 GitHub 链接）
6. 快速开始

## 规范
- 源码导航格式: https://github.com/{p['repo']}/blob/main/<path>
- 禁止编造类名、函数名、文件路径、API 用法
- 禁止提到不存在的变量或机制
""",
        expected_output="完整的中文 Markdown 技术文章",
        agent=crew.agents[1],
    )

    # 执行：Planner(提纲) → Specialist(展开)
    print(f"🚀 CrewAI PSE 撰写 {p['desc']} 的中文文章...")
    crew.tasks = [planner_task, specialist_task]
    # 只跑一轮（Planner → Specialist），不循环
    # CrewAI 1.15+ 需要显式创建 event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        crew.kickoff()
    except RuntimeError as e:
        if "no running event loop" in str(e):
            # Fallback: run in new event loop
            asyncio.run(crew.kickoff_async())
        else:
            raise
    article = str(specialist_task.output.raw) if specialist_task.output else ""
    prompt_tokens = 0
    completion_tokens = 0

    # 程序化验证 + 自动修正
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        fictitious, verified = _verify_article(article, source_dir)
        print(f"\n{'='*60}")
        print(f"  核查 (第{attempt}次) — 已验证 {len(verified)} 项")
        if fictitious:
            print(f"  ❌ 虚构内容 {len(fictitious)} 项: {', '.join(fictitious)}")
            if attempt < max_retries:
                print("  🔄 自动修正中...")
                fix_prompt = f"""以下文章被核查发现存在虚构的代码引用，请逐一删除所有虚构引用。

**虚构项（在源码中不存在）**: {', '.join(fictitious)}

**规则**:
1. 逐一找到并删除以上每个虚构项在文章中的引用
2. 如果虚构项出现在代码示例中，删除整个示例或替换为源码中真实存在的代码
3. 如果虚构项出现在正文描述中，删除相关句子
4. 不要创造性替换 — 只删除，只保留源码中真实存在的内容

## 当前文章
{article}

输出修正后的完整文章（从 Front Matter 开始），不输出解释。"""
                try:
                    resp = fix_client.chat.completions.create(
                        model=fix_model,
                        messages=[{"role": "user", "content": fix_prompt}],
                        max_tokens=8192,
                        temperature=0.7,
                    )
                    article = resp.choices[0].message.content
                    usage = resp.usage
                    if usage:
                        prompt_tokens += usage.prompt_tokens
                        completion_tokens += usage.completion_tokens
                except Exception as e:
                    print(f"  ⚠️ API 调用失败: {e}")
                    continue
            else:
                print(f"\n❌ 修正{max_retries}次后仍有虚构内容")
                sys.exit(1)
        else:
            print("  ✅ 无虚构内容，全部通过")
            break

    # 保存中文
    slug = project_key.replace("-", "_")
    zh_path = ARTICLES_DIR / "zh" / f"{slug}.md"
    zh_path.parent.mkdir(parents=True, exist_ok=True)
    article = _clean_code_block_whitespace(article)
    zh_path.write_text(article, encoding="utf-8")
    print(f"\n✅ 中文已保存 → {zh_path}")

    # 翻译英文
    print("🌐 翻译英文版...")
    translate_prompt = (
        "Translate the following Chinese technical article to English. "
        "Keep ALL code examples, file paths, class names, and function names unchanged. "
        f"Output ONLY the translated article:\n\n{article}"
    )
    try:
        resp = fix_client.chat.completions.create(
            model=fix_model,
            messages=[{"role": "user", "content": translate_prompt}],
            max_tokens=8192,
            temperature=0.3,
        )
        en_content = _clean_code_block_whitespace(resp.choices[0].message.content)
        en_path = ARTICLES_DIR / "en" / f"{slug}.md"
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(en_content, encoding="utf-8")
        print(f"✅ 英文已保存 → {en_path}")
    except Exception as e:
        print(f"⚠️ 翻译失败（跳过）: {e}")

    # Token 统计
    total = prompt_tokens + completion_tokens
    print(f"\n📊 Token 消耗: {prompt_tokens} 输入 + {completion_tokens} 输出 = {total} 合计")
    if total:
        print(f"💰 预估费用: ¥{total * 0.0000014:.4f}")

    # 发布到 WordPress
    if do_publish:
        print(f"\n{'='*60}")
        print("🚀 发布文章到 WordPress...")
        pub_result = subprocess.run(
            [sys.executable, str(BASE / "publish.py"), project_key],
            capture_output=True,
            text=True,
        )
        print(pub_result.stdout)
        if pub_result.returncode != 0:
            print(pub_result.stderr)
            sys.exit(pub_result.returncode)


if __name__ == "__main__":
    main()
