# 知网论文检索 API（CNKI Paper Search API）

给 AI Agent 用的"论文事实查询"服务：**只返回知网真实检索到的论文**，每条带来源链接，从而杜绝 AI 对着不存在的论文大篇幅胡编。

---

## 一、它能做什么

- **分页检索**：`page` 指定第几页，`page_size`（1~20）指定每页多少条——不只是第一页。
- **多字段/多排序**：主题/篇名/关键词/作者等 16 字段；相关度/时间/被引/下载/综合 5 种排序。
- **批量查询**：一次传多个关键词，串行执行避免风控。
- **防幻觉设计**：每条论文带 `source_url`（知网详情页）和抓取时间；响应带 `total / has_more`，Agent 能判断是否还有更多，不把第一页当全部。
- **人工过滑块**：遇到验证码自动弹出可见浏览器，你手动滑一下就继续，**不自动破解**。会话存本机，后续复用。
- **本地缓存**：SQLite 缓存 1 小时，重启不丢，少打扰知网。
- **PDF 下载**：`/search` 结果带 `paper_id`，`/download?paper_id=xxx` 让服务端用浏览器复用登录态真实下载到本地 `downloads/`，返回文件路径与 sha256（上限 3 篇）。不返回无法直接使用的下载链接。临时论文库 start/stop 自动清空。
- **多种接入**：HTTP API（所有 Agent 通用）+ Swagger 文档 + MCP Server + curl/Python 示例。

## 二、目录结构

```
论文ai/
├─ start.bat / stop.bat / status.bat / install.bat   # 双击即用
├─ openapi.json                 # OpenAPI 3.1 规范快照（供外部 Agent 导入，含 /download）
├─ scripts\.serverctl.py        # 进程管理（PID、端口检测、优雅停止）
├─ cnki_paper_api/
│  ├─ config.py  constants.py  models.py
│  ├─ api/
│  │  ├─ main.py       # FastAPI 端点
│  │  ├─ client.py      # 检索 / 分页 / 令牌 / 限速
│  │  ├─ session.py     # Cookie 持久化 + 人工验证
│  │  ├─ parser.py      # HTML 解析
│  │  ├─ cache.py       # SQLite 缓存
│  │  ├─ papers.py      # 临时论文库（papers.json，start/stop 清理）
│  │  └─ downloader.py  # 服务端真实下载（Playwright 点击 + 校验 + sha256）
│  ├─ mcp_server.py      # MCP 工具
│  ├─ run.py
│  └─ storage/           # 会话与缓存（含登录态，勿外传）
└─ examples/             # curl / Python 示例
```

## 三、安装与启动（Windows）

**前提**：Python **3.10+** 且安装时勾选了 "Add Python to PATH"；能联网（首次要下载依赖和 Chromium 约 100~200MB）；端口 8765 空闲。

1. 双击 `install.bat`：自动检查 Python、装依赖 + Playwright Chromium，并做环境自检。国内网络若下载慢/失败，脚本会提示换清华 pip 源和 Playwright 镜像。
2. 双击 `start.bat`：启动服务并自动打开接口文档 `http://127.0.0.1:8765/docs`。
3. **首次使用必须做这一步**：调用一次 `POST /session/refresh`，在弹出的浏览器里**先登录/过滑块，再点页面右上角绿色按钮"我已完成"**，会话即保存到本机。之后就不用再登录了。
4. 停止：双击 `stop.bat`；查看状态：`status.bat`。
5. 随时可手动自检：`python scripts/preflight.py post`。

### 换一台电脑部署

代码里**没有硬编码路径**，整个文件夹复制到任意 Windows 电脑即可，但注意：

- **每台电脑都要单独登录一次知网**（第 3 步）。登录态存在 `cnki_paper_api/storage/cnki_state.json`，含你的 Cookie，**不会也不应该随代码分发**；它已在 `.gitignore` 里。
- 没有知网账号 / 机构 IP 权限的话，即使登录也可能查不全或查不到题录——这是数据源权限问题，不是服务问题。
- 发行版已清空 `storage/`：缓存、日志、PID、含 Cookie 的登录态全部删除。首次运行会自动重建空目录，每台电脑都需重新登录一次知网。
- 若那台电脑 Python 未加入 PATH，`install.bat` 第一步就会明确报错并提示，不会让你对着一堆英文报错猜。

**start/stop 脚本说明**（`scripts/.serverctl.py`，经闭环验证）：

- `start.bat`：在**独立控制台窗口**启动服务并写 PID 文件，最多等 15 秒确认端口就绪，随后自动打开接口文档。重复启动会提示"已在运行"。
- `stop.bat`：**优先按 PID 文件、并按端口 8765 反查 PID 双保险**，即使 PID 文件过期也能干净停止，并确认端口释放。  
  |  |  |  |  |  
  | --- | --- | --- | --- |
