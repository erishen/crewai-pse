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
import shutil
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


def _src_mirror(src: Path, dst: Path) -> None:
    """把目标项目源码镜像进沙箱内目录，供 LLM 经 read_file 读取。

    read_file 沙箱限定在 crewai-pse 仓库根（见 crewai_pse/tools.py 的
    _PROJECT_ROOT），无法直接读取 frameworks/langgraph-pse 等外部目录。
    这里把真实源码复制进 crewai-pse 内的缓存目录，使 LLM 仍能经 read_file
    读取目标项目；dst 即"项目仓库根"，nav 链接的相对路径因此保持正确。
    """
    EXCLUDE_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".src_cache"}
    EXCLUDE_NAMES = {".env", ".env.example", ".env.local"}
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.name in EXCLUDE_NAMES:
            continue
        if path.is_file():
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


# 全站统一的 WordPress 标签（避免孤立标签）。中英文各自一套。
# 改这里即可调整所有文章的标签。
STANDARD_TAGS_ZH = ["AI助手", "架构设计", "开源工具"]
STANDARD_TAGS_EN = ["AI Assistant", "Architecture Design", "Open Source Tool"]


def _strip_outer_fence(text: str) -> str:
    """去掉模型偶尔加在最外层的 ```markdown ... ``` 包裹（含残缺情形）。

    WordPress 发布时会把整个内容当代码块渲染，必须剥掉。

    覆盖两种情形：
    - 成对包裹：整体以 ``` 开头且以 ``` 结尾 → 首尾围栏都剥。
    - 残缺包裹：只有开头 ```markdown、结尾忘记闭合 → 只剥首行围栏即可
      （正文中其余 ``` 代码块保持不动）。

    正常文章以 frontmatter 的 --- 开头，首行不是围栏，不受影响。
    """
    t = text.strip()
    lines = t.split("\n")
    # 剥首行围栏（成对 / 只有开头 两种情形都覆盖）
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    # 剥结尾闭合围栏（仅当存在，处理成对情形）
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _fix_frontmatter_slug(text: str, suffix: str = "") -> str:
    """归一化 frontmatter 的 slug：去掉可能的 -zh/-en 后缀，再追加指定后缀。

    中文版 suffix=""（无后缀），英文版 suffix="-en"。与既有文章命名规则统一：
    中文版 slug 不加语言后缀，英文版加 -en。
    """
    def repl(m):
        base = re.sub(r"-(zh|en)$", "", m.group(1).strip().strip('"').strip("'"))
        return f"slug: {base}{suffix}"
    return re.sub(r'^slug:\s*["\']?(.+?)["\']?\s*$', repl, text, count=1, flags=re.MULTILINE)


