# curl 调用示例（服务启动后）

Base URL：`http://127.0.0.1:8765`

## 0. 首次使用：打开浏览器登录 / 过滑块

```bash
curl -X POST http://127.0.0.1:8765/session/refresh
```

执行后会弹出 Chromium 窗口：先手动登录知网并完成滑块，然后**点击页面右上角绿色按钮"✅ 我已完成登录/验证，点击继续"**，会话即保存到本机（5 分钟超时）。

## 1. 健康检查

```bash
curl http://127.0.0.1:8765/health
```

## 2. 检索（第 1 页，10 条）

```bash
curl "http://127.0.0.1:8765/search?keyword=%E4%B8%AD%E8%8D%AF"
```

中文需 URL 编码，`keyword=中药` → `keyword=%E4%B8%AD%E8%8D%AF`。

## 3. 翻页（第 2 页，每页 20 条）

```bash
curl "http://127.0.0.1:8765/search?keyword=%E4%B8%AD%E8%8D%AF&page=2&page_size=20"
```

## 4. 按篇名检索、按被引排序

```bash
curl "http://127.0.0.1:8765/search?keyword=%E5%A4%A7%E8%AF%AD%E8%A8%80%E6%A8%A1%E5%9E%8B&field=TI&sort=cited&page=1&page_size=10"
```

## 5. 批量检索两个关键词

```bash
curl "http://127.0.0.1:8765/batch?keywords=%E4%B8%AD%E8%8D%AF&keywords=%E9%92%88%E7%81%B8&page_size=10"
```

## 6. PDF 下载（服务端真实下载到本地）

先从 `/search` 结果里拿到某篇论文的 `paper_id`。知网 2026 年下载走订单接口，链接直接打开会报「来源应用不正确(01)」，所以接口不返回下载网址，只由服务端用浏览器真实下载到本机：

```bash
curl "http://127.0.0.1:8765/download?paper_id=a1b2c3d4e5f6"
# 批量最多 3 个
curl "http://127.0.0.1:8765/download?paper_id=aaa&paper_id=bbb&paper_id=ccc"
```

`status=saved` 时返回本地文件信息：

```json
{
  "success": true, "requested": 1,
  "results": [{
    "paper_id": "a1b2c3d4e5f6", "title": "论文标题", "status": "saved",
    "can_download": true, "session_ready": true,
    "file": {
      "path": "D:/.../downloads/论文标题.pdf",
      "filename": "论文标题.pdf", "size_bytes": 1099112,
      "sha256": "a1b2...", "is_pdf": true
    }
  }]
}
```

> 状态说明：`saved` 才是文件已落盘（用 `file.path` 告知用户、`file.sha256` 校验）。`no_pdf` 该类无 PDF（报纸仅 CAJ）或无下载按钮；`need_login` 需先 `POST /session/refresh`；`not_found` ID 过期需重新检索；`error` 其他失败。**接口不返回下载链接，只有本地文件路径。请仅下载你已获授权访问的文献。**

## 7. 清空临时论文库

```bash
curl -X POST http://127.0.0.1:8765/papers/clear
```

## 8. 列出字段与排序码

```bash
curl http://127.0.0.1:8765/fields
```

## 9. 清除登录态（下次重新验证）

```bash
curl -X POST http://127.0.0.1:8765/session/reset
```

## 响应结构速览

```json
{
  "success": true,
  "query": {
    "keyword": "中药", "field": "SU", "field_label": "主题",
    "sort": "relevance", "page": 1, "page_size": 10,
    "total": 12345,        // 知网命中总数；null 表示未解析到
    "total_pages": 1235,   // 按 page_size 估算
    "returned": 10,
    "has_more": true,      // Agent 判断是否继续翻页
    "fetched_at": "...", "from_cache": false
  },
  "papers": [
    {
      "n": 1, "title": "...", "authors": "...", "journal": "...",
      "date": "2023", "cited": "12", "db": "中国学术期刊",
      "source_url": "https://kns.cnki.net/kcms2/article/abstract?v=...",
      "paper_id": "a1b2c3d4e5f6",
      "abstract": "", "keywords": "", "doi": ""
    }
  ],
  "notice": "知网列表页不提供摘要/关键词/DOI，已留空；如需全文信息请到 source_url 核对。"
}
```

Agent 防幻觉要点：只引用 `papers` 里真实返回的条目；引用时带上 `source_url`；
若 `query.total` 很大而 `returned` 很少，说明只看了第一页，不要臆断全貌。
