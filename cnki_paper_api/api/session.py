"""知网会话：Cookie 持久化 + 验证检测 + Playwright 有头浏览器人工过滑块。

设计要点（合规）：不自动破解验证码。检测到滑块时，拉起一个用户看得见的
Chromium，让用户自己完成；成功后把 storage_state（cookies + localStorage）
存到本机，后续请求复用。超时则抛明确错误，绝不假装成功。
"""
from __future__ import annotations

import json
import re
import threading
from typing import Optional

import requests

from .. import config
from ..constants import SEARCH_PAGE, UA, WAF_CODE
from .parser import looks_like_captcha


class CaptchaRequired(Exception):
    """需要人工完成滑块验证。"""

    def __init__(self, message: str = "需要人工完成滑块验证"):
        self.detail = message
        super().__init__(message)


class VerificationTimeout(Exception):
    """等待人工验证超时。"""


class CnkiSession:
    """线程安全的知网会话单例。"""

    def __init__(self) -> None:
        self._s = requests.Session()
        self._s.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self._lock = threading.RLock()
        self._verified = False
        self._load_state()

    # ---------- 会话持久化 ----------
    def _load_state(self) -> None:
        p = config.STORAGE_STATE_PATH
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for c in data.get("cookies", []):
                domain = c.get("domain") or ".cnki.net"
                self._s.cookies.set(c["name"], c["value"], domain=domain, path=c.get("path", "/"))
            self._verified = bool(data.get("cookies"))
        except Exception:
            # 会话文件损坏不应导致服务起不来，忽略即可
            self._verified = False

    def _save_state_from_browser(self, storage_state: dict) -> str:
        config.STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.STORAGE_STATE_PATH.write_text(
            json.dumps(storage_state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._s.cookies.clear()
        for c in storage_state.get("cookies", []):
            self._s.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain") or ".cnki.net",
                path=c.get("path", "/"),
            )
        self._verified = True
        return config.STORAGE_STATE_PATH.as_posix()

    def has_session(self) -> bool:
        return self._verified and config.STORAGE_STATE_PATH.exists()

    def state_path(self) -> str:
        return config.STORAGE_STATE_PATH.as_posix()

    # ---------- 验证检测 ----------
    @staticmethod
    def is_verification_response(resp: requests.Response) -> bool:
        """判断响应是否要求人工验证（JSON code=-403 或 HTML 拦截页）。"""
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype or resp.text.lstrip().startswith("{"):
            try:
                data = resp.json()
                if str(data.get("code")) == str(WAF_CODE):
                    return True
                msg = str(data.get("message", ""))
                if "captcha" in msg.lower() or "验证" in msg or "verify" in msg.lower():
                    return True
            except Exception:
                pass
        return looks_like_captcha(resp.text)

    # ---------- 人工过滑块 ----------
    _DONE_FLAG = "__cnki_verify_done__"

    def _inject_done_button(self, page, reason: str) -> None:
        """在页面右上角注入一个"完成验证"按钮，由用户明确点击确认。

        这样避免"页面一离开验证态就误判完成"的问题——登录页/搜索页本身
        不一定是验证页，用户可能还没登录成功。
        """
        import html as _html
        safe_reason = _html.escape(reason)
        js = f"""
        (() => {{
          if (document.getElementById('__cnki_done_btn')) return;
          const btn = document.createElement('button');
          btn.id = '__cnki_done_btn';
          btn.textContent = '✅ 我已完成登录/验证，点击继续';
          Object.assign(btn.style, {{
            position:'fixed', top:'12px', right:'12px', zIndex:'2147483647',
            padding:'12px 18px', fontSize:'15px', fontWeight:'bold',
            color:'#fff', background:'#1a7f37', border:'none', borderRadius:'8px',
            cursor:'pointer', boxShadow:'0 2px 12px rgba(0,0,0,.35)'
          }});
          const tip = document.createElement('div');
          tip.id = '__cnki_tip';
          tip.textContent = {json.dumps('知网论文API：请先在下方完成登录/滑块，再点右上角绿色按钮。原因：' + safe_reason, ensure_ascii=False)};
          Object.assign(tip.style, {{
            position:'fixed', top:'12px', left:'50%', transform:'translateX(-50%)',
            zIndex:'2147483647', padding:'10px 16px', fontSize:'14px',
            color:'#222', background:'#fffbe6', border:'1px solid #ffe58f',
            borderRadius:'8px', boxShadow:'0 2px 12px rgba(0,0,0,.2)'
          }});
          btn.onclick = () => {{ window['{self._DONE_FLAG}'] = true; }};
          document.body.appendChild(btn);
          document.body.appendChild(tip);
        }})();
        """
        try:
            page.evaluate(js)
        except Exception:
            pass

    def _launch_human_verification(self, url: str, reason: str) -> dict:
        """启动有头 Chromium，让用户手动完成验证，返回 storage_state。

        在独立线程里跑 playwright；用户在页面点"完成"按钮后保存 storage_state。
        主线程轮询等待，超时抛明确错误。绝不自动破解验证码。
        """
        from playwright.sync_api import sync_playwright  # 延迟导入，未安装也能启动服务

        result: dict = {}
        error: dict = {}

        target = url if url and url.startswith("http") else SEARCH_PAGE

        def worker() -> None:
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=config.HEADLESS)
                    context = browser.new_context(
                        viewport={"width": 1280, "height": 800},
                        locale="zh-CN",
                        user_agent=UA,
                    )
                    page = context.new_page()
                    page.goto(target, wait_until="domcontentloaded", timeout=60000)
                    page.evaluate(f"window['{self._DONE_FLAG}'] = false;")

                    deadline_ms = config.VERIFY_TIMEOUT_SECONDS * 1000
                    elapsed = 0
                    step = 500
                    while elapsed < deadline_ms:
                        try:
                            # 每次循环重新注入按钮（应对页面跳转导致按钮丢失）
                            self._inject_done_button(page, reason)
                            done = page.evaluate(f"window['{self._DONE_FLAG}'] === true")
                            if done:
                                break
                        except Exception:
                            pass
                        page.wait_for_timeout(step)
                        elapsed += step

                    state = context.storage_state()
                    result["state"] = state
                    context.close()
                    browser.close()
            except Exception as exc:  # noqa: BLE001
                error["exc"] = exc

        t = threading.Thread(target=worker, name="cnki-human-verify", daemon=True)
        t.start()
        t.join(timeout=config.VERIFY_TIMEOUT_SECONDS + 20)
        if t.is_alive():
            raise VerificationTimeout(
                f"等待人工验证超过 {config.VERIFY_TIMEOUT_SECONDS} 秒（原因：{reason}）。"
                "请在弹出的浏览器里完成登录/滑块，并点击右上角绿色按钮。"
            )
        if error:
            raise CaptchaRequired(f"启动验证浏览器失败：{error['exc']}")
        state = result.get("state")
        if not state:
            raise CaptchaRequired("未能取得浏览器会话状态，请重试 /session/refresh")
        return state

    def ensure_verified(self, reason: str = "") -> None:
        """确保有可用会话；没有就弹浏览器让用户过验证。"""
        with self._lock:
            if self.has_session():
                return
            state = self._launch_human_verification(SEARCH_PAGE, reason or "首次使用需登录")
            self._save_state_from_browser(state)

    # ---------- 请求封装 ----------
    def post(self, url: str, *, data: dict, referer: str = SEARCH_PAGE,
             allow_verification: bool = True) -> requests.Response:
        """带会话的 POST。检测到验证时：若允许则弹浏览器等人过，再重放。"""
        with self._lock:
            resp = self._s.post(url, data=data, headers={"Referer": referer}, timeout=30)
            if self.is_verification_response(resp) and allow_verification:
                # 从响应里尽量提取知网给的验证跳转地址
                verify_url = self._extract_verify_url(resp)
                self._launch_and_adopt(verify_url, "检索被滑块拦截")
                resp = self._s.post(url, data=data, headers={"Referer": referer}, timeout=30)
            return resp

    def get(self, url: str, *, allow_verification: bool = True) -> requests.Response:
        with self._lock:
            resp = self._s.get(url, timeout=30)
            if self.is_verification_response(resp) and allow_verification:
                self._launch_and_adopt(self._extract_verify_url(resp), "页面被滑块拦截")
                resp = self._s.get(url, timeout=30)
            return resp

    def _launch_and_adopt(self, verify_url: str, reason: str) -> None:
        state = self._launch_human_verification(verify_url, reason)
        self._save_state_from_browser(state)

    @staticmethod
    def _extract_verify_url(resp: requests.Response) -> str:
        try:
            msg = str(resp.json().get("message", ""))
        except Exception:
            msg = resp.text
        m = re.search(r"(https?://[^\s\"'<>]*verify[^\s\"'<>]*)", msg)
        if m:
            return m.group(1)
        m = re.search(r"returnUrl=([^&\s\"']+)", msg)
        if m:
            from urllib.parse import unquote
            return unquote(m.group(1))
        return SEARCH_PAGE

    def refresh_session(self) -> dict:
        """手动刷新会话：弹浏览器让用户登录/过验，保存 storage_state。"""
        state = self._launch_human_verification(SEARCH_PAGE, "手动刷新会话")
        path = self._save_state_from_browser(state)
        return {"path": path, "cookie_count": len(state.get("cookies", []))}

    def invalidate(self) -> None:
        self._s.cookies.clear()
        self._verified = False
        if config.STORAGE_STATE_PATH.exists():
            config.STORAGE_STATE_PATH.unlink(missing_ok=True)


_session: Optional[CnkiSession] = None
_session_lock = threading.Lock()


def get_session() -> CnkiSession:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = CnkiSession()
    return _session
