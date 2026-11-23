"""SQLite 查询缓存：键为检索参数组合，TTL 过期，重启不丢。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional

from .. import config


class QueryCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        config.CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(config.CACHE_DB_PATH), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS query_cache (
                   key TEXT PRIMARY KEY,
                   payload TEXT NOT NULL,
                   created_at REAL NOT NULL
               )"""
        )
        self._conn.commit()

    @staticmethod
    def make_key(keyword: str, field: str, sort: str, page: int,
                 page_size: int, expert: bool = False) -> str:
        return f"{int(expert)}|{field}|{sort}|{page}|{page_size}|{keyword}"

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, created_at FROM query_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        payload, created = row
        if time.time() - created > config.CACHE_TTL_SECONDS:
            self.delete(key)
            return None
        try:
            return json.loads(payload)
        except Exception:
            self.delete(key)
            return None

    def set(self, key: str, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO query_cache (key, payload, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            self._conn.commit()

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM query_cache WHERE key = ?", (key,))
            self._conn.commit()

    def clear(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM query_cache")
            self._conn.commit()
            return cur.rowcount

    def size(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0]


_cache: Optional[QueryCache] = None
_cache_lock = threading.Lock()


def get_cache() -> QueryCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = QueryCache()
    return _cache
