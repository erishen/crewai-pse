"""源码核查（安装命令 / CLI 入口点）的单测。

verify._check_* 返回 (虚构列表, 已验证列表)，纯函数、给定 source_dir 即可。
"""
import tempfile
from pathlib import Path

from pipeline import verify


def _w(p: Path, content: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_check_install_commands_mismatch():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "pyproject.toml", '[project]\nname = "real-pkg"\n')
        bad, good = verify._check_install_commands("运行 pip install wrong-pkg 安装", sd)
        assert any("不匹配" in b for b in bad)
        assert good == []


def test_check_install_commands_match_good():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "pyproject.toml", '[project]\nname = "real-pkg"\n')
        bad, good = verify._check_install_commands("运行 pip install real-pkg 安装", sd)
        assert bad == []
        assert any("real-pkg" in g for g in good)


def test_check_install_commands_uv_project():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "pyproject.toml", '[project]\nname = "p"\n')
        _w(sd / "uv.lock", "")
        bad, good = verify._check_install_commands("使用 pip install p 安装", sd)
        assert any("uv" in b for b in bad)


def test_check_cli_entrypoints_missing():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        bad, good = verify._check_cli_entrypoints("运行 python -m mymod 启动", sd)
        assert any("入口点不存在" in b for b in bad)


def test_check_cli_entrypoints_ok():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "mymod" / "__main__.py", "print(1)")
        bad, good = verify._check_cli_entrypoints("运行 python -m mymod 启动", sd)
        assert bad == []
        assert any("入口点存在" in g for g in good)
