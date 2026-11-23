"""用真实浏览器（复用知网登录态）点击详情页下载按钮，把 PDF 保存到 downloads/。

为什么需要浏览器：知网 2026 年 PDF 下载走 bar.cnki.net/bar/download/order
订单接口，强制跳 platform=nxgp 登录页，静态直链已不可直接下载。本脚本用
Playwright 加载已保存的 storage_state，模拟真人点击下载按钮并接收下载事件。

用法：
    python scripts/download_pdf.py <paper_id>
    python scripts/download_pdf.py                 # 不带参数，用内置示例 ID

若详情页跳登录页：请在弹出的浏览器里完成登录，然后点右上角绿色按钮
"已登录/下载完成，点此继续"，脚本会自动重试下载。登录态会回写到本机。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnki_paper_api import config  # noqa: E402
from cnki_paper_api.constants import UA  # noqa: E402

DONE_FLAG = "__cnki_dl_done__"
DEFAULT_ID = "17920bd59c07"


def safe_filename(title: str, maxlen: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:maxlen] or "paper") + ".pdf"


def inject_button(page, reason: str) -> None:
    js = """
    (() => {
      if (document.getElementById('__cnki_dl_done_btn')) return;
      const btn = document.createElement('button');
      btn.id = '__cnki_dl_done_btn';
      btn.textContent = '✅ 已登录/下载完成，点此继续';
      Object.assign(btn.style, {
        position:'fixed', top:'12px', right:'12px', zIndex:'2147483647',
        padding:'12px 18px', fontSize:'15px', fontWeight:'bold', color:'#fff',
        background:'#1a7f37', border:'none', borderRadius:'8px', cursor:'pointer',
        boxShadow:'0 2px 12px rgba(0,0,0,.35)'
      });
      btn.onclick = () => { window['%s'] = true; };
      document.body && document.body.appendChild(btn);
    })();
    """ % DONE_FLAG
    try:
        page.evaluate(js)
    except Exception:
        pass


def main() -> int:
    from playwright.sync_api import sync_playwright

    pid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ID
    papers_file = config.PAPERS_DB_PATH
    papers = {}
    if papers_file.exists():
        papers = json.loads(papers_file.read_text(encoding="utf-8"))

    paper = papers.get(pid)
    if not paper:
        print(f"[!] 临时论文库里没有 {pid}，请先调用一次 /search 写入。")
        return 2

    detail = paper.get("source_url", "")
    title = paper.get("title", pid)
    if not detail:
        print("[!] 该论文没有详情页 URL。")
        return 2

    out_dir = ROOT / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / safe_filename(title)
    print(f"[下载] 目标：{title}")
    print(f"[下载] 保存至：{out_path}")

    state_path = config.STORAGE_STATE_PATH
    if not state_path.exists():
        print("[!] 没有登录态，请先调用 POST /session/refresh 完成知网登录。")
        return 2

    captured: dict = {}

    def on_download(d) -> None:
        captured["download"] = d

    timeout_s = 300
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(state_path),
            accept_downloads=True,
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        ctx.on("page", lambda p: p.on("download", on_download))

        page = ctx.new_page()
        page.on("download", on_download)
        page.goto(detail, wait_until="domcontentloaded", timeout=60000)

        def click_download() -> bool:
            for sel in ["a#pdfDown", "li.btn-dlpdf a", "a[name=pdfDown]"]:
                try:
                    page.click(sel, timeout=6000)
                    return True
                except Exception:
                    continue
            return False

        clicked = click_download()
        print(f"[下载] 已点击下载按钮：{clicked}；当前页面：{page.url[:90]}")

        deadline = time.time() + timeout_s
        last_retry = 0.0
        saved = False
        while time.time() < deadline:
            d = captured.get("download")
            if d is not None:
                try:
                    d.save_as(str(out_path))
                    size = out_path.stat().st_size
                    head = out_path.open("rb").read(4)
                    print(f"[下载] 已保存 {size} 字节，文件头={head!r}，PDF={head == b'%PDF'}")
                    saved = head == b"%PDF"
                    if saved:
                        break
                except Exception as exc:
                    print(f"[下载] 保存失败：{exc}")
                captured.clear()

            # 弹出提示按钮，等用户完成登录/点下载
            try:
                inject_button(page, "PDF 下载")
                if page.evaluate(f"window['{DONE_FLAG}'] === true"):
                    page.evaluate(f"window['{DONE_FLAG}'] = false")
                    now = time.time()
                    if now - last_retry > 3:
                        last_retry = now
                        print("[下载] 收到继续信号，重新打开详情页并重试…")
                        page.goto(detail, wait_until="domcontentloaded", timeout=60000)
                        click_download()
            except Exception:
                pass
            page.wait_for_timeout(1000)

        # 无论成功与否，把浏览器里的最新会话回写（nxgp 登录产生的新 cookie）
        try:
            state = ctx.storage_state()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[下载] 会话已更新：{len(state.get('cookies', []))} 个 Cookie")
        except Exception as exc:
            print(f"[下载] 会话回写失败：{exc}")

        browser.close()

    if saved:
        print(f"[完成] {out_path}")
        return 0
    print("[未完成] 超时或未能下载。请确认已在浏览器里完成 nxgp 登录并点过下载按钮，然后重试。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
