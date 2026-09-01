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

        LLM 生成的提纲有时会写出绝对路径（如 /Users/erishen/.../x.md），
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
                    rel = _ref_rel(f)
                    if rel is None:
                        continue
                    if (source_dir / rel).exists() or _exists(rel):
                        existing.append(rel)
                if existing:
                    batches.append({"files": existing, "sections": sections})

    if batches:
        return batches

    # 回退：从提纲中提取所有提到的文件路径，作为单批次
    all_files = set()
    for match in re.finditer(r"[\w/]+\.(?:py|ts|tsx|js|jsx|md|rs|toml|go|java|cpp|rb|sql|json|ya?ml)", outline):
        f = match.group()
        rel = _ref_rel(f)
        if rel is None:
            continue
        if (source_dir / rel).exists() or _exists(rel):
            all_files.add(rel)
    if all_files:
        file_list = sorted(all_files)
        # 按 5 个一组分批
        return [
            {"files": file_list[i:i + 5], "sections": ""}
            for i in range(0, len(file_list), 5)
        ]
    return [{"files": [], "sections": ""}]


def load_projects() -> dict:
    """加载待写队列（projects.json）与已发布清单（projects-published.json）合并后的项目表。"""
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

    # schema 校验
    required_keys = {"repo", "desc", "highlights", "source_dir"}
    for name, cfg in projects.items():
        missing = required_keys - set(cfg.keys())
        if missing:
            print(f"❌ [{name}] 缺少字段: {', '.join(missing)}")
            sys.exit(1)
    return projects
