"""源码接入层：把目标项目搬进沙箱、解析提纲里的文件分批、加载项目清单。

read_file 工具受限于 crewai-pse 的仓库根沙箱，无法直接读 frameworks/langgraph-pse
这类外部目录，所以需要 `_src_mirror` 把真实源码复制进缓存目录，让 LLM 经
read_file 读到（且缓存目录即"项目仓库根"，nav 链接相对路径保持正确）。
"""

import json
import re
import shutil
import sys
from pathlib import Path

from pipeline.config import PROJECTS_FILE, PUBLISHED_FILE

# 镜像时要排除的目录 / 文件名（依赖、密钥、构建产物、模型权重，既无信息量又可能泄密/撑爆缓存）
EXCLUDE_DIRS = {
    ".venv", "__pycache__", ".git", "node_modules", ".src_cache",
    ".idea", ".vscode", "target",               # 通用构建/IDE
    "build", "out", "dist", ".next", ".nuxt",   # 编译/打包产物（非可读源码）
    "vendor", ".terraform", ".cache",           # 第三方/派生缓存
}
EXCLUDE_NAMES = {".env", ".env.example", ".env.local", ".DS_Store"}
# 单文件体积上限：超过则跳过（典型为 ggml/onnx/tar 等模型权重与压缩包，对写文章无意义且撑爆缓存）
MAX_MIRROR_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB


def src_mirror(src: Path, dst: Path) -> None:
    """把目标项目源码镜像进沙箱内目录，供 LLM 经 read_file 读取。

    read_file 沙箱限定在 crewai-pse 仓库根（见 crewai_pse/tools.py 的
    _PROJECT_ROOT），无法直接读取 frameworks/langgraph-pse 等外部目录。
    这里把真实源码复制进 crewai-pse 内的缓存目录，使 LLM 仍能经 read_file
    读取目标项目；dst 即"项目仓库根"，nav 链接的相对路径因此保持正确。

    任何超过 MAX_MIRROR_FILE_BYTES 的文件（模型权重、压缩包等）与符号链接一律跳过，
    避免把构建产物/大二进制复制进缓存目录（否则 .src_cache 可膨胀至 GB 级）。
    """
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.name in EXCLUDE_NAMES:
            continue
        if path.is_symlink():
            continue
        if path.is_file():
            if path.stat().st_size > MAX_MIRROR_FILE_BYTES:
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def parse_batches(outline: str, source_dir: Path) -> list[dict]:
    """从 Planner 提纲中解析文件分批。

    返回 [{"files": ["path1", "path2"], "sections": "章节描述"}, ...]
    如果未找到分批信息，回退为单批次（取提纲中提到的所有文件）。
    """
    batches = []
    in_batch_section = False

    # 预计算 source_dir 内全部文件清单（一次建树），后续匹配均在内存中完成，
    # 避免对每个引用都做一次全树 rglob（O(文件数 × 树深) → O(文件数)）。
    all_paths = [p for p in source_dir.rglob("*") if p.is_file()]

    def _exists(rel: str) -> bool:
        if not rel:
            return False
        if (source_dir / rel).exists():
            return True
        # 回退：提纲里可能写的是 glob 片段（如 "src/**/x.py"），在预计算清单里匹配，
        # 语义与原来的 source_dir.rglob(rel) 一致，但只扫一次树。
        return any(p.match(rel) for p in all_paths)

    def _ref_rel(f: str):
        """把提纲里的文件引用归一化为可用于 rglob 的相对字符串。

        LLM 生成的提纲有时会写出绝对路径（根路径由环境配置提供，形如 $HOME/proj/x.md），
        直接传给 Path.rglob 会触发 NotImplementedError: Non-relative patterns
        are unsupported。这里把绝对路径收敛到 source_dir 内的相对部分；
        若绝对路径不在 source_dir 内则返回 None（无法匹配，跳过）。
        """
        p = Path(f)
        if p.is_absolute():
            try:
                p = p.relative_to(source_dir)
            except ValueError:
                return None
        return str(p)

    def _resolve_ref(f: str) -> str | None:
        """把任意形态的文件引用收敛成 source_dir 内的相对路径。

        依次尝试：
        1. 原样相对路径（`src/a.py`）；
        2. 绝对路径相对化（落在 source_dir 内）；
        3. **从右向左逐段裁剪**——覆盖提纲里常见的 `frameworks/<x>.py` 前缀，
           以及模型照抄沙箱路径的情况（`.src_cache/<project>/Makefile`）。
        都命中不了返回 None（该引用无法核对，跳过）。
        """
        raw = (f or "").strip().strip("\"'`")
        if not raw:
            return None
        rel = _ref_rel(raw)
        if rel is not None and ((source_dir / rel).exists() or _exists(rel)):
            return rel
        parts = Path(raw).parts
        for i in range(1, len(parts)):
            cand = str(Path(*parts[i:]))
            if (source_dir / cand).exists() or _exists(cand):
                return cand
        return None

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
                    rel = _resolve_ref(f)
                    if rel is None:
                        continue
                    if rel not in existing:
                        existing.append(rel)
                if existing:
                    batches.append({"files": existing, "sections": sections})

    if batches:
        return batches

    # 回退：从提纲中提取所有提到的文件路径，作为单批次
    all_files = set()
    for match in re.finditer(r"[\w/]+\.(?:py|ts|tsx|js|jsx|md|rs|toml|go|java|cpp|rb|sql|json|ya?ml)", outline):
        rel = _resolve_ref(match.group())
        if rel:
            all_files.add(rel)
    # 无扩展名的实现文件（Makefile / Dockerfile…）同样是证据主体，单独补一条
    for match in re.finditer(r"\b(Makefile|Dockerfile(?:\.\w+)?|justfile|Procfile)\b", outline):
        rel = _resolve_ref(match.group(1))
        if rel:
            all_files.add(rel)
    # 抢救：Planner 把 read_file 工具调用当正文吐回时（`<｜｜ parameter name="path">…`），
    # path 参数是它对源码唯一的真实指向——不提取的话证据池会是空的。
    for match in re.finditer(r'parameter\s+name="path"[^>]*>([^<]+)', outline):
        rel = _resolve_ref(match.group(1))
        if rel:
            all_files.add(rel)
    if all_files:
        file_list = sorted(all_files)
        # 按 5 个一组分批
        return [
            {"files": file_list[i:i + 5], "sections": ""}
            for i in range(0, len(file_list), 5)
        ]
    return [{"files": [], "sections": ""}]


