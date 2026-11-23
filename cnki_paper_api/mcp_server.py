"""MCP Server：把检索能力封装成 Agent 可自动发现的工具（stdio 传输）。

支持 MCP 的 Agent（WorkBuddy / Claude Desktop / Cursor 等）配置本服务后，
可直接调用 search_papers / batch_search / download_pdf_links 工具，无需记 HTTP URL。

注意：MCP 工具直接进程内调用检索客户端，共享同一份 SQLite 缓存与会话文件，
因此建议先通过 HTTP 服务的 /session/refresh 完成一次登录，再启动本 MCP。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 兼容 MCP SDK 1.x（FastMCP）与 2.x（MCPServer）
try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server  # noqa: E402
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # noqa: E402

from cnki_paper_api import __version__  # noqa: E402
from cnki_paper_api.api.client import CnkiClient, SearchError  # noqa: E402
from cnki_paper_api.api.session import get_session  # noqa: E402
from cnki_paper_api.constants import FIELD_LABEL, SORT_CODES  # noqa: E402
from cnki_paper_api.models import utcnow_iso  # noqa: E402

mcp = _Server("cnki-paper-search")


def _search_dict(keyword: str, field: str, sort: str, page: int, page_size: int) -> dict:
    session = get_session()
    session.ensure_verified("MCP 检索需要先登录知网")
    client = CnkiClient(session)
    papers, total = client.fetch_slice(keyword, field, sort, page, page_size)
    total_pages = (total + page_size - 1) // page_size if total else None
    return {
        "success": True,
        "query": {
            "keyword": keyword, "field": field,
            "field_label": FIELD_LABEL.get(field, "主题"),
            "sort": sort, "page": page, "page_size": page_size,
            "total": total, "total_pages": total_pages,
            "returned": len(papers),
            "has_more": (page < total_pages) if total_pages else (len(papers) >= page_size),
            "fetched_at": utcnow_iso(),
        },
        "papers": [p.model_dump() for p in papers],
        "notice": "仅返回知网真实检索到的论文；每条 source_url 可供你核对。",
    }


@mcp.tool()
def search_papers(
    keyword: str,
    field: str = "SU",
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """检索知网真实论文，返回题录和来源链接，防止 AI 编造论文。

    参数：
    - keyword: 检索词，如「中药」
    - field: 字段码 SU主题/TI篇名/KY关键词/AU作者 等，默认 SU
    - sort: relevance相关度/time发表时间/cited被引/download下载/overall综合
    - page: 页码，从 1 开始
    - page_size: 每页条数，1~20，默认 10

    响应中的 query.total / has_more 用于判断是否继续翻页。
    """
    try:
        if field not in FIELD_LABEL:
            return {"success": False, "error": f"未知字段 {field}"}
        if sort not in SORT_CODES:
            return {"success": False, "error": f"未知排序 {sort}"}
        if not (1 <= page_size <= 20):
            return {"success": False, "error": "page_size 必须在 1~20"}
        return _search_dict(keyword, field, sort, page, page_size)
    except SearchError as exc:
        return {"success": False, "error": exc.detail, "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"检索失败：{exc}"}


@mcp.tool()
def batch_search(
    keywords: list[str],
    field: str = "SU",
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """批量检索多个关键词（串行执行，避免触发风控）。返回每个关键词的结果。"""
    out = []
    for kw in keywords:
        try:
            out.append({"keyword": kw, "success": True, **_search_dict(kw, field, sort, page, page_size)})
        except Exception as exc:  # noqa: BLE001
            out.append({"keyword": kw, "success": False, "error": str(exc)})
    return {"success": True, "results": out}


@mcp.tool()
def download_pdf_links(paper_ids: list[str]) -> dict:
    """按论文 ID 下载 PDF 到本机（服务端用浏览器复用登录态真实下载，最多 3 篇）。

    参数：
    - paper_ids: 论文 ID 列表，来自 search_papers 返回的 paper_id

    成功后读 result.file.path（本机绝对路径）、result.file.sha256（校验用）。
    知网 2026 年下载走订单接口，没有可直接 GET 的 PDF 链接，本工具不返回下载网址。
    status: saved / need_login / no_pdf / not_found / error。一个失败不影响其他。
    请仅用于你已获授权访问的文献。
    """
    from cnki_paper_api.api.papers import get_paper_store
    from cnki_paper_api.api.downloader import get_downloader
    from cnki_paper_api.api.main import _save_item
    from cnki_paper_api.api.session import get_session
    from cnki_paper_api import config

    limit = config.PDF_SAVE_BATCH_MAX
    ids = list(dict.fromkeys(paper_ids))[:limit]
    store = get_paper_store()
    found = store.get_many(ids)
    session_ready = get_session().has_session()
    results = []
    for pid in ids:
        paper = found.get(pid)
        if paper is None:
            results.append({
                "paper_id": pid, "status": "not_found",
                "error": "临时库找不到该 ID；请先用 search_papers 检索（start/stop 会清空临时库）。",
            })
            continue
        dl = get_downloader().download(paper)
        results.append(_save_item(dl, paper, session_ready).model_dump())
    return {
        "success": all(r.get("status") == "saved" for r in results),
        "requested": len(ids),
        "results": results,
        "notice": (
            "仅返回你已获授权访问的文献；请遵守知网服务条款与个人学术研究用途。"
            " 服务端真实下载到 downloads/，请校验 file.sha256。"
        ),
    }


@mcp.tool()
def list_fields() -> dict:
    """列出可用的检索字段码和排序码。"""
    return {"fields": FIELD_LABEL, "sorts": list(SORT_CODES), "page_size_range": [1, 20]}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
