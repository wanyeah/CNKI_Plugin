"""解析知网 /kns8s/brief/grid 返回的 HTML 片段为 Paper 列表。

解析器做成"多选择器兜底 + 宽松清洗"，因为知网改版时 class 可能微调。
字段来源以列表页实际能拿到的为准：标题/作者/来源/日期/被引/数据库/链接。
摘要、关键词、DOI 列表页通常没有，留空，由 /paper/detail 扩展接口按需补。
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from ..constants import DB_NAMES
from ..models import Paper, make_paper_id


def _text(node) -> str:
    if node is None:
        return ""
    return node.get_text(strip=True)


def _first(soup, selectors: list[str]):
    """按选择器列表依次尝试，返回第一个命中的节点。"""
    for sel in selectors:
        node = soup.select_one(sel)
        if node is not None:
            return node
    return None


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://kns.cnki.net" + href
    return "https://kns.cnki.net/" + href


def _clean(s: str) -> str:
    """去掉图片 alt 拼接残留和多余空白。"""
    return re.sub(r"\s+", " ", s or "").strip()


def parse_total(html: str, soup: Optional[BeautifulSoup] = None) -> Optional[int]:
    """从结果页提取命中总数。知网常见形态：
    - <span class="Count">找到 12,345 条结果</span>
    - <em id="count">...</em>
    - 纯文本 "共有 12345 条"
    """
    soup = soup or BeautifulSoup(html, "html.parser")
    node = _first(soup, ["span.Count", "#Count", ".count", "em.count", "#totalCount"])
    candidates: list[str] = []
    if node is not None:
        candidates.append(_text(node))
    candidates.append(_text(soup))
    for raw in candidates:
        m = re.search(r"(?:找到|共有|检索到|合计)\s*([\d,，]+)\s*条", raw)
        if not m:
            m = re.search(r"([\d,，]+)\s*条结果", raw)
        if m:
            try:
                return int(m.group(1).replace(",", "").replace("，", ""))
            except ValueError:
                continue
    return None


def parse_turnpage_token(html: str, soup: Optional[BeautifulSoup] = None) -> str:
    """提取翻页令牌 hidTurnPage。"""
    soup = soup or BeautifulSoup(html, "html.parser")
    node = soup.select_one("#hidTurnPage")
    if node is not None:
        return node.get("value", "")
    m = re.search(r'id=["\']hidTurnPage["\'][^>]*value=["\']([^"\']*)["\']', html)
    return m.group(1) if m else ""


def has_result_table(html: str, soup: Optional[BeautifulSoup] = None) -> bool:
    soup = soup or BeautifulSoup(html, "html.parser")
    return soup.select_one(".result-table-list") is not None


def looks_like_captcha(html: str) -> bool:
    """从 HTML 文本判断是否是验证/拦截页（兼容非 JSON 的拦截响应）。

    知网详情页被拦时返回的是 /verify/ 下的 SPA 空壳（标题"安全验证"常被
    编码成乱码），所以除了中文关键字，还用 /verify/ 的静态资源路径做精确
    识别——这类路径正常结果页/详情页不会出现。
    """
    flags = [
        "安全验证", "向右滑动", "blockPuzzle", "verify/home",
        "captchaId", "请完成验证", "滑动验证",
    ]
    # /verify/ 空壳页的精确特征（正常页面不会有这些静态资源）
    path_markers = ["/verify/js/app.", "/verify/cnki.ico", "/verify/css/app."]
    is_verify = (
        any(f in html for f in flags)
        or any(m in html for m in path_markers)
    )
    return is_verify and ".result-table-list" not in html


def parse_papers(html: str, start_index: int = 1) -> list[Paper]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one(".result-table-list")
    if table is None:
        return []

    rows = table.select("tbody tr")
    if not rows:
        rows = table.select("tr")

    papers: list[Paper] = []
    for tr in rows:
        # 标题链接：主流是 td.name a.fz14；个别版本 a 直接带 class
        a = _first(tr, ["td.name a.fz14", "td.name a", "a.fz14"])
        if a is None:
            continue
        title = _clean(_text(a))
        if not title:
            continue

        # 知网作者单元格：每个作者是一个 <a>，td.author 整格文本是所有名字的拼接，
        # 若同时选中会导致"张三李四; 张三; 李四"的重复。优先取 <a>，没有再退回整格。
        author_nodes = tr.select("td.author a")
        if not author_nodes:
            author_nodes = tr.select("td.author span")
        if not author_nodes:
            whole = tr.select_one("td.author")
            author_nodes = [whole] if whole else []
        seen: set[str] = set()
        author_parts: list[str] = []
        for x in author_nodes:
            val = _clean(_text(x))
            if val and val not in seen:
                seen.add(val)
                author_parts.append(val)
        authors = "; ".join(author_parts)

        journal = _clean(
            _text(_first(tr, ["td.source", "td.source a", "td.journal"]))
        )
        date = _clean(_text(_first(tr, ["td.date", "td.year", "td.publishdate"])))
        cited = _clean(_text(_first(tr, ["td.quote", "td.cited", "td.quote a"])))
        downloads = _clean(_text(_first(tr, ["td.download", "td.downloads"])))
        db_raw = _clean(_text(_first(tr, ["td.data", "td.database"])))
        db = DB_NAMES.get(db_raw, db_raw)

        cb = tr.select_one("input.cbItem")
        export_id = cb.get("value", "") if cb else ""

        href = a.get("href", "")
        source_url = _abs_url(href)
        papers.append(
            Paper(
                n=start_index + len(papers),
                paper_id=make_paper_id(export_id, source_url, title),
                title=title,
                authors=authors,
                journal=journal,
                date=date,
                cited=cited,
                downloads=downloads,
                db=db,
                source_url=source_url,
                export_id=export_id,
            )
        )
    return papers