# ── 源码地图：让 Planner「看得见源码」但**不持有任何工具** ─────────────────────
# 起因：Planner 挂了 read_file（甚至 run_bash），而部分 provider 会把工具调用原样
# 写进 content、调用根本不执行——nsbp-lume 2026-11-14 就是靠一条未执行的
# `<｜｜ invoke name="read_file">` 编排了整篇提纲，证据池为空。
# 改为程序读文件、只把「目录结构 + 文件头 + 真实符号行号」交给模型：证据链的行号
# 由程序计算，模型不再有机会把工具调用当正文吐出来。

# 目录概览/文件枚举时跳过的目录（构建产物与依赖树，对规划无价值）
_MAP_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__",
    ".next", "dist", "build", "out", "target", "models", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".src_cache", ".backup", ".idea", ".vscode", "vendor",
}

# 规划时信息密度最高的文件：优先出现在地图里
_MAP_SIGNAL_FILES = (
    "Makefile", "docker-compose.yml", "docker-compose.yaml", "pyproject.toml",
    "package.json", "Cargo.toml", "go.mod", "requirements.txt",
)

_MAP_CODE_EXT = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".rb", ".php", ".cs", ".swift", ".vue", ".svelte",
    ".sql", ".sh", ".lua", ".dart", ".scala", ".ex", ".exs",
}

# 符号行：类/函数/顶层常量声明。抓到的是**真实行号**，可直接作为锚点候选。
_MAP_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:"
    r"def\s+\w"
    r"|class\s+\w"
    r"|function\s+\w+\s*\("
    r"|fn\s+\w"
    r"|func\s+\w"
    r"|impl\b"
    r"|struct\s+\w"
    r"|enum\s+\w"
    r"|interface\s+\w"
    r"|type\s+\w"          # TS 的 `type X = …`；必须带名字，否则 `type: h[1],` 这类对象字段会混进来
    r"|const\s+\w"
    r"|let\s+\w"
    r"|var\s+\w"
    r"|module\.exports\b"
    r")"
)

# 常/变量声明：只作补位，不能挤掉真正的类型与函数声明
_MAP_SYMBOL_MINOR_RE = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\b")


def _iter_map_files(root: Path) -> list[Path]:
    """枚举可进地图的文件（相对路径、已剔除依赖/产物目录）。"""
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _MAP_SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        out.append(rel)
    return out


def _map_symbols(path: Path, limit: int = 10) -> list[str]:
    """抽取文件的符号声明行（含真实行号），失败则返回空。

    优先收录类型/函数级声明（class/def/function/impl…），常量只用来补位——
    否则一个文件前几行全是 `const XXX = 1`，真正的实现函数就被挤出地图了。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    major: list[str] = []
    minor: list[str] = []
    for no, line in enumerate(text.splitlines(), start=1):
        if len(line) > 200 or not _MAP_SYMBOL_RE.match(line):
            continue
        rendered = f"L{no} `{line.strip().rstrip(':{').strip()[:110]}`"
        if _MAP_SYMBOL_MINOR_RE.match(line):
            minor.append(rendered)
        else:
            major.append(rendered)
    return (major + minor)[:limit]


def _map_head(path: Path, head_lines: int = 6) -> str:
    """文件开头若干行（判断文件用途的抓手），超长行截断。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    lines = text.splitlines()[:head_lines]
    return "\n".join(ln[:120] for ln in lines)


