"""启动入口：python run.py 或 python -m cnki_paper_api.run（从上级目录）。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许从任意目录以脚本方式启动
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from cnki_paper_api import config  # noqa: E402


def main() -> None:
    uvicorn.run(
        "cnki_paper_api.api.main:app",
        host=config.HOST,
        port=config.PORT,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
