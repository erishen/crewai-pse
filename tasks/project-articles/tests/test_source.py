"""src_mirror 排除规则 + parse_batches 的单测。

锁死本轮回对 source.py 的两处优化：
1. src_mirror 排除 build/out/dist/.next/models 等目录、>5MiB 大文件、符号链接
   （此前 firefly-studio 把 141M ggml-base.bin 等复制进 .src_cache 撑到 1.3G）
2. parse_batches 预计算一次文件清单，避免每文件全树 rglob
"""
import tempfile
from pathlib import Path

from pipeline import source


def _w(p: Path, content: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_src_mirror_excludes():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "proj"
        dst = Path(td) / "cache"
        _w(src / "src" / "main.py", "print(1)")
        _w(src / "build" / "x.o", "obj")
        _w(src / "dist" / "app.js", "bundled")
        _w(src / "models" / "big.bin", "x" * (6 * 1024 * 1024))  # 6MB > 5MiB 上限
        _w(src / ".env", "SECRET=1")
        _w(src / "node_modules" / "pkg" / "i.js", "1")
        (src / "link_dir").symlink_to(src / "src", target_is_directory=True)

        source.src_mirror(src, dst)

        copied = sorted(str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file())
        assert "src/main.py" in copied
        assert not any(c.startswith("build") for c in copied)
        assert not any(c.startswith("dist") for c in copied)
        assert not any(c.startswith("models") for c in copied)
        assert not any(c.startswith("node_modules") for c in copied)
        assert ".env" not in copied
        assert not any("link_dir" in c for c in copied)


def test_parse_batches():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "a.py", "x=1")
        _w(sd / "src" / "b.ts", "y=2")
        _w(sd / "docs" / "c.md", "# t")
        outline = (
            "## 文件分批\n"
            "批次 1: src/a.py, src/b.ts → 对应章节: 出发点\n"
            "批次 2: docs/c.md → 对应章节: 结果\n"
            "### 其他\n普通段落提到 src/a.py\n"
        )
        res = source.parse_batches(outline, sd)
        files = [f for b in res for f in b["files"]]
        assert "src/a.py" in files
        assert "src/b.ts" in files
        assert "docs/c.md" in files
        assert any("出发点" in b["sections"] for b in res)


def test_parse_batches_salvages_leaked_tool_call_path():
    """Planner 把 read_file 当正文吐回时（原生 token 形态），path 参数是唯一线索。

    nsbp-lume 2026-11-14 的翻车现场：提纲正文只有一段 `<｜｜ calls>`，
    旧回退正则只认带扩展名的文件、且不读 parameter 里的路径，于是证据池为空。
    """
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "Makefile", "build:\n\tuv build\n")
        _w(sd / "src" / "main.py", "x=1")
        outline = (
            "# 规划提纲\n\n"
            "<｜｜ calls>\n"
            '<｜｜ invoke name="read_file">\n'
            f'<｜｜ parameter name="path" string="true">{sd}/Makefile</｜｜ parameter>\n'
            "</｜｜ invoke>\n"
            "</｜｜ calls>\n"
        )
        res = source.parse_batches(outline, sd)
        files = [f for b in res for f in b["files"]]
        assert "Makefile" in files, "泄漏的 path 参数必须能被抢救成证据池"


def test_parse_batches_resolves_sandbox_prefixed_path():
    """模型照抄沙箱路径（`.src_cache/<project>/src/x.ts`）时，靠从右向左裁剪命中真实文件。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "x.ts", "export const x = 1")
        outline = "证据见 .src_cache/proj/src/x.ts 的入口实现。\n"
        res = source.parse_batches(outline, sd)
        files = [f for b in res for f in b["files"]]
        assert "src/x.ts" in files


def test_parse_batches_counts_extensionless_files():
    """Makefile / Dockerfile 这类无扩展名实现文件也要进证据池。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "Makefile", "dev:\n\tnpm run dev\n")
        _w(sd / "Dockerfile", "FROM node:24\n")
        outline = "先看 Makefile 的 dev target，再看 Dockerfile 的构建阶段。\n"
        res = source.parse_batches(outline, sd)
        files = [f for b in res for f in b["files"]]
        assert "Makefile" in files
        assert "Dockerfile" in files


def test_parse_batches_absolute_path_filtered():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "a.py", "x=1")
        outline = f"批次 1: {sd / 'src' / 'a.py'}, nope.py → 对应章节: x\n"
        res = source.parse_batches(outline, sd)
        files = [f for b in res for f in b["files"]]
        assert "src/a.py" in files
        assert not any("nope.py" in f for f in files)


def test_parse_batches_fallback_single_batch():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "a.py", "x=1")
        _w(sd / "src" / "b.py", "y=2")
        outline = "没有任何分批信息的提纲，提到 src/a.py 和 src/b.py\n"
        res = source.parse_batches(outline, sd)
        files = [f for b in res for f in b["files"]]
        assert "src/a.py" in files
        assert "src/b.py" in files