- 支持 `python scripts/.serverctl.py start|stop|status|restart`。
- 若提示"端口被占用但没有 PID 文件"，先跑一次 `stop.bat` 清理即可。

> 服务只监听 `127.0.0.1`（本机），不需要认证，外部机器访问不到，Cookie 不出本机。

## 四、接口一览

| 方法   | 路径                 | 说明                               |
| ---- | ------------------ | -------------------------------- |
| GET  | `/search`          | 单关键词分页检索（核心）                     |
| GET  | `/batch`           | 多关键词批量检索                         |
| GET  | `/download`        | 按 paper_id 服务端真实下载 PDF 到本机（≤3 篇） |
| POST | `/papers/clear`    | 清空临时论文库                          |
| GET  | `/health`          | 健康检查（含临时库条数）                     |
| GET  | `/fields`          | 字段与排序码表                          |
| POST | `/session/refresh` | 弹浏览器登录/过滑块                       |
| POST | `/session/reset`   | 清除登录态                            |

### PDF 下载：`/download`

`/search` 返回的每条论文都带 `paper_id`（内容 hash，稳定）。`/download` 让服务端用浏览器复用登录态真实点击下载，把文件保存到本机 `downloads/`：

```bash
curl "http://127.0.0.1:8765/download?paper_id=a1b2c3d4e5f6"
# 批量：重复 paper_id，最多 3 个
curl "http://127.0.0.1:8765/download?paper_id=aaa&paper_id=bbb&paper_id=ccc"
```

`status=saved` 时返回 `file` 对象：`path`（本机绝对路径）、`filename`、`size_bytes`、`sha256`、`is_pdf`（文件头已校验为 `%PDF`）。同步等待，批量上限 **3 篇**。其他 `status`：`need_login`（下载平台未授权，需先 `POST /session/refresh`）、`no_pdf`（无 PDF 下载按钮）、`not_found`（ID 已过期，重新 `/search`）、`error`（如实报告，不假装成功）。

> **为什么不返回下载链接**：知网 2026 年 PDF 下载已改为 `bar.cnki.net/bar/download/order` 订单接口，强制跳 `platform=nxgp` 登录。直接把该链接交给浏览器打开会报 **「来源应用不正确(01)」**，普通 HTTP GET 只会拿到登录页 HTML——它不是可直接下载的 PDF。因此本接口不提供 info 探测模式，只做一件事：服务端用浏览器模拟真人点击下载按钮，把真实文件落盘到 `downloads/`。详情页请求间随机等 2~4 秒。**请仅用于你已获授权访问的文献、个人学术研究用途，遵守知网服务条款。**

### `/search` 参数

| 参数          | 默认          | 说明                                                         |
| ----------- | ----------- | ---------------------------------------------------------- |
| `keyword`   | 必填          | 检索词                                                        |
| `field`     | `SU`        | 字段码：`SU`主题 `TI`篇名 `KY`关键词 `AU`作者 等 16 种                    |
| `sort`      | `relevance` | `relevance`相关度 `time`时间 `cited`被引 `download`下载 `overall`综合 |
| `page`      | `1`         | 页码，从 1 开始                                                  |
| `page_size` | `10`        | 每页条数，1~20                                                  |
| `expert`    | `false`     | true 时把 keyword 当作知网专业检索表达式                                |

详细示例见 `examples/curl_examples.md`、`examples/python_client.py`。

## 五、Agent 接入方式对比

| 方式                         | 适用                 | 怎么用                                                   |
| -------------------------- | ------------------ | ----------------------------------------------------- |
| **HTTP + OpenAPI**（默认，最通用） | 所有能发 HTTP 的 Agent  | 读 `/openapi.json` 自动生成调用，或直接按文档请求                     |
| **MCP Server**             | 支持 MCP 的 Agent     | 把 `cnki_paper_api/mcp_server.py` 配成 stdio MCP，工具自动被发现 |
| **curl / Python 示例**       | 你自己 / 快速测试         | 照抄 examples                                           |
| **WorkBuddy Skill**        | 在 WorkBuddy 里说人话触发 | 可基于 HTTP API 再包一层 Skill                               |

**没有单一方案覆盖所有 Agent**：HTTP 是底座，MCP/Skill 是在其上针对不同生态的封装。建议先确保 HTTP 通，再按需加 MCP。

**给别的 Agent 接入的两种方式（含下载）**：

1. **在线读规范**：服务启动后 `GET http://127.0.0.1:8765/openapi.json`，Agent 读后即可自动构造 `/search`（检索）与 `/download`（下载）调用——下载只需传 `paper_id`，返回 `file.path` 与 `file.sha256`，**没有 mode 参数、也没有下载网址**。
2. **离线读快照**：发行包根目录的 `openapi.json` 是同一份规范的静态副本，可直接喂给支持 OpenAPI 导入的 Agent/平台，无需先启动服务。服务代码变更后需重新导出：

