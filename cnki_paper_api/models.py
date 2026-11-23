"""对外数据模型。防幻觉的核心：每条论文都带 source_url 和 fetched_at。"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


def make_paper_id(export_id: str, source_url: str, title: str) -> str:
    """论文稳定 ID：优先知网 export_id，否则用 URL/标题算短 hash。

    同一篇论文无论检索几次、第几页，只要 export_id 相同 ID 就稳定；
    没有 export_id 时退回到 URL + 标题。返回 12 位十六进制串。
    """
    seed = export_id or f"{source_url}|{title}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:12]


class Paper(BaseModel):
    """一条文献题录。"""

    n: int = Field(description="在本页返回中的序号，从 1 开始")
    paper_id: str = Field(default="", description="稳定 ID，供 /download 按 ID 取 PDF 直链")
    title: str = Field(default="", description="篇名")
    authors: str = Field(default="", description="作者，分号分隔")
    journal: str = Field(default="", description="来源期刊 / 学位论文单位")
    date: str = Field(default="", description="发表日期 / 年份")
    cited: str = Field(default="", description="被引次数（知网原始文本）")
    downloads: str = Field(default="", description="下载次数（知网原始文本）")
    db: str = Field(default="", description="数据库来源")
    doi: str = Field(default="", description="DOI（列表页通常没有，留空）")
    abstract: str = Field(default="", description="摘要（列表页通常没有，留空）")
    keywords: str = Field(default="", description="关键词（列表页通常没有，留空）")
    source_url: str = Field(default="", description="知网详情页链接，可溯源核对")
    export_id: str = Field(default="", description="知网引用导出 ID，供后续扩展")


class QueryMeta(BaseModel):
    """检索元信息，帮助 Agent 判断数据边界，避免把第一页当全部。"""

    keyword: str
    field: str = Field(description="检索字段码，如 SU")
    field_label: str = Field(description="检索字段中文名，如 主题")
    sort: str
    page: int = Field(description="本次返回的是第几页（用户视角，从 1 开始）")
    page_size: int = Field(description="本页每页条数（用户视角）")
    total: Optional[int] = Field(default=None, description="知网返回命中总数，解析失败为 null")
    total_pages: Optional[int] = Field(default=None, description="按 page_size 估算的总页数")
    returned: int = Field(description="本页实际返回条数")
    has_more: bool = Field(description="是否可能还有下一页")
    fetched_at: str = Field(description="数据抓取时间 ISO8601")
    from_cache: bool = Field(default=False, description="是否来自缓存")


class SearchResponse(BaseModel):
    success: bool = True
    query: QueryMeta
    papers: list[Paper]
    notice: Optional[str] = Field(
        default=None,
        description="面向 Agent 的提示，例如「摘要字段知网列表页不提供，已留空」",
    )


class BatchKeywordResult(BaseModel):
    keyword: str
    success: bool
    response: Optional[SearchResponse] = None
    error: Optional[str] = None


class BatchResponse(BaseModel):
    success: bool = True
    results: list[BatchKeywordResult]
    notice: Optional[str] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    code: str
    hint: Optional[str] = Field(default=None, description="给 Agent 或用户的处理建议")


class HealthResponse(BaseModel):
    status: str
    has_session: bool = Field(description="是否已有保存的登录态文件")
    cache_size: int = Field(description="缓存条目数")
    paper_count: int = Field(default=0, description="临时论文库中保存的论文数")
    version: str = "1.0.0"


class SessionRefreshResponse(BaseModel):
    success: bool
    message: str
    saved_at: Optional[str] = None


# ---------------- PDF 下载相关 ----------------

class SavedFile(BaseModel):
    """服务端真实下载得到的文件信息。"""

    path: str = Field(description="本机绝对路径，用户 / Agent 可直接读取")
    filename: str = Field(description="清洗后的建议文件名")
    size_bytes: int = Field(description="文件字节数")
    sha256: str = Field(description="文件 SHA-256，供校验与去重")
    is_pdf: bool = Field(description="文件头是否为 %PDF")


class PdfLinkItem(BaseModel):
    """单篇论文的下载结果。批量 /download 时逐条返回，一个失败不影响其他。

    本接口只用浏览器真实下载到本地，不返回任何可直接 GET 的下载链接
    （知网 2026 年改走订单接口，链接点击会报「来源应用不正确(01)」）。

    status 取值：
    - saved       文件已落盘，读 file 字段
    - no_pdf      该类无 PDF（如报纸仅 CAJ）或详情页无下载按钮
    - need_login  下载平台（nxgp）未授权，需先 POST /session/refresh
    - not_found   ID 不在临时库
    - error       其他失败
    """

    paper_id: str
    title: str = Field(default="", description="论文标题（便于 Agent 核对，不信任时看 source_url）")
    status: str = Field(description="结果状态，见类文档")
    can_download: bool = Field(default=False, description="是否已成功下载（等同 status==saved）")
    session_ready: bool = Field(default=False, description="当前是否已有可用登录态")
    how_to: str = Field(default="", description="给调用方的下一步操作建议")
    source_url: str = Field(default="", description="知网详情页，供溯源核对")
    file: Optional[SavedFile] = Field(default=None, description="下载成功时的本地文件信息")
    error: Optional[str] = Field(default=None, description="失败原因，status 为失败态时有值")
    fetched_at: str = Field(default="", description="下载完成时间 ISO8601")


class PdfLinkResponse(BaseModel):
    """GET /download 的响应。"""

    success: bool = Field(description="是否全部成功；有一条失败即为 false，但结果仍逐条返回")
    requested: int = Field(description="请求的 ID 数量（去重截断后）")
    results: list[PdfLinkItem]
    notice: str = Field(
        default="仅返回你已获授权访问的文献；请遵守知网服务条款与个人学术研究用途。"
    )


def utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
