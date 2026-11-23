"""知网接口的常量：URL、检索字段码、排序码、页面识别特征。"""
from __future__ import annotations

GRID_URL = "https://kns.cnki.net/kns8s/brief/grid"
SEARCH_PAGE = "https://kns.cnki.net/kns8s/search"
VERIFY_HOME = "https://kns.cnki.net/verify/home"

# 检索字段码（知网前端 data-val 实抓，2026-10）
FIELD_LABEL = {
    "SU": "主题", "TKA": "篇关摘", "KY": "关键词", "TI": "篇名", "FT": "全文",
    "AU": "作者", "FI": "第一作者", "RP": "通讯作者", "AF": "作者单位",
    "FU": "基金", "AB": "摘要", "CO": "小标题", "RF": "参考文献",
    "CLC": "分类号", "LY": "文献来源", "DOI": "DOI",
}

# 排序码（知网结果页 data-sort 实抓）
# 注意：relevance(FFD) 首页不传排序参数，由知网按检索词相关度默认排序
SORT_CODES = {
    "relevance": "FFD",   # 相关度
    "time": "PT",         # 发表时间（默认）
    "cited": "CF",        # 被引
    "download": "DFR",    # 下载
    "overall": "ZH",      # 综合
}

# 翻页请求 QueryJson.Products 固定规格（前端 SPA 实抓）
PAGE2_PRODUCTS = (
    "CJFQ,CAPJ,ZHYX,CJTL,CDFD,CMFD,WBFD,CPFD,IPFD,CCND,"
    "SCSF,SCHF,SCSD,SNAD,CCJD,CJFN,CCVD"
)

# 跨库检索的代码串（总库）
KUA_KU_CODE = (
    "YSTT4HG0,LSTPFY1C,EMRPGLPA,JUP3MUPD,MPMFIG1A,WQ0UVIAA,"
    "BLZOG7CK,PWFIRAGL,NN3FJMUV,NLBO1Z6R"
)

# 数据库代码 -> 中文名（用于把 td.data 的英文码翻译得更友好）
DB_NAMES = {
    "CJFQ": "中国学术期刊", "CAPJ": "中国学术期刊", "CJFN": "期刊",
    "CDFD": "博士学位论文", "CMFD": "硕士学位论文", "WBFD": "外文文献",
    "CPFD": "会议论文", "IPFD": "国际会议", "CCND": "中国会议",
    "SCSF": "社会科学", "SCHF": "世纪工程", "SCSD": "科技",
    "SNAD": "年鉴", "CCJD": "工具书", "CCVD": "视频",
    "ZHYX": "挚研", "CJTL": "期刊 tablets",
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 知网每页固定条数（pageSize 不可改，应用层用切片映射用户要的 page_size）
CNKI_PAGE_SIZE = 20
MAX_USER_PAGE_SIZE = 20
DEFAULT_USER_PAGE_SIZE = 10

# 触发验证的特征：响应 JSON 的 code == -403，message 里带 captchaType=...blockPuzzle...
WAF_CODE = -403
# 软封特征：HTTP 200 但正文含"暂无数据"且没有结果表格
SOFT_BAN_TEXT = "暂无数据"
