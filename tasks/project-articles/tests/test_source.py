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
