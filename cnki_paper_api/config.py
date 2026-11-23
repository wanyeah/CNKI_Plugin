"""配置：从环境变量 / .env 读取，提供 sane 默认值。"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（本文件所在目录）
BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """极简 .env 载入：仅在环境变量未设置时填入，避免依赖 python-dotenv。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and os.getenv(key) is None:
            os.environ[key] = value


_load_dotenv()


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


HOST = os.getenv("HOST", "127.0.0.1")
PORT = _get_int("PORT", 8765)
VERIFY_TIMEOUT_SECONDS = _get_int("VERIFY_TIMEOUT_SECONDS", 300)
HEADLESS = os.getenv("HEADLESS", "false").strip().lower() in {"1", "true", "yes"}
REQUEST_DELAY_MIN = _get_float("REQUEST_DELAY_MIN", 1.0)
REQUEST_DELAY_MAX = _get_float("REQUEST_DELAY_MAX", 2.0)
MAX_RETRIES = _get_int("MAX_RETRIES", 3)
CACHE_TTL_SECONDS = _get_int("CACHE_TTL_SECONDS", 3600)
CACHE_DB_PATH = BASE_DIR / os.getenv("CACHE_DB_PATH", "storage/cache.sqlite3")
STORAGE_STATE_PATH = BASE_DIR / os.getenv("STORAGE_STATE_PATH", "storage/cnki_state.json")
# 临时论文库：本次会话检索到的论文，start/stop 时清理
PAPERS_DB_PATH = BASE_DIR / os.getenv("PAPERS_DB_PATH", "storage/papers.json")
# PDF 直链提取间隔（秒），随机均匀分布于区间
PDF_DELAY_MIN = _get_float("PDF_DELAY_MIN", 2.0)
PDF_DELAY_MAX = _get_float("PDF_DELAY_MAX", 4.0)
# 批量 /download 一次最多几个 ID（真实下载，重操作）
PDF_SAVE_BATCH_MAX = _get_int("PDF_SAVE_BATCH_MAX", 3)
# 下载文件保存目录（默认项目根目录的 downloads/，与 download_pdf.py 一致）
ROOT_DIR = BASE_DIR.parent
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", str(ROOT_DIR / "downloads")))
# 下载浏览器是否无头：默认有头（便于人工登录兜底），设 true 则后台静默
PDF_SAVE_HEADLESS = os.getenv("PDF_SAVE_HEADLESS", "false").strip().lower() in {"1", "true", "yes"}
# 等待下载的超时秒数
PDF_SAVE_TIMEOUT_SECONDS = _get_int("PDF_SAVE_TIMEOUT_SECONDS", 120)

# 保证目录存在
CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
PAPERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
