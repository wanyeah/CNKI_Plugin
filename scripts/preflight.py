"""安装前/后自检：Python 版本、依赖、Playwright 浏览器、端口。

用法：
  python scripts/preflight.py pre   # 装依赖前：只查 python 版本
  python scripts/preflight.py post  # 装依赖后：查依赖、浏览器、端口
"""
from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_PY = (3, 10)


def check_python() -> bool:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PY
    print(f"  Python 版本: {v.major}.{v.minor}.{v.micro}  {'OK' if ok else '需要 >= 3.10'}")
    return ok


def check_deps() -> bool:
    mods = ["fastapi", "uvicorn", "requests", "bs4", "playwright", "pydantic", "mcp", "httpx"]
    missing = [m for m in mods if importlib.util.find_spec(m) is None]
    print(f"  Python 依赖: {'全部就绪' if not missing else '缺少 ' + ', '.join(missing)}")
    return not missing


def check_browser() -> bool:
    candidates = [
        Path.home() / "AppData" / "Local" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ]
    found = any(p.exists() and any(p.iterdir()) for p in candidates if p.exists())
    print(f"  Chromium 浏览器: {'已安装' if found else '未安装（执行 python -m playwright install chromium）'}")
    return found


def check_port() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        busy = s.connect_ex(("127.0.0.1", 8765)) == 0
    print(f"  端口 8765: {'已被占用' if busy else '空闲'}")
    return not busy


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    print("=== 环境自检 ===")
    if mode == "pre":
        ok = check_python()
        print("=== 结果:", "通过，可以安装" if ok else "未通过，请先安装 Python 3.10+ 并勾选 Add to PATH")
    else:
        r1, r2, r3, r4 = check_python(), check_deps(), check_browser(), check_port()
        ok = r1 and r2 and r3
        print("=== 结果:", "通过，双击 start.bat 即可" if ok else "部分未通过，按上面提示处理")
        if not r4:
            print("  提示：端口被占用可先运行 stop.bat，或改 cnki_paper_api/.env 的 PORT")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
