"""Python 调用示例：httpx 调 HTTP API。运行前先启动服务并完成登录。"""
from __future__ import annotations

import json

import httpx

BASE = "http://127.0.0.1:8765"


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=180) as c:
        health = c.get("/health").json()
        print("health:", health)
        if not health.get("has_session"):
            print("尚无登录态，先调用 POST /session/refresh 并在弹出的浏览器里登录")

        r = c.get("/search", params={
            "keyword": "中药", "page": 1, "page_size": 10, "sort": "relevance",
        })
        data = r.json()
        if not data.get("success"):
            print("检索失败:", data)
            return

        q = data["query"]
        print(f"命中 {q['total']} 条，共 {q['total_pages']} 页，本页 {q['returned']} 条")
        for p in data["papers"]:
            print(f"[{p['n']}] {p['title']} | {p['authors']} | {p['journal']} {p['date']}")
            print(f"     被引 {p['cited']} | paper_id={p.get('paper_id')} | {p['source_url']}")

        # PDF：服务端真实下载到本机，成功后 file.path 是本机路径（仅用于已授权文献）
        if data["papers"]:
            pid = data["papers"][0]["paper_id"]
            result = c.get("/download", params=[("paper_id", pid)], timeout=180).json()
            for item in result.get("results", []):
                if item["status"] == "saved":
                    f = item["file"]
                    print(f"PDF/saved: {f['path']} | {f['size_bytes']} bytes | sha256={f['sha256'][:12]}")
                else:
                    print(f"PDF [{item['status']}]: {item.get('error')}")

        # 翻第 2 页
        page2 = c.get("/search", params={
            "keyword": "中药", "page": 2, "page_size": 10,
        }).json()
        print("第2页条数:", page2.get("query", {}).get("returned"))


if __name__ == "__main__":
    main()