def build_source_map(root: Path, max_files: int = 30, head_lines: int = 6,
                     max_chars: int = 14000) -> str:
    """生成喂给 Planner 的「源码地图」：目录概览 + README 摘要 + 文件头与符号行号。

    为什么要这个：Planner 一旦持有 read_file/run_bash，遇到不支持原生工具调用的
    provider 就会把 tool call 当正文吐回来（调用未执行 → 证据池为空 → Writer 在
    零素材上写作 → 必然被论断—源码一致性闸门拒绝）。改成程序读文件后，模型看到的
    每一行都真实存在，连行号都是程序算出来的，不会再出现自编路径/自编行号。
    """
    files = _iter_map_files(root)

    # 目录概览：顶层目录的文件计数，给模型一个整体结构感
    top_counts: dict[str, int] = {}
    for rel in files:
        top = rel.parts[0] if len(rel.parts) > 1 else "(根目录)"
        top_counts[top] = top_counts.get(top, 0) + 1

    parts: list[str] = ["## 源码地图（由程序读取真实文件生成，你无需也不得调用任何工具）", ""]
    if top_counts:
        overview = "、".join(
            f"{k}（{v} 个文件）"
            for k, v in sorted(top_counts.items(), key=lambda kv: -kv[1])[:12]
        )
        parts.append(f"### 目录概览\n{overview}\n")

    # README 摘要：项目的自我描述，规划时的第一手材料
    for name in ("README.md", "README.zh.md", "README.zh-CN.md", "README.rst", "README.txt"):
        f = root / name
        if f.exists():
            head = _map_head(f, head_lines=40)[:1200]
            if head.strip():
                parts.append(f"### {name}（开头片段）\n```md\n{head}\n```\n")
            break

    # 待收录文件：信号文件优先 → 代码文件按体积降序（大文件通常是实现主体）
    def _rank(rel: Path) -> tuple:
        name = rel.name
        is_signal = name in _MAP_SIGNAL_FILES or name.startswith("Dockerfile")
        is_readme = name.lower().startswith("readme")
        code = rel.suffix.lower() in _MAP_CODE_EXT
        try:
            size = (root / rel).stat().st_size
        except OSError:
            size = 0
        return (0 if is_signal else 1 if code else 2 if is_readme else 3, -size, str(rel))

    picked = [rel for rel in sorted(files, key=_rank) if not rel.name.lower().startswith("readme")][:max_files]

    parts.append("### 文件清单（含真实符号与行号；提纲要引用的文件只能从这里挑）")
    used = sum(len(p) for p in parts)
    for rel in picked:
        if used >= max_chars:
            parts.append(f"…（其余 {len(files) - len(picked)} 个文件未展开）")
            break
        path = root / rel
        try:
            total = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            continue
        block = [f"#### `{rel}`（{total} 行）"]
        syms = _map_symbols(path)
        if syms:
            block.append("符号：\n" + "\n".join(f"- {s}" for s in syms))
        head = _map_head(path, head_lines=head_lines)
        if head.strip():
            block.append("开头片段：\n```\n" + head + "\n```")
        text = "\n".join(block) + "\n"
        parts.append(text)
        used += len(text)
    return "\n".join(parts)


def load_projects() -> dict:
    """加载待写队列（projects.json）与已发布清单（projects-published.json）合并后的项目表。

    只要求每个条目都有描述（desc）——那是所有条目（源码项目、方法论文章等）共有的。
    源码字段（repo / highlights / source_dir）只对**真正要生成的文章项目**有意义，
    由 run.py 在选定项目后校验，绝不能在这里对整个合并表硬校验：已发布清单里完全
    可能有纯方法论 / 无源码的条目（如 vibecoding），把源码字段放这里会拖垮所有请求。
    """
    pending = {}
    if PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, encoding="utf-8") as f:
            pending = json.load(f)
    published = {}
    if PUBLISHED_FILE.exists():
        with open(PUBLISHED_FILE, encoding="utf-8") as f:
            published = json.load(f)
    # 待写队列优先（理论上两文件不会重名）；合并后任一项目名都可被生成/重跑
    projects = {**published, **pending}
    if not projects:
        print(f"❌ 找不到项目配置文件: {PROJECTS_FILE} / {PUBLISHED_FILE}")
        print("请从 projects.json.example 复制并填写实际配置")
        sys.exit(1)

    # desc 是所有条目共有的最小编码投影；缺了它连标题/提纲都无从生成。
    for name, cfg in projects.items():
        if not isinstance(cfg, dict) or not str(cfg.get("desc", "")).strip():
            print(f"❌ [{name}] 缺少 desc（描述）字段")
            sys.exit(1)
    return projects