def _set_frontmatter_tags(text: str, tags: list[str]) -> str:
    """强制覆写 frontmatter 的 tags 为给定标准标签集（统一全站标签）。"""
    tags_yaml = "[" + ", ".join(f'"{t}"' for t in tags) + "]"
    if re.search(r'^tags:\s*.+$', text, flags=re.MULTILINE):
        return re.sub(r'^tags:\s*.+$', f"tags: {tags_yaml}", text, count=1, flags=re.MULTILINE)
    # 没有 tags 行则在首个 --- 后插入
    return re.sub(r'^(---\n)', lambda m: f"{m.group(1)}tags: {tags_yaml}\n", text, count=1)


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
    """程序化验证：grep 检查代码引用 + 环境变量 + 安装命令 + CLI 入口点。返回 (虚构列表, 正确列表)。"""
    refs = set(re.findall(r"`([A-Za-z_][\w._]*(?:/[A-Za-z_][\w._]*)*)`", article))
    for match in re.finditer(r"```(?:python)?\s*\n(.*?)```", article, re.DOTALL):
        for line in match.group(1).split("\n"):
            m = re.match(r"^\s*(?:def|class)\s+(\w+)", line)
            if m:
                refs.add(m.group(1))

    # Python 关键字和内置名称，不应作为项目代码引用检查
    PYTHON_KEYWORDS = {
        "def", "class", "import", "from", "return", "yield", "raise",
        "try", "except", "finally", "with", "as", "if", "elif", "else",
        "for", "while", "break", "continue", "pass", "lambda", "and",
        "or", "not", "in", "is", "True", "False", "None", "self", "cls",
        "async", "await", "global", "nonlocal", "del", "assert",
        "print", "len", "range", "str", "int", "float", "list", "dict",
        "set", "tuple", "bool", "type", "super", "isinstance", "issubclass",
        "hasattr", "getattr", "setattr", "enumerate", "zip", "map", "filter",
        "sorted", "reversed", "any", "all", "open", "input", "format",
        "property", "staticmethod", "classmethod", "abstractmethod",
        "Optional", "Union", "List", "Dict", "Set", "Tuple", "Any",
        "Callable", "Iterable", "Iterator", "Generator", "Sequence",
    }

    fictitious = []
    verified = []
    for ref in sorted(refs):
        if len(ref) < 3 or ref.startswith("http"):
            continue
        if ref in PYTHON_KEYWORDS:
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

    # ── 环境变量名核查 ──
    # 从 .env.example 提取真实变量名，与文章中的 export/X= 比对
    env_example = source_dir / ".env.example"
    if env_example.exists():
        real_vars = set()
        for line in env_example.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                var_name = line.split("=", 1)[0].strip()
                real_vars.add(var_name)
        # 也提取注释里的变量（如 # AGNES_KEY=...）
        for line in env_example.read_text(encoding="utf-8").splitlines():
            m = re.match(r"#\s*(\w+)=", line)
            if m:
                real_vars.add(m.group(1))
        # 检查文章中出现的 export XXX 和 XXX=yyy 格式
        article_vars = set()
        for m in re.finditer(r"export\s+(\w+)", article):
            article_vars.add(m.group(1))
        for m in re.finditer(r"^(\w+)=\S+", article, re.MULTILINE):
            var = m.group(1)
            if var.isupper() or var.startswith(("OPENAI_", "AGNES_", "PSE_", "CRM_")):
                article_vars.add(var)
        for var in sorted(article_vars):
            if var in real_vars:
                verified.append(f"{var} (环境变量存在于 .env.example)")
            else:
                fictitious.append(f"[环境变量] {var} 不在 .env.example 中，可能是虚构的")

    # ── 安装命令核查 ──
    # 检查 pip install <pkg> 是否匹配 pyproject.toml 中的 name
    pyproject = source_dir / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8")
        m = re.search(r'name\s*=\s*"([^"]+)"', content)
        if m:
            real_pkg_name = m.group(1)
            # 检查文章中的 pip install 命令
            for pip_match in re.finditer(r"pip\s+install\s+(\S+)", article):
                claimed_pkg = pip_match.group(1)
                if claimed_pkg == real_pkg_name:
                    verified.append(f"pip install {claimed_pkg} (匹配 pyproject.toml)")
                else:
                    fictitious.append(
                        f"[安装] pip install {claimed_pkg} 与 pyproject.toml name={real_pkg_name} 不匹配"
                    )
        # 检查是否应该用 uv sync 而非 pip install
        if (source_dir / "uv.lock").exists() and "pip install" in article:
            fictitious.append("[安装] 项目使用 uv 管理（存在 uv.lock），文章中不应出现 pip install，应改为 uv sync")

    # ── CLI 入口点核查 ──
    # 检查 python -m <module> 是否有对应的 __main__.py
    for m in re.finditer(r"python\s+-m\s+(\S+)", article):
        module_path = m.group(1).replace(".", "/")
        main_file = source_dir / "src" / module_path / "__main__.py"
        if main_file.exists():
            verified.append(f"python -m {m.group(1)} (入口点存在)")
        else:
            # 也检查 tasks/ 下的 run.py
            alt_file = source_dir / "tasks" / module_path / "run.py"
            if not alt_file.exists():
                fictitious.append(f"[CLI] python -m {m.group(1)} 入口点不存在，无 __main__.py")

    # 描述夸大检查
    for keyword, reason in EXAGGERATED_TERMS.items():
        if keyword in article:
            fictitious.append(f"[夸大] {keyword} — {reason}")

    return fictitious, verified


# 夸大词汇表：关键词 → 说明（用于程序化兜底清理）
EXAGGERATED_TERMS = {
    "断点恢复": "Trace 只用于审计和提取结论，不支持从断点恢复执行",
    "上下文压缩": "代码中无上下文压缩/摘要机制",
}


