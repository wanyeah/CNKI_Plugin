"""临时论文库：保存本次会话检索到的论文，供 /download 按 paper_id 真实下载。

设计：
- 单文件 JSON（config.PAPERS_DB_PATH），追加写入，线程安全
- 每次 /search 结果写入；/download 按 ID 下载文件
- start/stop 时清空，防止堆积（见 scripts/.serverctl.py）
- 进程重启后文件可能残留，但 start 会清，因此无需在读取时做过期判断
"""
from __future__ import annotations

import json
import threading
from typing import Optional

from .. import config
from ..models import Paper


class PaperStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.path = config.PAPERS_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def put_many(self, papers: list[Paper]) -> int:
        """批量写入，返回本次新增条数（按 ID 去重，重复则覆盖）。"""
        with self._lock:
            data = self._read()
            before = set(data)
            for p in papers:
                if p.paper_id:
                    data[p.paper_id] = p.model_dump()
            self._write(data)
            return len(set(data) - before)

    def get(self, paper_id: str) -> Optional[Paper]:
        with self._lock:
            raw = self._read().get(paper_id)
        if not raw:
            return None
        try:
            return Paper(**raw)
        except Exception:
            return None

    def get_many(self, ids: list[str]) -> dict[str, Optional[Paper]]:
        with self._lock:
            data = self._read()
        out: dict[str, Optional[Paper]] = {}
        for pid in ids:
            raw = data.get(pid)
            try:
                out[pid] = Paper(**raw) if raw else None
            except Exception:
                out[pid] = None
        return out

    def size(self) -> int:
        with self._lock:
            return len(self._read())

    def clear(self) -> int:
        """清空临时库，返回清空前条数。"""
        with self._lock:
            n = len(self._read())
            self._write({})
            return n


_store: Optional[PaperStore] = None
_store_lock = threading.Lock()


def get_paper_store() -> PaperStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = PaperStore()
    return _store
