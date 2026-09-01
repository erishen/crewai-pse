import sys
from pathlib import Path

# 双保险：确保 pipeline 包在 pytest（pythonpath=.）与 `python -m pytest` 两种调用下都可导入
sys.path.insert(0, str(Path(__file__).parent))