def _strip_exaggerated(text: str) -> str:
    """程序化删除包含夸大词汇的句子（按句号/换行分割）。"""
    for keyword in EXAGGERATED_TERMS:
        # 按句子边界（中文句号、换行、分号）逐句清理
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            if keyword in line:
                # 尝试只删除包含关键词的子句（按中文标点分割）
                parts = re.split(r'([。；;])', line)
                filtered = []
                for i in range(0, len(parts) - 1, 2):
                    sentence = parts[i]
                    punct = parts[i + 1] if i + 1 < len(parts) else ""
                    if keyword not in sentence:
                        filtered.append(sentence + punct)
                # 处理最后一段（无标点结尾）
                if len(parts) % 2 == 1 and parts[-1]:
                    if keyword not in parts[-1]:
                        filtered.append(parts[-1])
                cleaned_line = "".join(filtered).strip()
                if cleaned_line:
                    cleaned.append(cleaned_line)
                # 如果整行都是关于该夸大词的，直接跳过
            else:
                cleaned.append(line)
        text = "\n".join(cleaned)
    return text


def _strip_fictional_refs(text: str, refs: list[str]) -> str:
    """程序化删除仍存在的虚构代码引用，保证通过 _verify_article。

    代码块：删除含引用的整行（含 def/class 行与调用行）。
    正文：按中英文句/子句边界删除含引用的子句，尽量保留其余内容。
    这是 LLM 自动修正失败后的确定性兜底，避免丢弃已生成的整篇文章。
    """
    if not refs:
        return text
    ref_set = set(refs)

    def _clean_code_block(block: str) -> str:
        lines = block.split("\n")
        kept = [ln for ln in lines if not any(r in ln for r in ref_set)]
        return "\n".join(kept)

    segments = re.split(r"(```[\s\S]*?```)", text)
    out = []
    for seg in segments:
        if seg.startswith("```") and seg.endswith("```"):
            cleaned = _clean_code_block(seg)
            # 若代码块内容被清空（只剩围栏），丢弃整块
            inner = cleaned.strip().strip("`").strip()
            if not inner:
                continue
            out.append(cleaned)
        else:
            lines = seg.split("\n")
            cleaned_lines = []
            for line in lines:
                if not any(r in line for r in ref_set):
                    cleaned_lines.append(line)
                    continue
                # 含引用：按子句拆分，仅删含引用的子句
                parts = re.split(r"([。；;！？!?])", line)
                filtered = []
                for i in range(0, len(parts) - 1, 2):
                    clause, punct = parts[i], parts[i + 1]
                    if not any(r in clause for r in ref_set):
                        filtered.append(clause + punct)
                if len(parts) % 2 == 1 and parts[-1] and not any(r in parts[-1] for r in ref_set):
                    filtered.append(parts[-1])
                kept = "".join(filtered).strip()
                if kept:
                    cleaned_lines.append(kept)
            out.append("\n".join(cleaned_lines))
    return "".join(out)


def _parse_batches(outline: str, source_dir: Path) -> list[dict]:
    """从 Planner 提纲中解析文件分批。

    返回 [{"files": ["path1", "path2"], "sections": "章节描述"}, ...]
    如果未找到分批信息，回退为单批次（取提纲中提到的所有文件）。
    """
    batches = []
    in_batch_section = False

    for line in outline.split("\n"):
        stripped = line.strip()
        if "文件分批" in stripped:
            in_batch_section = True
            continue
        if in_batch_section and stripped.startswith("###"):
            break  # 到了下一个 section
        if in_batch_section and re.match(r"[-*]?\s*批次\s*\d+", stripped):
            # 解析 "批次 1: file1, file2 → 对应章节: xxx"
            match = re.search(r"[:：]\s*(.+?)(?:\s*(?:→|->|>)+\s*对应章节[:：]\s*(.+))?$", stripped)
            if match:
                files_str = match.group(1).strip()
                sections = (match.group(2) or "").strip()
                files = [f.strip() for f in re.split(r"[,，、\s]+", files_str) if f.strip()]
                # 过滤出实际存在的文件
                existing = []
                for f in files:
                    if (source_dir / f).exists() or any(source_dir.rglob(f)):
                        existing.append(f)
                if existing:
                    batches.append({"files": existing, "sections": sections})

    if batches:
        return batches

    # 回退：从提纲中提取所有提到的文件路径，作为单批次
    all_files = set()
    for match in re.finditer(r"[\w/]+\.py", outline):
        f = match.group()
        if (source_dir / f).exists() or any(source_dir.rglob(f)):
            all_files.add(f)
    if all_files:
        file_list = sorted(all_files)
        # 按 5 个一组分批
        return [
            {"files": file_list[i:i + 5], "sections": ""}
            for i in range(0, len(file_list), 5)
        ]
    return [{"files": [], "sections": ""}]


