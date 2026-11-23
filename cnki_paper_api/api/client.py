"""检索客户端：构造知网表单、首页取令牌、翻页、限速重试。

关键事实：知网 pageSize 固定 20，pageNum>0 时 boolSearch=true 会回空；
必须先用 boolSearch=true 触发检索拿到 #hidTurnPage 令牌，再用 boolSearch=false 翻页。
用户侧的 page / page_size 由本层做"逻辑页"切片，不改动知网参数。
"""
from __future__ import annotations

import json
import random
import re
import time
from typing import Callable, Optional

from .. import config
from ..constants import (
    CNKI_PAGE_SIZE, FIELD_LABEL, GRID_URL, KUA_KU_CODE,
    PAGE2_PRODUCTS, SEARCH_PAGE, SORT_CODES,
)
from ..models import Paper
from .parser import (
    has_result_table, parse_papers, parse_total, parse_turnpage_token,
)
from .session import CnkiSession, VerificationTimeout


class SearchError(Exception):
    def __init__(self, message: str, code: str = "SEARCH_FAILED"):
        self.detail = message
        self.code = code
        super().__init__(message)


def _sleep_delay() -> None:
    time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))


def _build_query_json(keyword: str, field: str, expert: bool = False) -> dict:
    label = FIELD_LABEL.get(field, "主题")
    if expert:
        return {
            "Platform": "", "Resource": "CROSSDB", "Classid": "WD0FTY92", "Products": "",
            "QNode": {"QGroup": [
                {"Key": "Subject", "Title": "", "Logic": 0, "Items": [{
                    "Key": "Expert", "Title": "", "Logic": 0, "Field": "EXPERT",
                    "Operator": 0, "Value": keyword, "Value2": "", "options": {},
                }], "ChildItems": []},
                {"Key": "ControlGroup", "Title": "", "Logic": 0, "Items": [], "ChildItems": []},
            ]},
            "ExScope": "1", "SimpTrad": "0", "SearchType": 4, "Rlang": "CHINESE",
            "KuaKuCode": KUA_KU_CODE, "Expands": {}, "View": "changeDBCh", "SearchFrom": 1,
        }
    return {
        "Platform": "", "Resource": "CROSSDB", "Classid": "WD0FTY92", "Products": "",
        "QNode": {"QGroup": [{
            "Key": "Subject", "Title": "", "Logic": 0,
            "Items": [{
                "Field": field, "Value": keyword, "Operator": "TOPRANK",
                "Logic": 0, "Vector": "", "Title": label,
            }],
            "ChildItems": [],
        }]},
        "ExScope": 1, "SimpTrad": "0", "SearchType": 2, "Rlang": "CHINESE",
        "KuaKuCode": KUA_KU_CODE, "Expands": {}, "View": "changeDBCh", "SearchFrom": 1,
    }


def _first_page_form(qj: dict, aside: str, sort_field: str) -> dict:
    return {
        "boolSearch": "true",
        "QueryJson": json.dumps(qj, ensure_ascii=False, separators=(",", ":")),
        "pageNum": "1",
        "pageSize": str(CNKI_PAGE_SIZE),
        "sortField": sort_field,
        "sortType": "desc" if sort_field else "",
        "dstyle": "listmode",
        "productStr": "",
        "aside": aside,
        "searchFrom": "资源范围：总库",
        "subject": "", "language": "", "uniplatform": "", "CurPage": "1",
    }


def _turn_form(qj: dict, page_num: int, turnpage: str, sort_field: str) -> dict:
    qj2 = dict(qj)
    qj2["Products"] = PAGE2_PRODUCTS
    qj2["SearchFrom"] = 4
    return {
        "boolSearch": "false",
        "QueryJson": json.dumps(qj2, ensure_ascii=False, separators=(",", ":")),
        "pageNum": str(page_num),
        "pageSize": str(CNKI_PAGE_SIZE),
        "sortField": sort_field or "PT",
        "sortType": "desc",
        "dstyle": "listmode",
        "boolSortSearch": "false",
        "productStr": "", "aside": "",
        "searchFrom": "资源范围：总库",
        "subject": "", "turnpage": turnpage,
        "language": "", "uniplatform": "",
    }