```bash
python -c "import json; from cnki_paper_api.api.main import app; json.dump(app.openapi(), open('openapi.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)"
```

> 提示：无论哪种方式，`/download` 都要求先用 `/search` 拿到 `paper_id`；数据是本机服务，Agent 必须与服务运行在同一台电脑上（或自行改 `HOST` 并做好鉴权）。

MCP 配置示例（Claude Desktop / 支持 stdio MCP 的客户端）：

```json
{
  "mcpServers": {
    "cnki-paper": {
      "command": "python",
      "args": ["<替换为你的项目绝对路径>/cnki_paper_api/mcp_server.py"]
    }
  }
}
```

## 六、防幻觉的正确姿势（给 Agent 的规则）

1. **只引用 `papers` 数组里真实出现的论文**，绝不补全或编造不在列表中的条目。
2. 引用时带上 `title / authors / journal / date / source_url`，让用户可点击核对。
3. 看 `query.total` 和 `query.has_more`：若 `total` 远大于 `returned`，说明只看了第一页，**不要说"就这些"**，应继续翻页或说明"仅展示前 N 条"。
4. `notice` 会提示哪些字段知网列表页不提供（摘要/关键词/DOI 为空是正常的，不是数据缺失）。
5. 需要核验某篇论文是否存在：用标题在 `field=TI` 下检索，命中即存在，可用 `source_url` 佐证。
6. **下载 PDF**：只能用 `/search` 返回的 `paper_id` 调 `/download`，**绝不自己拼 URL 或猜地址**。服务会用浏览器真实下载到本地，成功后读 `file.path` 告诉用户本机路径，并用 `file.sha256` 佐证。不要、也无法从本接口拿到"下载链接"——知网链接直接打开会报「来源应用不正确(01)」。
7. 按 `status` 如实告知：`saved` 才是文件已落盘（带 `file.sha256`）；`need_login` 提示先 `POST /session/refresh`；`no_pdf` 说无 PDF；`not_found` 说 ID 已过期请重新检索；`error` 如实报告。任何情况都不要假装已下载成功、不要编造路径。

## 七、技术说明与已知约束

- **知网每页固定 20 条**（pageSize 不可改），且翻页依赖首页响应里的 `hidTurnPage` 令牌。本服务用"逻辑页切片"实现你要的 `page_size`：内部按 20 条抓取，再按你的 `page/page_size` 切出对应片段，跨页会自动拼接。
- **限流**：默认每次真实请求后随机等待 1~2 秒，可改 `.env` 的 `REQUEST_DELAY_MIN/MAX`。别调到 0。
- **软封**：知网可能返回 200 + "暂无数据"，服务会明确报错并提示 `/session/refresh`，不会假装没结果。
- **验证码**：只做"人工过验证"门控，不自动识别/破解滑块。检测依据：JSON `code=-403` 或页面含验证特征。
- **接口会变**：知网前端改版可能导致选择器/表单失效。解析层做了多选择器兜底，但仍可能需要更新 `parser.py` / `client.py`。
- **合规**：本工具只读取你有权访问的题录元数据；PDF 下载复用你自己的登录态、模拟真人点击，不绕过权限或付费墙。PDF 功能**仅用于你已获授权访问的文献**（如校园网/机构订阅）、个人学术用途，请遵守知网服务条款。
- **PDF 下载机制**：服务端用 Playwright 加载登录态、点击详情页下载按钮、接收下载事件保存到 `downloads/`，校验文件头 `%PDF` 并计算 sha256；每次请求间随机等 2~4 秒。报纸类等无 PDF 下载按钮时返回 `no_pdf`。
- **临时论文库** `storage/papers.json` 保存本次会话检索到的论文供 `/download` 按 ID 查找，**start/stop 时自动清空**，不会堆积。
- **登录态文件** `storage/cnki_state.json` 含你的 Cookie，**等同账号凭证，切勿外传或提交到代码仓库**。

## 八、常见问题

|   |   |
| - | - |

- **启动时报"端口被占用"**：说明 8765 被别的程序占了，改 `.env` 的 `PORT`，或 `netstat -ano | findstr :8765` 找到进程。
- **检索返回 VERIFICATION_REQUIRED**：说明需要登录，调 `POST /session/refresh` 在浏览器里完成验证。
- **返回空且提示"暂无数据"**：要么关键词确实无命中，要么被软封，先 `/session/refresh` 再试。
- **total 为 null**：知网改了总数元素的结构，解析没命中，但论文本身正常返回；可据此判断但不必强依赖。