def _do_translate(
    article: str,
    slug: str,
    client,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    do_publish: bool,
    project_key: str = "",
):
    """翻译英文 + 统计 token + 可选发布。"""
    # 翻译英文
    print("🌐 翻译英文版...")
    translate_prompt = (
        "Translate the following Chinese technical article to English. "
        "Keep ALL code examples, file paths, class names, function names, and the "
        "YAML front matter structure unchanged. Translate the `title` field, but change "
        "the `slug` field to end with `-en` (e.g. `foo` -> `foo-en`), never `-zh`. "
        f"Output ONLY the translated article:\n\n{article}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": translate_prompt}],
            max_tokens=8192,
            temperature=0.3,
        )
        raw_en = resp.choices[0].message.content or ""
        en_content = _set_frontmatter_tags(
            _fix_frontmatter_slug(
                _strip_outer_fence(_clean_code_block_whitespace(raw_en)), "-en"
            ),
            STANDARD_TAGS_EN,
        )
        t_usage = resp.usage
        if t_usage:
            prompt_tokens += t_usage.prompt_tokens
            completion_tokens += t_usage.completion_tokens
            print(f"📊 翻译: {t_usage.prompt_tokens} 输入 + {t_usage.completion_tokens} 输出")
        en_path = ARTICLES_DIR / "en" / f"{slug}.md"
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(en_content, encoding="utf-8")
        print(f"✅ 英文已保存 → {en_path}")
    except Exception as e:
        print(f"⚠️ 翻译失败（跳过）: {e}")

    # Token 统计
    total = prompt_tokens + completion_tokens
    print(f"\n📊 Token 总消耗: {prompt_tokens} 输入 + {completion_tokens} 输出 = {total} 合计")
    if total:
        print(f"💰 预估费用: ¥{total * 0.0000014:.4f}")

    # 发布到 WordPress
    if do_publish and project_key:
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


