"""FastAPI 应用：HTTP API 端点 + OpenAPI 文档。

所有端点都是同步 def（在线程池运行），避免人工验证阻塞事件循环。
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from .. import __version__, config
from ..constants import FIELD_LABEL, SORT_CODES
from ..models import (
    BatchKeywordResult, BatchResponse, ErrorResponse, HealthResponse,
    Paper, PdfLinkItem, PdfLinkResponse, QueryMeta, SearchResponse,
    SessionRefreshResponse, make_paper_id, utcnow_iso,
)
from .cache import get_cache
from .client import CnkiClient, SearchError
from .downloader import get_downloader
from .papers import get_paper_store
from .session import CaptchaRequired, VerificationTimeout, get_session

app = FastAPI(
    title="知网论文检索 API",
    description=(
        "供 Agent 查询真实存在的知网论文，杜绝幻觉引用。\n\n"
        "核心特性：分页查询（page / page_size）、多关键词批量、来源可溯源"
        "（每条带 source_url）、遇到滑块自动弹出浏览器由人工完成。\n\n"
        "响应中的 query.total / has_more 可帮助 Agent 判断是否需要继续翻页。\n\n"
        "PDF 下载：/search 结果里的 paper_id 存入临时库。GET /download?paper_id=xxx "
        "让服务端用浏览器复用登录态真实下载，返回本地文件路径（含 sha256，批量上限 3）。"
        "临时库在 start/stop 时清空。"
        "注意知网 2026 年下载走订单接口，必须用浏览器会话完成，不提供可直接 GET 的下载链接。"
    ),
    version=__version__,
)


def _build_response(payload: dict) -> SearchResponse:
    return SearchResponse(**payload)


def _remember_papers(data: dict) -> None:
    """把检索到的论文存入临时库（兼容旧缓存里没有 paper_id 的条目）。"""
    papers: list[Paper] = []
    for raw in data.get("papers", []):
        try:
            p = Paper(**raw)
        except Exception:
            continue
        if not p.paper_id:
            p.paper_id = make_paper_id(p.export_id, p.source_url, p.title)
        papers.append(p)
    if papers:
        get_paper_store().put_many(papers)
        # 回写 paper_id 到响应，保证 Agent 看到的每条都有 ID
        by_title = {p.title: p for p in papers}
        for raw in data.get("papers", []):
            p = by_title.get(raw.get("title"))
            if p:
                raw["paper_id"] = p.paper_id


def _do_search(
    keyword: str, field: str, sort: str, page: int, page_size: int,
    expert: bool = False,
) -> dict:
    """执行检索，命中缓存则直接返回。返回可序列化的 dict。"""
    cache = get_cache()
    key = cache.make_key(keyword, field, sort, page, page_size, expert)
    cached = cache.get(key)
    if cached is not None:
        cached["query"]["from_cache"] = True
        cached["query"]["fetched_at"] = utcnow_iso()
        _remember_papers(cached)
        return cached

    session = get_session()
    session.ensure_verified("首次检索需要先登录知网")
    client = CnkiClient(session)

    papers, total = client.fetch_slice(keyword, field, sort, page, page_size, expert)

    total_pages: Optional[int] = None
    has_more = False
    if total is not None:
        total_pages = (total + page_size - 1) // page_size if page_size else None
        has_more = page < total_pages
    else:
        # 无法解析总数时，用"本页是否满员"保守判断
        has_more = len(papers) >= page_size

    meta = QueryMeta(
        keyword=keyword,
        field=field,
        field_label=FIELD_LABEL.get(field, "主题"),
        sort=sort,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        returned=len(papers),
        has_more=has_more,
        fetched_at=utcnow_iso(),
        from_cache=False,
    )
    notice = (
        "知网列表页不提供摘要/关键词/DOI，已留空；如需全文信息请到 source_url 核对。"
        if not expert else None
    )
    resp = SearchResponse(success=True, query=meta, papers=papers, notice=notice)
    data = resp.model_dump()
    cache.set(key, data)
    _remember_papers(data)
    return data


@app.get(
    "/search",
    response_model=SearchResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="按关键词检索论文（分页）",
)
def search(
    keyword: str = Query(..., description="检索词，如：中药"),
    field: str = Query("SU", description=f"检索字段码，可选：{','.join(FIELD_LABEL)}"),
    sort: str = Query("relevance", description=f"排序：{','.join(SORT_CODES)}"),
    page: int = Query(1, ge=1, description="第几页，从 1 开始"),
    page_size: int = Query(10, ge=1, le=20, description="每页条数，1~20,默认10"),
    expert: bool = Query(False, description="是否把 keyword 当作知网专业检索表达式"),
):
    try:
        if field not in FIELD_LABEL:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error=f"未知检索字段 {field}",
                    code="BAD_FIELD",
                    hint=f"可用字段：{', '.join(FIELD_LABEL)}",
                ).model_dump(),
            )
        if sort not in SORT_CODES:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error=f"未知排序 {sort}", code="BAD_SORT",
                    hint=f"可用排序：{', '.join(SORT_CODES)}",
                ).model_dump(),
            )
        return _do_search(keyword, field, sort, page, page_size, expert)
    except SearchError as exc:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(error=exc.detail, code=exc.code,
                                  hint="重试一次；若仍失败先调用 /session/refresh").model_dump(),
        )
    except (CaptchaRequired, VerificationTimeout) as exc:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error=str(exc), code="VERIFICATION_REQUIRED",
                hint="请在弹出的浏览器中完成滑块后重试，或调用 /session/refresh",
            ).model_dump(),
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=f"检索失败：{exc}", code="INTERNAL").model_dump(),
        )


@app.get(
    "/batch",
    response_model=BatchResponse,
    summary="批量检索（多关键词，串行执行）",
)
def batch(
    keywords: list[str] = Query(..., description="多个检索词，如 ?keywords=中药&keywords=针灸"),
    field: str = Query("SU", description="检索字段码"),
    sort: str = Query("relevance", description="排序"),
    page: int = Query(1, ge=1, description="第几页"),
    page_size: int = Query(10, ge=1, le=20, description="每页条数"),
):
    results: list[BatchKeywordResult] = []
    for kw in keywords:
        try:
            data = _do_search(kw, field, sort, page, page_size)
            results.append(BatchKeywordResult(
                keyword=kw, success=True, response=SearchResponse(**data)
            ))
        except Exception as exc:  # noqa: BLE001
            results.append(BatchKeywordResult(keyword=kw, success=False, error=str(exc)))
    return BatchResponse(
        success=all(r.success for r in results),
        results=results,
        notice="串行执行以避免触发知网风控；关键词较多时请耐心等待。",
    )


@app.get(
    "/download",
    response_model=PdfLinkResponse,
    summary="按论文 ID 下载 PDF（服务端真实下载到本地，上限 3 篇）",
)
def download(
    paper_id: list[str] = Query(
        ..., description="论文 ID（/search 返回的 paper_id），可重复传多个，最多 3 个"
    ),
):
    """服务端用浏览器复用登录态真实点击下载，返回本地文件路径。

    - 成功时 `file` 字段为 SavedFile：path/filename/size_bytes/sha256/is_pdf。
    - 同步等待，一次最多 3 篇；逐条返回状态，一个失败不影响其他。

    注意：知网 2026 年下载走 bar.cnki.net 订单接口，没有可直接 GET 的 PDF 链接，
    因此本接口不返回下载网址，只负责把真实文件下载到 downloads/。
    """
    limit = config.PDF_SAVE_BATCH_MAX

    ids = list(dict.fromkeys(paper_id))  # 去重保序
    truncated = len(ids) > limit
    if truncated:
        ids = ids[:limit]

    store = get_paper_store()
    found = store.get_many(ids)
    downloader = get_downloader()
    session_ready = get_session().has_session()

    results: list[PdfLinkItem] = []
    for pid in ids:
        paper = found.get(pid)
        if paper is None:
            results.append(PdfLinkItem(
                paper_id=pid, status="not_found",
                session_ready=session_ready,
                error="临时库中找不到该 ID。请先用 /search 检索，注意 start/stop 会清空临时库。",
                how_to="重新调用 /search 获取新的 paper_id。",
                fetched_at=utcnow_iso(),
            ))
            continue
        results.append(_save_item(downloader.download(paper), paper, session_ready))

    all_ok = all(r.status == "saved" for r in results) and not truncated
    base_notice = (
        "仅返回你已获授权访问的文献；请遵守知网服务条款与个人学术研究用途。"
        " 服务端真实下载到 downloads/，请校验 file.sha256。"
    )
    if truncated:
        base_notice += (
            f" 请求 {len(paper_id)} 个 ID 超过上限 {limit}，已截断为前 {limit} 个。"
        )
    return PdfLinkResponse(
        success=all_ok, requested=len(ids), results=results, notice=base_notice,
    )


def _save_item(dl: dict, paper: Paper, session_ready: bool) -> PdfLinkItem:
    """把 downloader 的结果转成 PdfLinkItem。"""
    status = dl["status"]
    # need_login 时也标记 session_ready=false（可能会话存在但 nxgp 未授权）
    ready = session_ready and status != "need_login"
    return PdfLinkItem(
        paper_id=paper.paper_id, title=paper.title, status=status,
        session_ready=ready, can_download=status == "saved",
        file=dl.get("file"), error=dl.get("error"),
        source_url=paper.source_url,
        how_to=(
            "文件已下载到本机，读取 file.path 即可。"
            if status == "saved" else
            "先 POST /session/refresh 完成知网登录，再重试下载。"
            if status == "need_login" else
            "如实向用户说明失败原因，不要假装下载成功。"
        ),
        validity_hint="请用 file.sha256 校验文件完整性，文件头已校验为 %PDF。",
        fetched_at=dl.get("fetched_at", utcnow_iso()),
    )


@app.post("/papers/clear", summary="清空临时论文库")
def clear_papers():
    n = get_paper_store().clear()
    return {"success": True, "cleared": n, "message": f"已清空 {n} 条临时论文记录"}


@app.get("/health", response_model=HealthResponse, summary="健康检查")
def health():
    session = get_session()
    return HealthResponse(
        status="ok",
        has_session=session.has_session(),
        cache_size=get_cache().size(),
        paper_count=get_paper_store().size(),
        version=__version__,
    )


@app.post("/session/refresh", response_model=SessionRefreshResponse,
          summary="手动刷新登录态（弹出浏览器）")
def refresh_session():
    """弹浏览器让用户登录知网 / 过滑块，保存会话。"""
    try:
        result = get_session().refresh_session()
        return SessionRefreshResponse(
            success=True,
            message=f"会话已保存：{result['path']}（{result['cookie_count']} 个 Cookie）",
            saved_at=result["path"],
        )
    except Exception as exc:  # noqa: BLE001
        return SessionRefreshResponse(success=False, message=f"刷新失败：{exc}")


@app.post("/session/reset", summary="清除登录态（下次检索重新验证）")
def reset_session():
    get_session().invalidate()
    return {"success": True, "message": "登录态已清除"}


@app.get("/fields", summary="列出可用检索字段与排序码")
def fields():
    return {
        "fields": [{"code": c, "label": l} for c, l in FIELD_LABEL.items()],
        "sorts": [{"code": c, "desc": d} for c, d in {
            "relevance": "相关度", "time": "发表时间", "cited": "被引",
            "download": "下载", "overall": "综合",
        }.items()],
        "page_size_range": [1, 20],
    }