class CnkiClient:
    def __init__(self, session: CnkiSession, delay: bool = True):
        self.session = session
        self.delay = delay
        # 缓存同一查询的令牌与首页结果，避免重复触发检索
        self._token_cache: dict[str, tuple[str, str, Optional[int]]] = {}

    def _post(self, data: dict) -> str:
        """带重试的 POST，返回正文。"""
        last = ""
        for attempt in range(config.MAX_RETRIES):
            try:
                resp = self.session.post(GRID_URL, data=data, referer=SEARCH_PAGE)
                last = resp.text
                if self.delay:
                    _sleep_delay()
                return last
            except (VerificationTimeout,) :
                raise
            except Exception as exc:  # noqa: BLE001
                if attempt == config.MAX_RETRIES - 1:
                    raise SearchError(f"请求知网失败：{exc}", "NETWORK_ERROR")
                time.sleep(1 + attempt)
        return last

    def fetch_page(self, keyword: str, field: str, sort: str, page_num: int,
                   expert: bool = False, on_progress: Optional[Callable[[str], None]] = None
                   ) -> tuple[list[Paper], Optional[int]]:
        """取知网第 page_num 页（1 起），返回 (papers, total)。

        page_num == 1 用 boolSearch=true；>1 用翻页形态并携带令牌。
        """
        qj = _build_query_json(keyword, field, expert=expert)
        label = FIELD_LABEL.get(field, "主题")
        aside = f"({keyword})" if expert else f"({label}：{keyword})"
        sort_field = "" if sort == "relevance" else SORT_CODES.get(sort, "PT")

        token_key = f"{keyword}|{field}|{sort}|{expert}"
        cached = self._token_cache.get(token_key)
        turnpage = cached[0] if cached else ""

        if page_num == 1:
            html = self._post(_first_page_form(qj, aside, sort_field))
            if on_progress:
                on_progress("已触发检索（第 1 页）")
            turnpage = parse_turnpage_token(html) or turnpage
            total = parse_total(html)
            if turnpage or total is not None:
                self._token_cache[token_key] = (turnpage, html, total)
            return parse_papers(html, start_index=1), total

        # 翻页：确保已有令牌
        if not turnpage:
            html1 = self._post(_first_page_form(qj, aside, sort_field))
            turnpage = parse_turnpage_token(html1)
            total = parse_total(html1)
            if on_progress:
                on_progress("已获取翻页令牌")
            if not turnpage:
                raise SearchError(
                    "未能获取知网翻页令牌（hidTurnPage），可能是验证未完成或接口改版。"
                    "可尝试 /session/refresh 后重试。",
                    "NO_TURN_TOKEN",
                )
            self._token_cache[token_key] = (turnpage, html1, total)
        else:
            total = cached[1] if cached else None

        html = self._post(_turn_form(qj, page_num, turnpage, sort_field))
        if on_progress:
            on_progress(f"已取第 {page_num} 页")
        papers = parse_papers(html, start_index=(page_num - 1) * CNKI_PAGE_SIZE + 1)
        # 软封：空结果且带"暂无数据"，尝试刷新会话后重试一次
        if not papers and "暂无数据" in html:
            raise SearchError(
                "知网返回空结果并提示「暂无数据」，可能是软封或关键词无命中。"
                "可尝试 /session/refresh 后重试。",
                "SOFT_BAN_OR_EMPTY",
            )
        return papers, total

    def fetch_slice(self, keyword: str, field: str, sort: str, page: int,
                    page_size: int, expert: bool = False,
                    on_progress: Optional[Callable[[str], None]] = None
                    ) -> tuple[list[Paper], Optional[int]]:
        """把用户视角的 (page, page_size) 映射到知网 20 条/页，跨页时自动拼接切片。"""
        if page < 1:
            raise SearchError("page 必须 >= 1", "BAD_PARAM")
        if page_size < 1 or page_size > 20:
            raise SearchError("page_size 必须在 1~20 之间", "BAD_PARAM")

        start = (page - 1) * page_size
        end = start + page_size
        first_cnki = start // CNKI_PAGE_SIZE + 1
        last_cnki = (end - 1) // CNKI_PAGE_SIZE + 1

        collected: dict[int, list[Paper]] = {}
        total: Optional[int] = None
        for cp in range(first_cnki, last_cnki + 1):
            papers, t = self.fetch_page(keyword, field, sort, cp, expert, on_progress)
            collected[cp] = papers
            if t is not None:
                total = t

        out: list[Paper] = []
        for cp in range(first_cnki, last_cnki + 1):
            page_papers = collected.get(cp, [])
            off_in_page = (start - (cp - 1) * CNKI_PAGE_SIZE) if cp == first_cnki else 0
            take = page_papers[off_in_page:off_in_page + page_size]
            for p in take:
                p.n = len(out) + 1
                out.append(p)
            if len(out) >= page_size:
                break
        return out[:page_size], total
