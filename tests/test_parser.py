"""解析器与分页切片单测（纯本地，不联网）。运行：python -m pytest tests/ 或 python tests/test_parser.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cnki_paper_api.api.parser import (
    has_result_table, parse_papers, parse_total, parse_turnpage_token,
)
from cnki_paper_api.api.client import CnkiClient
from cnki_paper_api.models import Paper

SAMPLE = """
<div class="result-table-list">
  <span class="Count">找到 1,234 条结果</span>
  <input type="hidden" id="hidTurnPage" value="TURNPAGE_TOKEN_ABC" />
  <table><tbody>
    <tr>
      <td class="name"><a class="fz14" href="/kcms2/article/abstract?v=ABC123">中药药理研究进展</a></td>
      <td class="author"><a>张三</a>; <a>李四</a></td>
      <td class="source">中国中药杂志</td>
      <td class="date">2023</td>
      <td class="quote"><a>42</a></td>
      <td class="data">CJFQ</td>
      <td><input class="cbItem" value="EXPORTID001" /></td>
    </tr>
    <tr>
      <td class="name"><a class="fz14" href="https://kns.cnki.net/kcms2/article/abstract?v=XYZ">针灸疗法综述</a></td>
      <td class="author"><a>王五</a></td>
      <td class="source">中医杂志</td>
      <td class="date">2022</td>
      <td class="quote">18</td>
      <td class="data">CDFD</td>
      <td><input class="cbItem" value="EXPORTID002" /></td>
    </tr>
  </tbody></table>
</div>
"""


def test_parse_papers():
    rows = parse_papers(SAMPLE)
    assert len(rows) == 2, rows
    p = rows[0]
    assert p.title == "中药药理研究进展", p.title
    assert p.authors == "张三; 李四", p.authors  # 不再出现整格拼接重复
    assert p.journal == "中国中药杂志", p.journal
    assert p.date == "2023"
    assert p.cited == "42"
    assert p.db == "中国学术期刊", p.db  # CJFQ -> 中文名
    assert p.source_url == "https://kns.cnki.net/kcms2/article/abstract?v=ABC123"
    assert p.export_id == "EXPORTID001"
    assert rows[1].db == "博士学位论文"  # CDFD
    print("[OK] test_parse_papers")


def test_total_and_token():
    assert parse_total(SAMPLE) == 1234
    assert parse_turnpage_token(SAMPLE) == "TURNPAGE_TOKEN_ABC"
    assert has_result_table(SAMPLE)
    print("[OK] test_total_and_token")


def test_slice_mapping():
    """验证用户 page/page_size 切片：10 条/页时第 2 页 = 全局第 11~20 条。"""
    client = CnkiClient.__new__(CnkiClient)  # 不走真实网络
    calls = []

    def fake_fetch(kw, field, sort, cnki_page, expert, on_progress):
        calls.append(cnki_page)
        start = (cnki_page - 1) * 20 + 1
        papers = [Paper(n=start + i, title=f"paper-{start+i}") for i in range(20)]
        return papers, 100

    client.fetch_page = fake_fetch  # type: ignore
    # 第 1 页 10 条 -> 只抓知网第 1 页
    p1, t = client.fetch_slice("中药", "SU", "relevance", 1, 10)
    assert [x.title for x in p1] == [f"paper-{i}" for i in range(1, 11)], [x.title for x in p1]
    assert calls == [1], calls

    calls.clear()
    # 第 2 页 10 条 -> 全局 11~20，仍在知网第 1 页
    p2, _ = client.fetch_slice("中药", "SU", "relevance", 2, 10)
    assert [x.title for x in p2] == [f"paper-{i}" for i in range(11, 21)], [x.title for x in p2]
    assert calls == [1], calls

    calls.clear()
    # 第 3 页 10 条 -> 全局 21~30，跨到知网第 2 页
    p3, _ = client.fetch_slice("中药", "SU", "relevance", 3, 10)
    assert [x.title for x in p3] == [f"paper-{i}" for i in range(21, 31)], [x.title for x in p3]
    assert calls == [2], calls  # 只需知网第 2 页（起点正好对齐）
    assert p3[0].n == 1, p3[0].n  # 本页重新编号
    print("[OK] test_slice_mapping")


def test_captcha_detection():
    """知网 /verify/ 安全验证空壳页要被识别，正常结果页不误判。"""
    from cnki_paper_api.api.parser import looks_like_captcha

    verify_shell = '<title>å®‰å…¨éªŒè¯</title><script src="/verify/js/app.f9c1.js"></script>'
    assert looks_like_captcha(verify_shell) is True
    assert looks_like_captcha(SAMPLE) is False
    print("[OK] test_captcha_detection")


def test_download_models():
    """下载模型：只有 save 一种模式，成功项带 file，且不含 download_url/mode 旧字段。"""
    from cnki_paper_api.models import PdfLinkItem, SavedFile, PdfLinkResponse

    saved = PdfLinkItem(
        paper_id="abc", status="saved",
        can_download=True, session_ready=True,
        file=SavedFile(path="D:/x.pdf", filename="x.pdf", size_bytes=100,
                       sha256="a" * 64, is_pdf=True),
    )
    d = saved.model_dump()
    assert d["status"] == "saved" and d["file"]["is_pdf"] is True
    assert d["file"]["sha256"] == "a" * 64
    # info 模式的遗留字段必须已移除，避免再把误导性的下载链接交给用户
    for legacy in ("mode", "download_url", "pdf_url", "doc_type", "validity_hint"):
        assert legacy not in d, f"遗留字段未清除: {legacy}"

    resp = PdfLinkResponse(success=True, requested=1, results=[saved])
    d2 = resp.model_dump()
    assert "mode" not in d2
    print("[OK] test_download_models")


if __name__ == "__main__":
    test_parse_papers()
    test_total_and_token()
    test_slice_mapping()
    test_captcha_detection()
    test_download_models()
    print("\nALL TESTS PASSED")
