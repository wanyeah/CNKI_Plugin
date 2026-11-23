"""服务端真实 PDF 下载器。

知网 2026 年 PDF 下载走 bar.cnki.net/bar/download/order 订单接口，强制跳
platform=nxgp 登录，静态直链已不可直接 GET。本模块用 Playwright 加载已保存的
storage_state，模拟真人点击详情页的下载按钮，接收下载事件并保存到本地。

设计要点：
- 同步执行，单个请求内完成；批量由调用方控制（上限 config.PDF_SAVE_BATCH_MAX）
- 检测到登录页跳返回 need_login（不弹窗死等人工）；用户可先 POST /session/refresh
- 下载后校验文件头 %PDF、算 SHA-256；失败返回明确 status，绝不假装成功
- 线程安全：Playwright 全局串行（一个锁），避免多请求同时开浏览器触发风控
- 合规：只复用用户已合法获取的登录态，不破解验证、不绕过付费墙
"""
from __future__ import annotations

import hashlib
import random
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .. import config
from ..models import Paper, SavedFile, utcnow_iso

# Playwright 全局串行锁：同一时刻只允许一个下载任务
_BROWSER_LOCK = threading.Lock()


def _sleep_pdf_delay() -> None:
    """详情页请求之间随机等待，降低风控概率。"""
    time.sleep(random.uniform(config.PDF_DELAY_MIN, config.PDF_DELAY_MAX))


def safe_filename(title: str, maxlen: int = 80) -> str:
    """把论文标题清洗成安全文件名。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:maxlen] or "paper") + ".pdf"


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_pdf(path: Path) -> bool:
    try:
        return path.open("rb").read(4) == b"%PDF"
    except Exception:
        return False


class PdfDownloader:
    """用浏览器真实下载 PDF。"""

    def download(self, paper: Paper) -> dict:
        """下载单篇。返回 dict，含 status / file / error 等，绝不抛异常到调用方。"""
        result = {
            "status": "error",
            "file": None,
            "error": None,
            "fetched_at": utcnow_iso(),
        }
        state_path = config.STORAGE_STATE_PATH
        if not state_path.exists():
            result["status"] = "need_login"
            result["error"] = "没有登录态。请先 POST /session/refresh 完成知网登录。"
            return result
        if not paper.source_url:
            result["error"] = "该论文没有详情页 URL，无法下载。"
            return result

        # 限速：与详情页探测保持一致，降低风控概率
        _sleep_pdf_delay()

        with _BROWSER_LOCK:
            return self._download_locked(paper, state_path, result)

    def _download_locked(self, paper: Paper, state_path: Path, result: dict) -> dict:
        from playwright.sync_api import sync_playwright

        out_dir = config.DOWNLOAD_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / safe_filename(paper.title or paper.paper_id)

        captured: dict = {}

        def on_download(d) -> None:
            captured["download"] = d

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=config.PDF_SAVE_HEADLESS)
                try:
                    ctx = browser.new_context(
                        storage_state=str(state_path),
                        accept_downloads=True,
                        user_agent=_ua(),
                        viewport={"width": 1280, "height": 900},
                        locale="zh-CN",
                    )
                    ctx.on("page", lambda p: p.on("download", on_download))
                    page = ctx.new_page()
                    page.on("download", on_download)

                    try:
                        page.goto(paper.source_url, wait_until="domcontentloaded", timeout=60000)
                    except Exception as exc:
                        result["error"] = f"打开详情页失败：{exc}"
                        return result

                    # 检测是否被导向登录页（nxgp 未授权）
                    if "login.cnki.net" in (page.url or "").lower() or "verify" in (page.url or "").lower():
                        result["status"] = "need_login"
                        result["error"] = (
                            "知网下载平台要求登录（platform=nxgp）。请先 POST /session/refresh "
                            "在弹出的浏览器里完成登录，再重试下载。"
                        )
                        return result

                    # 点击 PDF 下载按钮
                    clicked = False
                    for sel in ["a#pdfDown", "li.btn-dlpdf a", "a[name=pdfDown]"]:
                        try:
                            page.click(sel, timeout=8000)
                            clicked = True
                            break
                        except Exception:
                            continue
                    if not clicked:
                        result["status"] = "no_pdf"
                        result["error"] = "详情页未找到 PDF 下载按钮（可能该文献仅有 CAJ 或无权限）。"
                        return result

                    # 等待下载事件（点击后可能跳登录页）
                    deadline = time.time() + config.PDF_SAVE_TIMEOUT_SECONDS
                    while time.time() < deadline:
                        d = captured.get("download")
                        if d is not None:
                            try:
                                d.save_as(str(out_path))
                            except Exception as exc:
                                result["error"] = f"保存文件失败：{exc}"
                                return result
                            break
                        # 点击后跳到登录页 → 需要人工授权
                        if "login.cnki.net" in (page.url or "").lower():
                            result["status"] = "need_login"
                            result["error"] = (
                                "点击下载后跳到知网登录页，下载平台需要单独授权。"
                                "请先 POST /session/refresh 完成登录（可在浏览器里点一次 PDF 下载按钮），再重试。"
                            )
                            return result
                        page.wait_for_timeout(500)
                    else:
                        result["error"] = "等待下载超时，可能触发风控或无下载权限。"
                        return result

                    # 回写会话（nxgp 登录可能产生新 cookie）
                    try:
                        state = ctx.storage_state()
                        state_path.write_text(
                            __import__("json").dumps(state, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
                finally:
                    browser.close()

            if not out_path.exists():
                result["error"] = "下载流程结束但未生成文件。"
                return result
            if not _is_pdf(out_path):
                result["status"] = "error"
                result["error"] = "下载到的文件不是有效 PDF（文件头非 %PDF，可能是登录页或错误页）。"
                try:
                    out_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return result

            result["status"] = "saved"
            result["file"] = SavedFile(
                path=out_path.as_posix(),
                filename=out_path.name,
                size_bytes=out_path.stat().st_size,
                sha256=_sha256_of(out_path),
                is_pdf=True,
            ).model_dump()
            return result
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"下载失败：{type(exc).__name__}: {exc}"
            return result


def _ua() -> str:
    from ..constants import UA
    return UA


_downloader: Optional[PdfDownloader] = None


def get_downloader() -> PdfDownloader:
    global _downloader
    if _downloader is None:
        _downloader = PdfDownloader()
    return _downloader