def main():
    projects = _load_projects()
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_publish = "--publish" in flags
    do_translate_only = "--translate" in flags

    if not args or args[0] not in projects:
        print("用法: python run.py <项目名> [--publish] [--translate]")
        print(f"  --publish    生成后自动发布到 WordPress")
        print(f"  --translate  仅翻译已有的中文文章（跳过 CrewAI 生成）")
        print(f"可用项目: {', '.join(projects.keys())}")
        sys.exit(1)

    project_key = args[0]
    p = projects[project_key]
    source_dir = ROOT / p["source_dir"]
    if not source_dir.exists():
        print(f"❌ 源码目录不存在: {source_dir}")
        print("请检查 projects.json 中该项目的 source_dir，或 PSE_ROOT 环境变量是否指向仓库根。")
        sys.exit(1)

    # 镜像目标项目源码进沙箱缓存：read_file 沙箱限定在 crewai-pse 仓库根，
    # 无法直接读取 frameworks/langgraph-pse 等外部目录。镜像后 LLM 经 read_file
    # 读取缓存目录（即项目仓库根），nav 链接相对路径仍正确。
    sandbox_dir = BASE / ".src_cache" / project_key
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir)
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    _src_mirror(source_dir, sandbox_dir)
    print(f"📂 已镜像源码到沙箱: {sandbox_dir}")
    slug = project_key.replace("-", "_")
    slug_zh = f"{slug}-zh"
    slug_en = f"{slug}-en"

    from openai import OpenAI
    fix_client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
    )
    fix_model = settings.OPENAI_MODEL.replace("openai/", "")

    # --translate 模式：直接读取已有中文文章进行翻译
    if do_translate_only:
        zh_path = ARTICLES_DIR / "zh" / f"{slug_zh}.md"
        if not zh_path.exists():
            # 兼容旧文件名（无 -zh 后缀）
            legacy_path = ARTICLES_DIR / "zh" / f"{slug}.md"
            if legacy_path.exists():
                zh_path = legacy_path
            else:
                print(f"❌ 中文文章不存在: {zh_path}")
                sys.exit(1)
        article = zh_path.read_text(encoding="utf-8")
        print(f"📖 已读取中文文章: {zh_path} ({len(article)} 字)")
        prompt_tokens = 0
        completion_tokens = 0
        # 跳到翻译步骤
        _do_translate(article, slug, fix_client, fix_model,
                       prompt_tokens, completion_tokens, do_publish)
        return

    crew = create_crew(task="project-articles")

    # ── Phase 1: Planner 生成提纲 ──
    planner_task = Task(
        description=f"""撰写 {p['desc']}（{project_key}）的中文技术文章。

## 项目信息
- GitHub: {p['repo']}
- 核心卖点: {p['highlights']}
- 源码目录: {sandbox_dir}（这是该项目的仓库根目录，仅供 read_file 读取；文章中引用文件路径请用相对此目录的路径，如 `src/langgraph_pse/graph.py`，不要暴露此目录本身，也不要带 frameworks/ 前缀）

## 你的任务
1. 用 read_file 读取源码目录下的关键文件（README.md + 核心 .py 文件）
2. 分析项目特点，从 5 种叙事风格中选择最合适的一种（问题驱动/设计决策/实战场景/架构漫游/对比分析）
3. 基于源码提炼 2-3 个非显而易见的亮点，按选定风格组织提纲
4. 将推荐文件按主题相关性分成若干批次（每批不超过 5 个），标注每批对应的章节
5. 提纲末尾附上"交付完成"
""",
        expected_output="文章结构提纲（含叙事风格选择、文件分批）",
        agent=crew.agents[0],
    )

    print(f"🚀 Phase 1: Planner 规划 {p['desc']} 的文章提纲...")
    crew.tasks = [planner_task]
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        planner_output = crew.kickoff()
    except RuntimeError as e:
        if "no running event loop" in str(e):
            planner_output = asyncio.run(crew.kickoff_async())
        else:
            raise

    outline = planner_output.tasks_output[0].raw if planner_output.tasks_output else ""
    if not outline:
        print("❌ Planner 未输出提纲")
        sys.exit(1)
    print(f"✅ 提纲已完成 ({len(outline)} 字)")

    # 解析文件分批
    batches = _parse_batches(outline, source_dir)
    batch_summary = ", ".join(f"{len(b['files'])}个文件" for b in batches)
    print(f"📦 文件分 {len(batches)} 批: {batch_summary}")

    # ── Phase 2: 分批写作 + 合并 ──
    # 临时目录必须建在仓库根内：read_file 沙箱限定 _PROJECT_ROOT（Makefile 从 crewai-pse 根运行 → cwd=根），
    # 若用系统临时目录 /var/folders/... 会触发「路径超出项目范围」被拦截，导致提纲/草稿读不到、文章写不出。
    crewai_root = Path(__file__).resolve().parent.parent.parent
    tmpdir = crewai_root / ".pse_tmp" / f"run_{os.getpid()}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    try:
        crew2 = create_crew(task="project-articles")
        # 把提纲写到临时文件，避免内联在 task description 中撑大上下文
        outline_path = os.path.join(tmpdir, "outline.md")
        with open(outline_path, "w", encoding="utf-8") as f:
            f.write(outline)

        batch_tasks = []

        for i, batch in enumerate(batches):
            files_str = ", ".join(batch["files"])
            sections_str = batch.get("sections", "")
            draft_path = os.path.join(tmpdir, f"section_{i}.md")

            bt = Task(
                description=f"""读源码，写文章草稿。

读这些文件（用 read_file，不要读更多）: {files_str}
源码目录（即项目仓库根）: {sandbox_dir}
对应章节: {sections_str or '根据提纲判断'}
提纲: {outline_path}（用 read_file 读取）

写出本批次章节草稿，保存到: {draft_path}
代码必须来自实际源码。
""",
                expected_output="章节草稿（已保存到文件）",
                agent=crew2.agents[1],
            )
            batch_tasks.append(bt)

        # 合并任务
        draft_files = ", ".join(
            os.path.join(tmpdir, f"section_{i}.md")
            for i in range(len(batches))
        )
        merge_task = Task(
            description=f"""合并草稿为完整文章。

用 read_file 读取以下文件:
- 提纲: {outline_path}
- 草稿: {draft_files}

合并要求:
1. 按提纲结构合并为一篇连贯文章
2. 消除重复，确保过渡自然
3. 添加 Front Matter (title, date: {date.today().isoformat()}, slug 基于标题生成（纯小写连字符，不加语言后缀）, categories: ["AI"]；tags 字段稍后由程序统一设置，可留空或写占位)
4. 添加源码导航表格 (https://github.com/{p['repo']}/blob/main/<path>)
5. 正文不以 H1 开头
6. 不提及本地路径等内部信息
7. 输出完整最终文章
""",
            expected_output="完整的中文 Markdown 技术文章",
            agent=crew2.agents[1],
        )

        all_tasks = batch_tasks + [merge_task]
        print(f"\n🚀 Phase 2: {len(batches)} 批写作 + 合并...")
        crew2.tasks = all_tasks
        try:
            writing_output = crew2.kickoff()
        except RuntimeError as e:
            if "no running event loop" in str(e):
                writing_output = asyncio.run(crew2.kickoff_async())
            else:
                raise

        # 从合并任务中提取最终文章
        article = writing_output.tasks_output[-1].raw if writing_output.tasks_output else ""
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not article:
        print("❌ Specialist 未输出任何内容")
        sys.exit(1)

    # 从 CrewAI 获取主体流程的 token 用量（Phase 1 + Phase 2）
    prompt_tokens = 0
    completion_tokens = 0
    for c in (crew, crew2):
        usage = getattr(c, "usage_metrics", None)
        if usage:
            prompt_tokens += usage.prompt_tokens
            completion_tokens += usage.completion_tokens
    print(f"📊 CrewAI 主体: {prompt_tokens} 输入 + {completion_tokens} 输出")

    # 程序化验证 + 自动修正
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        fictitious, verified = _verify_article(article, source_dir)
        print(f"\n{'='*60}")
        print(f"  核查 (第{attempt}次) — 已验证 {len(verified)} 项")
        if fictitious:
            # 分离代码引用和夸大词
            code_refs = [f for f in fictitious if not f.startswith("[夸大]")]
            exaggerations = [f for f in fictitious if f.startswith("[夸大]")]

            print(f"  ❌ 虚构内容 {len(fictitious)} 项: {', '.join(fictitious)}")
            if attempt < max_retries:
                print("  🔄 自动修正中...")
                fix_parts = []
                if code_refs:
                    fix_parts.append(f"**虚构代码引用（在源码中不存在，必须删除）**: {', '.join(code_refs)}")
                if exaggerations:
                    exagg_words = [f.split("—")[0].replace("[夸大]", "").strip() for f in exaggerations]
                    fix_parts.append(f"**禁止使用的夸大词汇（必须从文章中彻底删除这些词）**: {', '.join(exagg_words)}")

                fix_prompt = f"""以下文章被核查发现问题，请修正。

{'\n'.join(fix_parts)}

**规则**:
1. 虚构代码引用：删除包含该引用的句子或代码示例，不要创造性替换
2. 夸大词汇：删除包含该词汇的整句话，不要尝试改写
3. 保持文章其余部分不变
4. 输出修正后的完整文章（从 Front Matter 开始），不输出解释

## 当前文章
{article}"""
                try:
                    resp = fix_client.chat.completions.create(
                        model=fix_model,
                        messages=[{"role": "user", "content": fix_prompt}],
                        max_tokens=8192,
                        temperature=0.3,
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
                # 兜底：程序化删除顽固的虚构代码引用 + 夸大词（确定性，不依赖 LLM）
                article = _strip_exaggerated(article)
                article = _strip_fictional_refs(article, code_refs)
                fictitious, verified = _verify_article(article, source_dir)
                code_refs = [f for f in fictitious if not f.startswith("[夸大]")]
                if code_refs:
                    print(f"\n⚠️ 程序化兜底后仍残留虚构代码引用: {', '.join(code_refs)}")
                    print("⚠️ 已保存文章，但需人工复核并删除上述引用（未自动中断）")
                else:
                    print(f"  ✅ 程序化兜底清理完成，剩余 {len(verified)} 项已验证")
        else:
            print("  ✅ 无虚构内容，全部通过")
            break

    # 保存中文
    zh_path = ARTICLES_DIR / "zh" / f"{slug_zh}.md"
    zh_path.parent.mkdir(parents=True, exist_ok=True)
    article = _set_frontmatter_tags(
        _fix_frontmatter_slug(_strip_outer_fence(_clean_code_block_whitespace(article)), ""),
        STANDARD_TAGS_ZH,
    )
    zh_path.write_text(article, encoding="utf-8")
    print(f"\n✅ 中文已保存 → {zh_path}")

    # 翻译 + 统计 + 发布
    _do_translate(article, slug_en, fix_client, fix_model,
                  prompt_tokens, completion_tokens, do_publish, project_key)


if __name__ == "__main__":
    main()
