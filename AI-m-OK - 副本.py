"""
AI'm OK v3.2 — 每日 AI 资讯抓取、HTML 生成与飞书推送脚本
================================================================================
修复内容（v3.2 新增）：
  1. 修复日期伪造问题：scrape_links_from_page 和 _scrape_jiqizhixin 不再将
     所有抓取到的文章日期强制设为当天，改为从 URL 中提取真实发布日期。
  2. 日期缺失处理：当无法确定文章发布日期时，默认跳过该条新闻，不再静默放行。
  3. 新增 extract_date_from_url() 工具函数，支持从 URL 路径中提取日期。
================================================================================
历史修复：
  v3.1: 严格拦截非新闻链接、网页底部特征过滤器、强化抓取清洗逻辑。
  v3.0: 新增文章正文抓取、强化反幻觉 Prompt、新增摘要事实校验。
  v2.9: 严格禁止特定产品网站、72小时时效、隔日去重、HN映射修复。
"""

import json
import os
import random
import re
import sqlite3
import subprocess
import time
import shutil
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from html import escape, unescape
from pathlib import Path
from urllib.parse import quote_plus, urlparse, urljoin, parse_qs, unquote
from email.utils import parsedate_to_datetime

import feedparser
import requests

import sys
try:
    from review_server import start_review_server
except Exception:
    start_review_server = None

# Windows GBK 控制台下避免 emoji 输出导致崩溃
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

_orig_print = print
def print(*args, **kwargs):  # type: ignore[override]
    safe_args = []
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    for a in args:
        s = str(a)
        if "gbk" in enc:
            s = s.encode("gbk", errors="ignore").decode("gbk", errors="ignore")
        safe_args.append(s)
    _orig_print(*safe_args, **kwargs)

# ══════════════════════════════════════════════════════════════════════════════
# 配置区域（建议敏感信息迁移至环境变量）
# ══════════════════════════════════════════════════════════════════════════════


FEISHU_WEBHOOKS = os.environ.get(
    "FEISHU_WEBHOOKS",
    "https://open.feishu.cn/open-apis/bot/v2/hook/30bd0594-8318-4475-9f34-e0ed5a65de00,https://open.feishu.cn/open-apis/bot/v2/hook/c16acbb8-5615-451e-9465-8321f70e8646"
).split(",")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAwesMzAFIU45qjxw0ISW92L-ufU4tFG78")
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3:14b"

OUTPUT_DIR = Path.home()
DEFAULT_PAGES_CANDIDATES = [
    Path(r"F:\jiangxy2\AI-m-OK"),
    Path.home() / "AI-m-OK",
]
PAGES_DIR = Path(
    os.environ.get(
        "PAGES_DIR",
        next((str(p) for p in DEFAULT_PAGES_CANDIDATES if p.exists()), str(DEFAULT_PAGES_CANDIDATES[0])),
    )
)
PAGES_URL = "https://twinkleshinya.github.io/AI-m-OK"
HISTORY_FILE = PAGES_DIR / "push_history.json"
STATE_DIR = Path(os.environ.get("AIM_OK_STATE_DIR", str(Path.home() / ".aim_ok")))
REVIEW_FEEDBACK_FILE = STATE_DIR / "review_feedback.jsonl"
REVIEW_FEEDBACK_MAX_ROWS = int(os.environ.get("REVIEW_FEEDBACK_MAX_ROWS", "4000"))
POOL_A_MIN_PUSH = int(os.environ.get("POOL_A_MIN_PUSH", "10"))

# ── 数量与多样性约束 ──
MAX_ITEMS = 26
MIN_ITEMS = 16
HN_TOP_N = 50
MAX_PER_SOURCE = 3
MIN_SOURCES = 5
DOMESTIC_RATIO_MIN = 0.35
DOMESTIC_RATIO_MAX = 0.55
MIN_DOMESTIC_SUCCESS = 2
MIN_INTL_SUCCESS = 3
DEFAULT_CONTENT_MAX_AGE_DAYS = int(os.environ.get("DEFAULT_CONTENT_MAX_AGE_DAYS", "5"))
OLD_NEWS_HOURS = DEFAULT_CONTENT_MAX_AGE_DAYS * 24
MAX_FUNDING_POLICY = 2
PRODUCT_HEAT_THRESHOLD = 90
FEISHU_TOP_N = 15

# ── 实用导向筛选（v3.3） ──
PRACTICAL_STRICT_ONLY = os.environ.get("PRACTICAL_STRICT_ONLY", "1").strip().lower() not in {"0", "false", "no"}
PRACTICAL_MIN_SCORE = int(os.environ.get("PRACTICAL_MIN_SCORE", "2"))
VIDEO_MAX_AGE_DAYS = int(os.environ.get("VIDEO_MAX_AGE_DAYS", "7"))
YOUTUBE_MAX_AGE_DAYS = int(os.environ.get("YOUTUBE_MAX_AGE_DAYS", "7"))
AUDIO_AI_SPECIAL_MAX_AGE_DAYS = int(os.environ.get("AUDIO_AI_SPECIAL_MAX_AGE_DAYS", "7"))

# ── 社媒/视频抓取源配置（v3.3） ──
RSSHUB_BASES = [
    x.strip().rstrip("/")
    for x in os.environ.get("RSSHUB_BASES", "https://rsshub.app").split(",")
    if x.strip()
]

# 支持直接传入 YouTube 官方 feed 地址（推荐：https://www.youtube.com/feeds/videos.xml?channel_id=xxx）
YOUTUBE_FEED_URLS = [
    x.strip()
    for x in os.environ.get("YOUTUBE_FEED_URLS", "").split(",")
    if x.strip()
]

DIRECT_TUTORIAL_FEEDS = [
    "https://github.blog/feed/",
    "https://openai.com/news/rss.xml",
    "https://developer.chrome.com/static/blog/feed.xml",
    "https://developer.nvidia.com/blog/feed/",
]

DISCOVERABLE_TUTORIAL_PAGES = [
    "https://replicate.com/blog",
    "https://elevenlabs.io/blog",
    "https://developers.googleblog.com/",
    "https://blog.google/technology/ai/",
    "https://blog.langchain.com/",
    "https://blog.n8n.io/",
    "https://dify.ai/blog",
    "https://www.pinecone.io/learn/",
    "https://weaviate.io/blog",
    "https://www.llamaindex.ai/blog",
    "https://www.crewai.com/blog",
    "https://vercel.com/blog",
    "https://www.anthropic.com/news",
]

AGENT_CODING_FEEDS = [
    "https://github.blog/feed/",
    "https://openai.com/news/rss.xml",
]

AGENT_CODING_PAGES = [
    "https://openai.com/news",
    "https://www.anthropic.com/news",
    "https://blog.langchain.com/",
    "https://www.llamaindex.ai/blog",
    "https://dify.ai/blog",
    "https://blog.n8n.io/",
    "https://www.crewai.com/blog",
    "https://www.pinecone.io/learn/",
    "https://weaviate.io/blog",
    "https://vercel.com/blog",
]

AUDIO_CREATOR_FEEDS = [
    "https://developer.nvidia.com/blog/feed/",
]

WECHAT_SOURCE_NAME = "微信公众号"
WECHAT_OFFICIAL_ACCOUNTS = [
    x.strip()
    for x in os.environ.get(
        "WECHAT_OFFICIAL_ACCOUNTS",
        "摩丁创想,风亭韵律,audiokinetic官方,玫瑰细嗅蔷薇,智能科学与技术学报,AIGEL-人工智能绿色探索实验室,机器之心,量子位,新智元,腾讯研究院,腾讯云开发者,阿里云,百度智能云,极客公园,InfoQ,AI寒武纪,甲子光年"
    ).split(",")
    if x.strip()
]
WECHAT_PRIORITY_ACCOUNTS = {
    "摩丁创想",
    "风亭韵律",
    "audiokinetic官方",
    "玫瑰细嗅蔷薇",
    "智能科学与技术学报",
    "AIGEL-人工智能绿色探索实验室",
}

AUDIO_CREATOR_PAGES = [
    "https://elevenlabs.io/blog",
    "https://replicate.com/blog",
    "https://suno.com/blog",
    "https://www.descript.com/blog",
    "https://runwayml.com/research",
    "https://www.unrealengine.com/en-US/blog",
    "https://unity.com/blog",
    "https://developer.nvidia.com/blog",
]

NITTER_BASES = [
    x.strip().rstrip("/")
    for x in os.environ.get(
        "NITTER_BASES",
        "https://nitter.poast.org,https://nitter.privacydev.net,https://nitter.net"
    ).split(",")
    if x.strip()
]

JINA_READER_PREFIX = os.environ.get("JINA_READER_PREFIX", "https://r.jina.ai/http://").strip()

# ── v3.0 新增：文章正文抓取配置 ──
ARTICLE_EXCERPT_MAX_CHARS = 1200
ARTICLE_FETCH_TIMEOUT = 10
YTDLP_TIMEOUT = int(os.environ.get("YTDLP_TIMEOUT", "15"))
VIDEO_QUERY_LIMIT = int(os.environ.get("VIDEO_QUERY_LIMIT", "10"))
VIDEO_DOMAIN_LIMIT = int(os.environ.get("VIDEO_DOMAIN_LIMIT", "6"))
VIDEO_CANDIDATE_MAX = int(os.environ.get("VIDEO_CANDIDATE_MAX", "14"))

# ── 快速抓取模式：避免教程/博客源在网络慢时串行深挖导致卡住 ──
FAST_FETCH_MODE = os.environ.get("FAST_FETCH_MODE", "1").strip().lower() not in {"0", "false", "no"}
REQUEST_RETRIES = int(os.environ.get("REQUEST_RETRIES", "1" if FAST_FETCH_MODE else "2"))
RSS_FETCH_TIMEOUT = int(os.environ.get("RSS_FETCH_TIMEOUT", "6" if FAST_FETCH_MODE else "12"))
LISTING_FETCH_TIMEOUT = int(os.environ.get("LISTING_FETCH_TIMEOUT", "6" if FAST_FETCH_MODE else "12"))
LISTING_PAGE_LIMIT = int(os.environ.get("LISTING_PAGE_LIMIT", "5" if FAST_FETCH_MODE else "12"))
LISTING_ITEMS_PER_PAGE = int(os.environ.get("LISTING_ITEMS_PER_PAGE", "2" if FAST_FETCH_MODE else "4"))
GOOGLE_NEWS_QUERY_LIMIT = int(os.environ.get("GOOGLE_NEWS_QUERY_LIMIT", "4" if FAST_FETCH_MODE else str(VIDEO_QUERY_LIMIT)))
PRACTICAL_DOMAIN_LIMIT = int(os.environ.get("PRACTICAL_DOMAIN_LIMIT", "5" if FAST_FETCH_MODE else "14"))
AGENT_DOMAIN_LIMIT = int(os.environ.get("AGENT_DOMAIN_LIMIT", "5" if FAST_FETCH_MODE else "10"))
AUDIO_CREATOR_DOMAIN_LIMIT = int(os.environ.get("AUDIO_CREATOR_DOMAIN_LIMIT", "5" if FAST_FETCH_MODE else "8"))
AUDIO_MUSIC_DOMAIN_LIMIT = int(os.environ.get("AUDIO_MUSIC_DOMAIN_LIMIT", "5" if FAST_FETCH_MODE else "8"))
FRONTIER_DOMAIN_LIMIT = int(os.environ.get("FRONTIER_DOMAIN_LIMIT", "6" if FAST_FETCH_MODE else "11"))
DEEP_PAGE_DATE_IN_LISTING = os.environ.get("DEEP_PAGE_DATE_IN_LISTING", "0").strip().lower() in {"1", "true", "yes"}
WECHAT_SEARCH_QUERY_LIMIT = int(os.environ.get("WECHAT_SEARCH_QUERY_LIMIT", "8" if FAST_FETCH_MODE else "16"))
WECHAT_ENABLE_GOOGLE_NEWS = os.environ.get("WECHAT_ENABLE_GOOGLE_NEWS", "0" if FAST_FETCH_MODE else "1").strip().lower() in {"1", "true", "yes"}
WECHAT_ENABLE_RSSHUB = os.environ.get("WECHAT_ENABLE_RSSHUB", "0").strip().lower() in {"1", "true", "yes"}
WECHAT_ENABLE_SOGOU = os.environ.get("WECHAT_ENABLE_SOGOU", "1").strip().lower() in {"1", "true", "yes"}
WECHAT_ENABLE_BING = os.environ.get("WECHAT_ENABLE_BING", "1").strip().lower() in {"1", "true", "yes"}
WERSS_ENABLE = os.environ.get("WERSS_ENABLE", "1").strip().lower() in {"1", "true", "yes"}
WERSS_BASES = [
    x.strip().rstrip("/")
    for x in os.environ.get("WERSS_BASES", os.environ.get("WERSS_BASE_URL", "http://127.0.0.1:8001")).split(",")
    if x.strip()
]
WERSS_FEED_URLS = [
    x.strip()
    for x in os.environ.get("WERSS_FEED_URLS", "").split(",")
    if x.strip()
]
WERSS_USERNAME = os.environ.get("WERSS_USERNAME", os.environ.get("WERSS_API_USERNAME", "")).strip()
WERSS_PASSWORD = os.environ.get("WERSS_PASSWORD", os.environ.get("WERSS_API_PASSWORD", "")).strip()
WERSS_DISCOVERY_LIMIT = int(os.environ.get("WERSS_DISCOVERY_LIMIT", "30"))
DEFAULT_WERSS_SQLITE_CANDIDATES = [
    os.environ.get("WERSS_SQLITE_PATH", "").strip(),
    r"E:\jiangxy2\werss\data\db.db",
    str(Path.cwd() / "werss" / "data" / "db.db"),
    str(Path.cwd().parent / "werss" / "data" / "db.db") if Path.cwd().parent != Path.cwd() else "",
    str(Path.home() / "werss" / "data" / "db.db"),
]
WERSS_SQLITE_CANDIDATES = [
    Path(x)
    for x in dict.fromkeys(
        x.strip()
        for x in os.environ.get("WERSS_SQLITE_PATHS", ",".join(DEFAULT_WERSS_SQLITE_CANDIDATES)).split(",")
        if x and x.strip()
    )
]
REQUEST_THROTTLE_MIN = float(os.environ.get("REQUEST_THROTTLE_MIN", "1.0"))
REQUEST_THROTTLE_MAX = float(os.environ.get("REQUEST_THROTTLE_MAX", "2.0"))

# ── 北京时区 ──
BEIJING_TZ = timezone(timedelta(hours=8))

# ══════════════════════════════════════════════════════════════════════════════
# v3.0 新增：已知 AI 实体映射表（用于反幻觉校验）
# ══════════════════════════════════════════════════════════════════════════════

KNOWN_AI_ENTITIES = {
    # 公司 -> 旗下模型/产品
    "OpenAI": ["GPT", "ChatGPT", "DALL-E", "Sora", "o1", "o3", "o4-mini", "Codex"],
    "Anthropic": ["Claude", "Opus", "Sonnet", "Haiku"],
    "Google": ["Gemini", "Bard", "PaLM", "Gemma", "Veo", "Imagen"],
    "Meta": ["Llama", "LLaMA", "SAM", "NLLB", "Cicero"],
    "Mistral": ["Mistral", "Mixtral", "Pixtral"],
    "xAI": ["Grok"],
    "智谱AI": ["GLM", "ChatGLM", "CogView", "CogVideo"],
    "百度": ["文心一言", "ERNIE"],
    "阿里": ["通义千问", "Qwen"],
    "字节跳动": ["豆包", "Doubao", "即梦"],
    "月之暗面": ["Kimi", "Moonshot"],
    "DeepSeek": ["DeepSeek"],
    "百川智能": ["Baichuan"],
    "MiniMax": ["MiniMax", "海螺"],
    "零一万物": ["Yi"],
    "阶跃星辰": ["Step"],
    "Stability AI": ["Stable Diffusion", "SDXL"],
    "Midjourney": ["Midjourney"],
    "Nvidia": ["NeMo", "Nemotron"],
    "Microsoft": ["Copilot", "Phi"],
    "Apple": ["Apple Intelligence"],
    "Amazon": ["Titan", "Nova"],
}

# 反向映射：模型/产品关键词 -> 正确的公司
MODEL_TO_COMPANY = {}
for company, models in KNOWN_AI_ENTITIES.items():
    for model in models:
        MODEL_TO_COMPANY[model.lower()] = company

# ══════════════════════════════════════════════════════════════════════════════
# 关键词与过滤器
# ══════════════════════════════════════════════════════════════════════════════

AI_KEYWORDS = re.compile(
    r"artificial.intelligence|machine.learning|deep.learning"
    r"|llm|large.language|gpt.?[3-6]|openai|claude|anthropic|gemini|mistral"
    r"|chatgpt|diffusion|neural.net|transformer|generative.ai"
    r"|langchain|hugging.?face|stable.diffusion|midjourney"
    r"|ai.agent|ai.model|foundation.model|reasoning.model"
    r"|ai.chip|ai.video|ai.startup|ai.fund|ai.regul|ai.safety"
    r"|sora|dall.?e|copilot.ai|cursor.ai|ai.coding"
    r"|deepseek|qwen|glm|baichuan|moonshot|kimi|doubao|zhipu"
    r"|大模型|人工智能|机器学习|深度学习|智能体|具身智能",
    re.IGNORECASE,
)

AI_KEYWORDS_ZH = re.compile(
    r"AI|人工智能|大模型|机器学习|深度学习|神经网络|自然语言处理"
    r"|生成式|智能体|大语言模型|多模态|GPT|LLM|AIGC"
    r"|DeepSeek|通义|文心|豆包|星火|智谱|月之暗面|Kimi"
    r"|具身智能|机器人|自动驾驶|AI芯片|算力",
    re.IGNORECASE,
)

PAPER_FILTER = re.compile(
    r"arxiv\.org|preprint|theorem|equation|proof|journal\.of"
    r"|hamilton.jacobi|reinforcement.learning.and.diffusion"
    r"|mathematical.methods"
    r"|\.pdf(\?|$)"
    r"|/papers?/"
    r"|www-cdn\.anthropic\.com",
    re.IGNORECASE,
)

FALSE_POSITIVE_FILTER = re.compile(
    r"copilot.edited.an.ad|smart.eyeglasses|smart.glasses"
    r"|philly.courts|apple.watch",
    re.IGNORECASE,
)

# ── 增强版 GitHub 过滤器 ──
GITHUB_FILTER = re.compile(
    r"github\.com/[\w\-\.]+(?:/[\w\-\.]+)?"
    r"|gist\.github\.com"
    r"|raw\.githubusercontent\.com"
    r"|[\w\-]+\.github\.io",
    re.IGNORECASE,
)

GITHUB_TITLE_FILTER = re.compile(
    r"\bgithub\b", re.IGNORECASE
)

# GitHub 内容仅在“有明确使用说明/教程”时放行
GITHUB_USAGE_FILTER = re.compile(
    r"readme|quick.?start|get.?started|installation|install|usage|how.to|tutorial|guide|docs?"
    r"|demo|example|examples|sample|run|deploy|setup"
    r"|使用说明|快速开始|教程|上手|安装|部署|示例|文档|操作步骤|实战",
    re.IGNORECASE,
)

SOFT_AD_FILTER = re.compile(
    r"sponsored|广告|PR稿|合作伙伴推广|赞助|soft.?article|advertorial",
    re.IGNORECASE,
)

# ── ★ v3.1 新增：网页底部备案与非新闻文本过滤器 ──
FOOTER_TEXT_FILTER = re.compile(
    r"ICP备|公网安备|版权所有|All Rights Reserved|联系我们|关于我们|免责声明|隐私政策|使用条款|营业执照|增值电信业务|不良信息举报",
    re.IGNORECASE,
)

# ── 融资/政策类过滤器 ──
FUNDING_POLICY_FILTER = re.compile(
    r"fund|rais|invest|ipo|valuat|\$\d|billion|million|serie[s\s]"
    r"|融资|投资|估值|上市|收购|并购|股权|资本|风投|基金|财报|营收|利润|亏损|市值|股价|交易"
    r"|A轮|B轮|C轮|D轮|天使轮|种子轮|pre-A"
    r"|regulat|policy|govern|law|eu.ai|congress|senate|ban|court"
    r"|政策|监管|法规|合规|立法|审查|治理",
    re.IGNORECASE,
)

INVESTMENT_URL_FILTER = re.compile(
    r"/tech/(?:roll|money|stock|finance)/"
    r"|finance\.sina\.com\.cn"
    r"|ithome\.com/\d+/\d+/\d+\.htm",
    re.IGNORECASE,
)

# ── 产品官网/Landing Page URL 模式过滤器 ──
PRODUCT_LANDING_FILTER = re.compile(
    r"coze\.cn|coze\.com"
    r"|overview\?utm_"
    r"|/product[s]?[/\?]"
    r"|/pricing[/\?]"
    r"|/landing[/\?]"
    r"|/signup[/\?]"
    r"|/register[/\?]"
    r"|/download[/\?]"
    r"|/get-started"
    r"|/try-free"
    r"|/features[/\?]",
    re.IGNORECASE,
)

# ── 产品官网域名黑名单（非新闻类的产品官网） ──
PRODUCT_SITE_DOMAINS = re.compile(
    r"coze\.cn|coze\.com"
    r"|wawawriter\.com"
    r"|cursor\.com(?!/blog)"
    r"|notion\.so(?!/blog)"
    r"|midjourney\.com(?!/blog)"
    r"|poe\.com"
    r"|character\.ai(?!/blog)"
    r"|perplexity\.ai(?!/blog)"
    r"|claude\.ai(?!/blog)"
    r"|copilot\.microsoft\.com(?!/blog)"
    r"|chat\.openai\.com"
    r"|gemini\.google\.com(?!/blog)",
    re.IGNORECASE,
)

# ── ★ v3.1 增强：严格封禁的产品网页及非新闻链接 ──
HARD_BLOCK_DOMAINS = re.compile(
    r"wawawriter\.com"
    r"|claude\.com/blog/claude-managed-agents"
    r"|anthropic\.com/glasswing"
    r"|chatdesks\.cn"           # 拦截豆包等推广/客服分发链接
    r"|beian\.miit\.gov\.cn"    # 拦截工信部备案链接
    r"|cyberpolice\.cn"         # 拦截公安备案链接
    r"|gov\.cn"                 # 拦截纯政府通用域名（通常是底部外链）
    r"|ibiling\.cn"             # <--- 新增：拦截 ibiling 产品页
    r"|iflydocs\.com"           # <--- 新增：拦截讯飞文档产品页
    r"|/about-us"
    r"|/contact-us"
    r"|/privacy"
    r"|/terms",
    re.IGNORECASE,
)

# ── 非技术向内容过滤器（消费电子/汽车/影视/游戏促销等） ──
NON_TECH_FILTER = re.compile(
    r"汽车|新车|车型|轿车|SUV|电动车(?!.*AI)|混动|油耗|续航里程|4S店"
    r"|比亚迪(?!.*AI)|特斯拉(?!.*AI|FSD|自动驾驶)|极氪|蔚来(?!.*AI)|小鹏(?!.*AI)"
    r"|理想汽车|长城汽车|吉利汽车|广汽|一汽|东风汽车|奇瑞"
    r"|海豹|海鸥|宋PLUS|汉EV|秦PLUS|元PLUS"
    r"|电视剧|电影|首播|上映|票房|追剧|剧集|综艺|真人秀"
    r"|黑袍纠察队|漫威|DC|Netflix|Prime.Video|迪士尼\+"
    r"|动漫|番剧|声优|偶像"
    r"|游戏促销|史低|打折|限免|喜加一|Steam(?!.*AI)|Epic(?!.*AI)"
    r"|PS[45]|Xbox|Switch|任天堂"
    r"|键盘(?!.*AI)|鼠标(?!.*AI)|耳机(?!.*AI)|音箱(?!.*AI)|充电器|数据线|保护壳"
    r"|散热器(?!.*AI)|机箱|电源(?!.*AI|算力)|显示器(?!.*AI)"
    r"|镜头|相机(?!.*AI)|摄影器材"
    r"|开箱|跑分|拆解|手机壳|钢化膜|手机膜"
    r"|冰箱|洗衣机|空调(?!.*AI)|扫地机(?!.*AI)|净水器|电饭煲"
    r"|智能马桶|浴霸|油烟机"
    r"|套餐|流量卡|话费|宽带(?!.*AI)"
    r"|优惠券|红包|满减|秒杀|预售|双十一|618|年货节",
    re.IGNORECASE,
)

AI_EXEMPT = re.compile(
    r"AI|人工智能|大模型|智能驾驶|自动驾驶|FSD|智能座舱"
    r"|GPT|LLM|深度学习|机器学习|神经网络|智能体"
    r"|AIGC|生成式|Copilot|算力|AI芯片",
    re.IGNORECASE,
)

# ── 企业商务类新闻过滤器 ──
ENTERPRISE_BIZ_FILTER = re.compile(
    r"(?:出任|担任|任命|升任|离职|加盟|履新|接任).{0,10}(?:CTO|CEO|CFO|COO|CMO|总裁|副总裁|董事长|总经理|首席)"
    r"|(?:CTO|CEO|CFO|COO|总裁|副总裁|董事长|总经理).{0,6}(?:变动|调整|更替|换帅|离任)"
    r"|人事变动|组织架构调整|设立.*(?:委员会|事业部)|升级.*(?:组织架构|事业部)"
    r"|战略合作|达成合作|签约仪式|签署.*协议|合作备忘录"
    r"|共建.*(?:集群|中心|基地|平台|实验室)"
    r"|联合建设|携手.*打造|强强联合|生态合作|框架协议"
    r"|代码贡献.*(?:万行|百万行)"
    r"|推动.*生态发展|加速.*生态"
    r"|(?:万卡|千卡).*集群"
    r"|(?:智算|算力|数据)中心.*(?:建设|落地|启用|投产|揭牌)"
    r"|营收.*(?:增长|下降|同比|环比)|净利润|财报发布|业绩报告|中标|招标|采购",
    re.IGNORECASE,
)

PRACTICE_BOOST = re.compile(
    r"tutorial|how.to|实战|教程|部署|fine.?tun|微调|训练|推理|inference"
    r"|benchmark|评测|对比|测评|实测|体验|上手|接入|集成|API"
    r"|应用|落地|案例|场景|实践|工具|框架|pipeline|workflow"
    r"|agent|智能体|RAG|function.call|tool.use|prompt.engineer"
    r"|技术突破|breakthrough|SOTA|刷新|超越|性能提升",
    re.IGNORECASE,
)

TECH_BOOST = re.compile(
    r"breakthrough|突破|首次|首发|全球首|benchmark|SOTA|超越|刷新|纪录"
    r"|发布|launch|release|推出|上线|开源|open.?source",
    re.IGNORECASE,
)

HOT_ENTITY = re.compile(
    r"OpenAI|Google|Meta|Apple|Microsoft|Nvidia|DeepSeek"
    r"|百度|阿里|腾讯|字节|华为|GPT-5|Claude|Gemini",
    re.IGNORECASE,
)

# ── v3.3 实用导向强约束：强调“可学、可复用、可实践” ──
PRACTICAL_SIGNAL = re.compile(
    r"教程|实战|上手|复现|部署|接入|集成|案例|工作流|workflow|prompt"
    r"|agent|智能体|RAG|自动化|脚本|插件|plugin|api|sdk|tool|工具链"
    r"|开源|github|模板|template|复用|best.practice|how.to|guide|playbook"
    r"|benchmark|评测|对比|实践|落地|效率|提效|办公|生产力|运营|销售|客服|数据分析"
    r"|skill|skills|skillset|agentic|copilot|n8n|zapier|make\.com|dify|coze|metagpt|langflow|langgraph"
    r"|medrag|kag|trendradar|报表自动化|AI报表|智能客服|客服助手|工作区|workspace"
    r"|低代码|无代码|apidog|lynx|生成式搜索|copilot search|google ai overview|ai overview"
    r"|音频|播客|podcast|voice|配音|降噪|混音|母带|转写|ASR|TTS|DAW|VST|MIDI|采样",
    re.IGNORECASE,
)

REUSABLE_SIGNAL = re.compile(
    r"open.?source|repo|github|模板|template|脚手架|boilerplate|sdk|api|示例代码|代码仓库"
    r"|插件市场|workflow模板|automation模板|prompt模板|agent模板|工程模板"
    r"|低代码模板|无代码模板|RAG平台|知识库模板|工作区模板|客服模板|报表模板",
    re.IGNORECASE,
)

INNOVATION_SIGNAL = re.compile(
    r"新模型|模型发布|技术突破|新范式|架构创新|推理能力|多模态|agentic"
    r"|reasoning|benchmark|SOTA|state.of.the.art|latency|成本下降|效率提升"
    r"|发布|launch|release|introduce|rollout|本地部署|local deployment|私有化部署"
    r"|生成式搜索|智能搜索|检索增强|knowledge retrieval",
    re.IGNORECASE,
)

MODEL_SIGNAL = re.compile(
    r"model|llm|gpt|claude|gemini|qwen|deepseek|mistral|agent|多模态|大模型|推理模型",
    re.IGNORECASE,
)

APPLICATION_SIGNAL = re.compile(
    r"应用|场景|落地|部署|workflow|自动化|效率|生产力|集成|api|sdk|tool|agent|RAG|实战|教程|案例"
    r"|音频制作|播客制作|配音工作流|语音克隆|字幕转写|音频后期|音乐生成"
    r"|客服|助理|工作区|workspace|报表|数据分析|趋势分析|搜索摘要|低代码|无代码"
    r"|医疗|政务|知识库|决策支持|视觉生成|视频生成|营销内容",
    re.IGNORECASE,
)

LOW_VALUE_SIGNAL = re.compile(
    r"融资|估值|人事|任命|合作签约|生态合作|政策|监管|法案|诉讼|广告|赞助|带货"
    r"|明星|八卦|营销|发布会回顾|隐私体验|用户体验|privacy.led|privacy-led|trust.in.the.ai.era"
    r"|privacy-led.ux|privacy.led.ux|building.trust.in.the.ai.era|trust.*privacy.*ux",
    re.IGNORECASE,
)

SOCIAL_VIDEO_DOMAINS = re.compile(
    r"bilibili\.com|b23\.tv|youtube\.com|youtu\.be|weibo\.com|twitter\.com|x\.com"
    r"|vimeo\.com|dailymotion\.com|ted\.com|coursera\.org|udemy\.com|egghead\.io|frontendmasters\.com",
    re.IGNORECASE,
)

SOCIAL_PRACTICAL_QUERIES = [
    "AI 教程 实战 工作流",
    "AI 自动化 提效 案例",
    "AI Agent RAG 部署",
    "AI 开源 工具 复用",
    "LLM 应用 落地",
    "AI skill 教程",
    "AI agent workflow tutorial",
    "AI 音频 工作流 教程",
    "播客 AI 工具 实战",
    "配音 AI agent 工具",
    "Dify MetaGPT Langflow 教程",
    "MedRAG KAG RAG 平台 案例",
    "DeepSeek Qwen Claude 实战",
    "Google AI Overview Copilot Search 教程",
    "ChatGPT 插件 AI 工作区 实战",
    "Stable Diffusion Runway 教程",
    "TrendRadar AI 报表 工具",
    "Lynx Apidog AI 低代码 教程",
    "AI 智能客服 工作流",
    "AI 知识库 检索增强 案例",
    "OpenAI Anthropic developer guide",
    "LangChain LangGraph tutorial",
    "LlamaIndex RAG tutorial",
    "n8n AI automation tutorial",
    "Dify workflow tutorial",
    "CrewAI agent tutorial",
    "Pinecone Weaviate RAG guide",
    "AI coding assistant tutorial",
    "AI prompt workflow guide",
]

MODEL_INNOVATION_QUERIES = [
    "new AI model release benchmark",
    "reasoning model launch practical usage",
    "多模态 模型 发布 实践",
    "AI 技术突破 应用场景",
    "audio AI model release",
    "voice agent workflow",
    "DeepSeek Qwen Claude model tutorial",
    "RAG platform release workflow",
    "AI search summary workflow",
    "AI workspace assistant tutorial",
    "OpenAI model release",
    "Anthropic Claude release",
    "Google Gemini release",
    "DeepSeek Qwen model launch",
    "AI benchmark reasoning model",
    "AI research breakthrough model",
    "新模型 发布 大模型",
    "推理模型 发布",
    "多模态 模型 突破",
    "AI 技术 研发 进展",
]

AUDIO_MUSIC_GAME_QUERIES = [
    "AI audio workflow tutorial",
    "AI music production tutorial",
    "AI game development tutorial",
    "AI sound design workflow",
    "AI voice synthesis tutorial",
    "AI game agent tutorial",
    "AI Unity Unreal workflow",
    "AI 游戏 开发 教程",
    "AI 音频 生成 教程",
    "AI 音乐 制作 实战",
    "Runway video generation tutorial",
    "Stable Diffusion visual workflow",
    "ElevenLabs voice workflow tutorial",
    "Suno music workflow tutorial",
    "game AI tool tutorial",
]

AUDIO_AI_SPECIAL_QUERIES = [
    "AI audio production workflow",
    "AI voice cloning tutorial",
    "ASR TTS practical guide",
    "podcast AI workflow",
    "sound design AI tutorial",
    "music generation AI practical",
    "AI dubbing deployment guide",
    "AI mixing mastering workflow",
    "AI 配音 工作流 实战",
    "AI 音频 处理 教程",
    "语音识别 语音合成 实战",
    "播客 AI 工具 上手",
    "音频后期 AI 自动化",
    "声音设计 AI 案例",
    "Wwise AI audio workflow",
]

ORDINARY_HINT_TERMS = [
    r"AI\s*skill", r"AI\s*skills", r"AI\s*agent", r"AI\s*tutorial",
    r"AI\s*tool", r"AI\s*tools", r"AI\s*workflow", r"AI\s*automation",
    r"智能应用", r"智能体", r"AI工具", r"大模型", r"LLM", r"RAG", r"AIGC",
    r"Dify", r"MetaGPT", r"Langflow", r"LangGraph", r"KAG", r"MedRAG",
    r"DeepSeek", r"Qwen", r"Claude Code", r"Codex", r"OpenAI", r"Anthropic", r"Gemini",
    r"Google AI Overview", r"AI Overview", r"Copilot Search", r"TrendRadar", r"Runway", r"Stable Diffusion",
    r"ChatGPT 插件", r"AI工作区", r"智能客服", r"低代码", r"无代码", r"Lynx", r"Apidog",
    r"AI 音频", r"AI 音乐", r"AI 游戏", r"voice AI", r"music AI", r"game AI",
    r"语音生成", r"语音克隆", r"音乐生成", r"游戏开发AI", r"AI编程", r"浏览器AI",
]

REQUIRED_TERMS = [
    r"开源", r"案例", r"教程", r"指南", r"实战", r"工作流", r"workflow",
    r"部署", r"上手", r"集成", r"文档", r"示例", r"demo", r"example",
    r"github", r"repo", r"readme", r"quickstart", r"usage", r"guide",
    r"plugin", r"template", r"best practice", r"playbook", r"技能", r"技巧", r"流程", r"拆解",
    r"客服", r"搜索摘要", r"知识库", r"报表", r"低代码", r"无代码", r"工作区",
]

EXCLUDE_TERMS = [
    r"招聘", r"试用", r"内测", r"邀请码", r"注册", r"账号", r"购买", r"价格",
    r"代充", r"辅助挂", r"棋牌", r"博彩", r"优惠", r"折扣", r"注册码",
    r"融资", r"投资", r"估值", r"收购", r"并购", r"财报", r"营收", r"利润", r"股价",
    r"号商", r"批量购买", r"外挂", r"透视", r"麻将",
]

AI_CORE_TERMS = [
    r"\bAI\b", r"人工智能", r"大模型", r"\bLLM\b", r"GPT(?:-\d+)?", r"生成式", r"AIGC",
    r"AI\s*agent", r"智能体", r"agentic", r"RAG", r"多模态", r"reasoning",
    r"OpenAI", r"ChatGPT", r"Claude(?:\s*Code)?", r"Anthropic", r"Gemini", r"Gemma",
    r"DeepSeek", r"Qwen", r"Dify", r"LangChain", r"LangGraph", r"Langflow", r"MetaGPT",
    r"Copilot", r"Codex", r"Cursor", r"Coze", r"n8n", r"Zapier", r"Make\.com",
    r"MedRAG", r"KAG", r"Runway", r"Stable Diffusion", r"TrendRadar", r"Copilot Search",
    r"Google AI Overview", r"AI Overview", r"Lynx", r"Apidog", r"ChatGPT 插件", r"AI工作区",
    r"AI\s*音频", r"AI\s*音乐", r"AI\s*游戏", r"voice\s*AI", r"music\s*AI", r"game\s*AI",
    r"语音识别", r"语音克隆", r"语音合成", r"AI编程", r"浏览器AI", r"Gemini\s*Skills",
    r"\bASR\b", r"\bTTS\b", r"Veo", r"Sora", r"音频生成", r"音乐生成",
]

PRACTICE_REQUIRED_TERMS = [
    r"教程", r"指南", r"实战", r"案例", r"工作流", r"workflow", r"部署", r"上手", r"接入", r"集成",
    r"文档", r"示例", r"demo", r"example", r"examples", r"github", r"repo", r"readme",
    r"quickstart", r"usage", r"guide", r"how[\s\-]?to", r"plugin", r"template",
    r"playbook", r"best practice", r"技能", r"技巧", r"流程", r"拆解", r"\bAPI\b", r"\bSDK\b",
    r"\bCLI\b", r"脚本", r"自动化", r"automation", r"提示词", r"prompt", r"开源", r"复现",
    r"可复用", r"starter", r"工具", r"tool(?:s|ing)?", r"skills?", r"音频工作流", r"配音",
    r"播客", r"混音", r"母带", r"转写", r"字幕", r"Unity", r"Unreal",
    r"搜索摘要", r"知识库", r"报表", r"工作区", r"客服", r"低代码", r"无代码",
]

NON_ACTIONABLE_URL_FILTER = re.compile(
    r"/campaigns?/|/whitepaper|/ebook|/report|/research|/insights|/survey"
    r"|/webinar|/events?/|/summit|/landing/",
    re.IGNORECASE,
)

NON_ACTIONABLE_TEXT_FILTER = re.compile(
    r"white\s*paper|whitepaper|白皮书|研究报告|行业报告|趋势报告|洞察|insights|survey|调研"
    r"|report download|download the report|register to read|campaign|活动专题|品牌专题",
    re.IGNORECASE,
)

NON_PRACTICAL_NEWS_FILTER = re.compile(
    r"shooting|attack|incident|lawsuit|sued|controversy|scandal|rumor|allegation|arrest"
    r"|charged|home attack|security incident|breach|leak|death|killed|violence"
    r"|privacy-led\s+ux|building\s+trust\s+in\s+the\s+ai\s+era|trust\s+in\s+the\s+ai\s+era"
    r"|building-trust-in-the-ai-era-with-privacy-led-ux"
    r"|privacy\s+led\s+ux|隐私.*用户体验|用户体验.*隐私|AI时代.*信任|建立信任"
    r"|枪击|袭击|遇袭|起诉|诉讼|争议|丑闻|爆料|传闻|泄露|安全事故|身亡|暴力",
    re.IGNORECASE,
)

ORDINARY_HINT_PATTERN = re.compile("|".join(ORDINARY_HINT_TERMS), re.IGNORECASE)
REQUIRED_PATTERN = re.compile("|".join(REQUIRED_TERMS), re.IGNORECASE)
EXCLUDE_PATTERN = re.compile("|".join(EXCLUDE_TERMS), re.IGNORECASE)
AI_CORE_PATTERN = re.compile("|".join(AI_CORE_TERMS), re.IGNORECASE)
PRACTICE_REQUIRED_PATTERN = re.compile("|".join(PRACTICE_REQUIRED_TERMS), re.IGNORECASE)

REMOVED_SOURCE_DOMAINS = re.compile(
    r"twitter\.com|x\.com|weibo\.com|huggingface\.co",
    re.IGNORECASE,
)

# ══════════════════════════════════════════════════════════════════════════════
# 来源注册表
# ══════════════════════════════════════════════════════════════════════════════

SOURCE_REGISTRY = {
    "TechCrunch":       {"type": "intl", "display": "TechCrunch",       "icon": "🌐"},
    "Hacker News":      {"type": "intl", "display": "Hacker News",      "icon": "🌐"},
    "TLDR.tech":        {"type": "intl", "display": "TLDR",             "icon": "🌐"},
    "The Verge":        {"type": "intl", "display": "The Verge",        "icon": "🌐"},
    "VentureBeat":      {"type": "intl", "display": "VentureBeat",      "icon": "🌐"},
    "Ars Technica":     {"type": "intl", "display": "Ars Technica",     "icon": "🌐"},
    "MIT Tech Review":  {"type": "intl", "display": "MIT Tech Review",  "icon": "🌐"},
    "IEEE Spectrum":    {"type": "intl", "display": "IEEE Spectrum",    "icon": "🌐"},
    "Wired":            {"type": "intl", "display": "Wired",            "icon": "🌐"},
    "Audio/Music/Game AI": {"type": "intl", "display": "Audio/Music/Game AI", "icon": "🎧"},
    "Audio AI Special": {"type": "intl", "display": "Audio AI Special", "icon": "🔊"},
    "Video Tutorials":  {"type": "intl", "display": "Video Tutorials",  "icon": "🎬"},
    "Practical Guides": {"type": "intl", "display": "Practical Guides", "icon": "🛠️"},
    "Agent/Coding AI":  {"type": "intl", "display": "Agent/Coding AI",  "icon": "🤖"},
    "Audio Creator AI": {"type": "intl", "display": "Audio Creator AI", "icon": "🎵"},
    "AI Frontier":      {"type": "intl", "display": "AI Frontier",      "icon": "🧪"},
    "YouTube":          {"type": "intl", "display": "YouTube",          "icon": "📺"},
    "B站":               {"type": "domestic", "display": "B站",           "icon": "📺"},
    "微信公众号":        {"type": "domestic", "display": "微信公众号",    "icon": "📬"},
    "机器之心":          {"type": "domestic", "display": "机器之心",       "icon": "🏮"},
    "量子位":            {"type": "domestic", "display": "量子位",         "icon": "🏮"},
    "36氪":              {"type": "domestic", "display": "36氪",           "icon": "🏮"},
    "IT之家":            {"type": "domestic", "display": "IT之家",         "icon": "🏮"},
    "新智元":            {"type": "domestic", "display": "新智元",         "icon": "🏮"},
    "InfoQ":             {"type": "domestic", "display": "InfoQ",          "icon": "🏮"},
    "新浪科技":          {"type": "domestic", "display": "新浪科技",       "icon": "🏮"},
    "今日头条":          {"type": "domestic", "display": "今日头条",       "icon": "🏮"},
    "澎湃新闻":          {"type": "domestic", "display": "澎湃新闻",       "icon": "🏮"},
}

SOURCE_WEIGHT = {
    "Hacker News": 1.0,
    "TechCrunch": 80,
    "The Verge": 75,
    "VentureBeat": 70,
    "MIT Tech Review": 85,
    "Ars Technica": 65,
    "TLDR.tech": 60,
    "IEEE Spectrum": 75,
    "Wired": 70,
    "Audio/Music/Game AI": 90,
    "Audio AI Special": 97,
    "Video Tutorials": 86,
    "Practical Guides": 95,
    "Agent/Coding AI": 96,
    "Audio Creator AI": 94,
    "AI Frontier": 93,
    "YouTube": 78,
    "B站": 80,
    "微信公众号": 92,
    "机器之心": 85,
    "量子位": 75,
    "36氪": 70,
    "IT之家": 60,
    "新智元": 65,
    "InfoQ": 60,
    "新浪科技": 70,
    "今日头条": 65,
    "澎湃新闻": 68,
}

def get_source_info(source_name):
    info = SOURCE_REGISTRY.get(source_name, {})
    return {
        "type": info.get("type", "intl"),
        "display": info.get("display", source_name),
        "icon": info.get("icon", "🌐"),
    }

# ══════════════════════════════════════════════════════════════════════════════
# 抓取状态追踪器
# ══════════════════════════════════════════════════════════════════════════════

class SourceTracker:
    def __init__(self):
        self.results = {}

    def record(self, source_name, items):
        if items:
            self.results[source_name] = {"status": "ok", "count": len(items)}
        else:
            self.results[source_name] = {"status": "fail", "count": 0}

    @property
    def intl_success_count(self):
        return sum(
            1 for name, r in self.results.items()
            if r["status"] == "ok" and get_source_info(name)["type"] == "intl"
        )

    @property
    def domestic_success_count(self):
        return sum(
            1 for name, r in self.results.items()
            if r["status"] == "ok" and get_source_info(name)["type"] == "domestic"
        )

    def print_report(self):
        print("\n  ┌─────────────────────────────────────────┐")
        print("  │         📊 数据源抓取状态报告             │")
        print("  ├──────────────┬────────┬──────────────────┤")
        print("  │ 来源         │ 状态   │ 条数             │")
        print("  ├──────────────┼────────┼──────────────────┤")
        for name, r in self.results.items():
            icon = "✅" if r["status"] == "ok" else "❌"
            stype = get_source_info(name)["icon"]
            print(f"  │ {stype} {name:<10s}│ {icon}     │ {r['count']:<16d} │")
        print("  └──────────────┴────────┴──────────────────┘")
        print(f"  国际源成功: {self.intl_success_count} | 国内源成功: {self.domestic_success_count}")

tracker = SourceTracker()
PAGE_DATE_CACHE = {}
REQUEST_HOST_LAST_TS = {}

# ══════════════════════════════════════════════════════════════════════════════
# 历史记录管理（用于隔日去重）
# ══════════════════════════════════════════════════════════════════════════════

def load_history():
    """加载已推送的历史 URL 记录"""
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_history(new_urls):
    """保存推送记录，保留最近 1000 条防止文件过大"""
    history = load_history()
    updated = list(history.union(new_urls))[-1000:]
    try:
        PAGES_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(updated, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [WARN] Failed to save history: {e}")


def _ensure_state_dir():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"  [WARN] Failed to create state dir: {e}")


def _normalize_feedback_label(label):
    value = str(label or "").strip()
    mapping = {
        "有用": "有用",
        "一般": "一般",
        "无关": "无关",
        "太偏技术": "太偏技术",
        "太偏商业": "太偏商业",
        "适合音频部": "适合音频部",
    }
    return mapping.get(value, "")


def _normalize_feedback_labels(labels):
    if isinstance(labels, str):
        labels = [labels]
    normalized = []
    for label in labels or []:
        value = _normalize_feedback_label(label)
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _extract_feedback_terms(item):
    terms = set()
    title = str(item.get("title", "") or "")
    title_zh = str(item.get("title_zh", "") or "")
    summary = str(item.get("summary", "") or "")
    summary_zh = str(item.get("summary_zh", "") or "")
    search_query = str(item.get("search_query", "") or "")
    combined = " ".join([title, title_zh, summary, summary_zh, search_query]).lower()

    for match in re.findall(r"[a-z][a-z0-9\-\+\.]{2,}", combined):
        if len(match) >= 3:
            terms.add(match)

    for token in [
        "音频", "语音", "配音", "播客", "转写", "降噪", "混音", "母带", "音乐", "游戏",
        "教程", "实战", "案例", "工作流", "智能体", "agent", "workflow", "rag",
        "开源", "github", "模型", "大模型", "自动化", "技能", "skill", "skills",
    ]:
        if token.lower() in combined:
            terms.add(token.lower())
    return sorted(terms)[:30]


def append_review_feedback(records):
    if not records:
        return
    _ensure_state_dir()
    try:
        with open(REVIEW_FEEDBACK_FILE, "a", encoding="utf-8") as f:
            for row in records:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [WARN] Failed to append review feedback: {e}")
        return
    trim_review_feedback_file()


def trim_review_feedback_file():
    try:
        if not REVIEW_FEEDBACK_FILE.exists():
            return
        lines = REVIEW_FEEDBACK_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) <= REVIEW_FEEDBACK_MAX_ROWS:
            return
        REVIEW_FEEDBACK_FILE.write_text(
            "\n".join(lines[-REVIEW_FEEDBACK_MAX_ROWS:]) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        print(f"  [WARN] Failed to trim review feedback: {e}")


def load_review_feedback_rows(limit=1200):
    if not REVIEW_FEEDBACK_FILE.exists():
        return []
    rows = []
    try:
        with open(REVIEW_FEEDBACK_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def build_feedback_profile(limit=1200):
    rows = load_review_feedback_rows(limit=limit)
    profile = {
        "label_counts": {},
        "source_bias": {},
        "category_bias": {},
        "term_bias": {},
    }
    for row in rows:
        labels = _normalize_feedback_labels(row.get("labels") or row.get("label"))
        if not labels:
            if row.get("selected") is True:
                labels = ["一般"]
            elif row.get("selected") is False:
                labels = []
        if not labels and row.get("selected") is False:
            weight = -0.15
        else:
            weight = 0.0

        for label in labels:
            profile["label_counts"][label] = profile["label_counts"].get(label, 0) + 1
            weight += {
                "有用": 2.5,
                "适合音频部": 3.0,
                "一般": 0.5,
                "无关": -2.0,
                "太偏技术": -1.0,
                "太偏商业": -2.5,
            }.get(label, 0.0)

        source = str(row.get("source", "") or "").strip()
        category = str(row.get("category", "") or "").strip()
        if source:
            profile["source_bias"][source] = profile["source_bias"].get(source, 0.0) + weight
        if category:
            profile["category_bias"][category] = profile["category_bias"].get(category, 0.0) + weight
        for term in row.get("terms", [])[:20]:
            t = str(term or "").strip().lower()
            if not t:
                continue
            profile["term_bias"][t] = profile["term_bias"].get(t, 0.0) + weight
    return profile


def feedback_bias_score(item, profile=None):
    profile = profile or build_feedback_profile()
    score = 0.0
    source = str(item.get("source", "") or "")
    category = str(item.get("category", "") or "")
    score += profile.get("source_bias", {}).get(source, 0.0) * 1.2
    score += profile.get("category_bias", {}).get(category, 0.0) * 1.0

    terms = _extract_feedback_terms(item)
    term_bias = profile.get("term_bias", {})
    if terms:
        matched = [term_bias.get(t, 0.0) for t in terms if t in term_bias]
        if matched:
            score += sum(matched[:10]) / max(3, min(len(matched), 10))
    return round(score, 2)


def get_wechat_account_hint(item):
    text = " ".join([
        str(item.get("account_name", "") or ""),
        str(item.get("title", "") or ""),
        str(item.get("summary", "") or ""),
        str(item.get("title_zh", "") or ""),
        str(item.get("summary_zh", "") or ""),
        str(item.get("search_query", "") or ""),
    ])
    for name in WECHAT_PRIORITY_ACCOUNTS:
        if name and name.lower() in text.lower():
            return name
    return ""


WECHAT_BROWSER_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.34(0x16082222) NetType/WIFI Language/zh_CN"
)

# ══════════════════════════════════════════════════════════════════════════════
# 通用抓取工具函数
# ══════════════════════════════════════════════════════════════════════════════

def _maybe_throttle_request(url):
    try:
        host = (urlparse(url).netloc or "").lower().strip()
        if not host or host in {"localhost", "127.0.0.1"}:
            return
        min_delay = max(0.0, min(REQUEST_THROTTLE_MIN, REQUEST_THROTTLE_MAX))
        max_delay = max(min_delay, max(REQUEST_THROTTLE_MIN, REQUEST_THROTTLE_MAX))
        target_gap = random.uniform(min_delay, max_delay)
        last_ts = REQUEST_HOST_LAST_TS.get(host, 0.0)
        wait_s = target_gap - (time.time() - last_ts)
        if wait_s > 0:
            time.sleep(wait_s)
        REQUEST_HOST_LAST_TS[host] = time.time()
    except Exception:
        return


def safe_request(url, timeout=15, headers=None, trust_env=True, allow_redirects=True):
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if "mp.weixin.qq.com/" in str(url):
        default_headers["User-Agent"] = WECHAT_BROWSER_UA
        default_headers["Accept-Language"] = "zh-CN,zh;q=0.9"
    if headers:
        default_headers.update(headers)
    for attempt in range(max(1, REQUEST_RETRIES)):
        try:
            _maybe_throttle_request(url)
            with requests.Session() as session:
                session.trust_env = trust_env
                resp = session.get(
                    url,
                    timeout=timeout,
                    headers=default_headers,
                    allow_redirects=allow_redirects,
                )
            resp.raise_for_status()
            return resp
        except Exception as e:
            if "mp.weixin.qq.com/" in str(url) and trust_env:
                try:
                    _maybe_throttle_request(url)
                    with requests.Session() as session:
                        session.trust_env = False
                        resp = session.get(
                            url,
                            timeout=timeout,
                            headers=default_headers,
                            allow_redirects=allow_redirects,
                        )
                    resp.raise_for_status()
                    return resp
                except Exception:
                    pass
            if attempt < max(1, REQUEST_RETRIES) - 1:
                time.sleep(0.5)
            else:
                raise e
    return None

def is_github_url(url):
    return bool(GITHUB_FILTER.search(url))


# ══════════════════════════════════════════════════════════════════════════════
# ★ v3.2 新增：从 URL 中提取真实发布日期
# ══════════════════════════════════════════════════════════════════════════════

def extract_date_from_url(url):
    """
    尝试从 URL 路径中提取发布日期。
    许多新闻网站的 URL 中包含日期信息，如:
      - https://www.jiqizhixin.com/articles/2026-04-09-xxx
      - https://36kr.com/p/2026041300001
      - https://tech.sina.com.cn/2026-04-13/doc-xxx.shtml
      - https://www.ithome.com/0/846/123.htm (无日期，返回 None)
    返回 "YYYY-MM-DD" 格式字符串，提取失败则返回 None。
    """
    try:
        # 模式1: /YYYY/MM/DD/ 或 /YYYY-MM-DD/ 或 /YYYY_MM_DD/
        m = re.search(r'/(\d{4})[/\-_](\d{1,2})[/\-_](\d{1,2})', url)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2020 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"

        # 模式2: /YYYYMMDD (8位连续数字，常见于国内新闻网站)
        m = re.search(r'/(\d{4})(\d{2})(\d{2})', url)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2020 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"

        # 模式3: 查询参数中的日期 (如 ?date=2026-04-13)
        m = re.search(r'[?&]date=(\d{4})-(\d{2})-(\d{2})', url)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2020 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"

        return None
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ★ v3.1 新增：通用产品官网首页自动检测
# ══════════════════════════════════════════════════════════════════════════════

def _is_product_homepage(url):
    """
    判断 URL 是否为产品官网首页（非新闻文章）。
    当 HN 文章的原始链接是网站根路径（如 https://botctl.dev/），
    大概率是产品官网首页而非新闻文章，应自动替换为 HN 讨论页。
    """
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        # 已知的 HN 自身域名，不视为产品官网
        if "ycombinator.com" in parsed.netloc or "news.ycombinator" in parsed.netloc:
            return False

        # 1. 根路径或空路径 → 大概率是产品官网首页
        if not path or path == "":
            return True

        # 2. 只有一级路径且为典型产品页面（如 /pricing、/about）→ 产品页
        segments = [s for s in path.split("/") if s]
        if len(segments) == 1 and segments[0].lower() in {
            "pricing", "about", "features", "docs", "signup",
            "register", "login", "download", "get-started",
            "try", "demo", "contact", "enterprise", "pro",
            "plans", "solutions", "platform", "overview",
            "changelog", "roadmap", "careers", "jobs",
        }:
            return True

        # 3. 非主流新闻/博客域名 且 只有短路径（≤1段）→ 很可能是产品官网
        known_content_domains = {
            # 主流新闻媒体
            "techcrunch.com", "theverge.com", "arstechnica.com",
            "wired.com", "venturebeat.com", "technologyreview.com",
            "spectrum.ieee.org", "reuters.com", "bloomberg.com",
            "nytimes.com", "wsj.com", "bbc.com", "bbc.co.uk",
            "theguardian.com", "cnbc.com", "apnews.com",
            "zdnet.com", "cnet.com", "engadget.com",
            "theregister.com", "tomshardware.com",
            # 技术博客/社区
            "medium.com", "dev.to", "substack.com",
            "wordpress.com", "blogspot.com",
            "reddit.com", "twitter.com", "x.com",
            "youtube.com", "youtu.be", "bilibili.com", "b23.tv",
            "weibo.com", "news.google.com", "nitter.net",
            # 国内媒体
            "jiqizhixin.com", "qbitai.com", "36kr.com",
            "ithome.com", "thepaper.cn", "sina.com.cn",
            "infoq.cn", "aihub.cn",
            # 官方博客子域（常见模式）
            "blog.google", "openai.com", "anthropic.com",
            "ai.meta.com", "deepmind.google",
        }
        domain = parsed.netloc.lower().replace("www.", "")
        if domain not in known_content_domains and len(segments) <= 1:
            return True

        return False
    except Exception:
        return False


def is_hn_discussion_url(url):
    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower()
        if "news.ycombinator.com" not in domain:
            return False
        if parsed.path != "/item":
            return False
        query = parsed.query or ""
        return "id=" in query
    except Exception:
        return False


def build_google_news_rss(query, hl="zh-CN", gl="CN", ceid="CN:zh-Hans"):
    encoded = quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl={hl}&gl={gl}&ceid={ceid}"


def _mark_social_item(item, platform="", is_video=False):
    item["is_social"] = True
    item["platform"] = platform or item.get("source", "")
    item["is_video"] = bool(is_video)
    return item


def parse_rss_feed_candidates(urls, source_name, max_entries=20, ai_filter=False):
    merged = []
    seen = set()
    for feed_url in urls:
        if not feed_url:
            continue
        try:
            part = parse_rss_feed(
                feed_url,
                source_name=source_name,
                max_entries=max_entries,
                ai_filter=ai_filter,
            )
            for it in part:
                norm = it.get("url", "").rstrip("/")
                if norm and norm not in seen:
                    seen.add(norm)
                    merged.append(it)
        except Exception:
            continue
    return merged


def _collect_links_from_listing(url, source_name, max_items=12, link_limit=60):
    items = []
    try:
        resp = safe_request(url, timeout=LISTING_FETCH_TIMEOUT)
        if not resp:
            return items
        html = resp.text
        base = resp.url or url
        seen = set()
        patterns = [
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            r"<a[^>]+href='([^']+)'[^>]*>(.*?)</a>",
        ]
        matches = []
        for pattern in patterns:
            matches.extend(re.findall(pattern, html, re.IGNORECASE | re.DOTALL))
        for href, raw_title in matches[:link_limit]:
            title = re.sub(r"<[^>]+>", " ", raw_title or "")
            title = unescape(re.sub(r"\s+", " ", title)).strip()
            if len(title) < 8:
                continue
            link = urljoin(base, href.strip())
            if not link.startswith("http"):
                continue
            if link in seen:
                continue
            if HARD_BLOCK_DOMAINS.search(link):
                continue
            seen.add(link)
            extracted_date = extract_date_from_url(link)
            if not extracted_date and DEEP_PAGE_DATE_IN_LISTING:
                extracted_date = extract_page_published_date(link)
            items.append({
                "title": title,
                "url": link,
                "summary": f"via {source_name}",
                "source": source_name,
                "source_type": get_source_info(source_name)["type"],
                "date": extracted_date or "",
                "date_inferred": not bool(extracted_date),
                "fetched_at": _now_iso(),
                "score": 0,
            })
            if len(items) >= max_items:
                break
    except Exception:
        return items
    return items


def _fetch_direct_tutorial_candidates(source_name, max_entries=18):
    items = []
    items.extend(
        parse_rss_feed_candidates(
            urls=DIRECT_TUTORIAL_FEEDS,
            source_name=source_name,
            max_entries=max(3, max_entries // 2),
            ai_filter=False,
        )
    )
    for page_url in DISCOVERABLE_TUTORIAL_PAGES[:LISTING_PAGE_LIMIT]:
        items.extend(
            _collect_links_from_listing(
                page_url,
                source_name=source_name,
                max_items=LISTING_ITEMS_PER_PAGE,
                link_limit=25 if FAST_FETCH_MODE else 60,
            )
        )
    return items


def _fetch_custom_curated_candidates(source_name, feed_urls, page_urls, max_entries=18):
    items = []
    items.extend(
        parse_rss_feed_candidates(
            urls=feed_urls,
            source_name=source_name,
            max_entries=max(3, max_entries // 2),
            ai_filter=False,
        )
    )
    for page_url in page_urls[:LISTING_PAGE_LIMIT]:
        items.extend(
            _collect_links_from_listing(
                page_url,
                source_name=source_name,
                max_items=LISTING_ITEMS_PER_PAGE,
                link_limit=25 if FAST_FETCH_MODE else 60,
            )
        )
    return items


def fetch_practical_guides():
    source = "Practical Guides"
    items = []
    stats = {"raw": 0, "date": 0, "keyword": 0}
    print("      [B.5] 实用教程源抓取中...")

    items.extend(_fetch_direct_tutorial_candidates(source_name=source, max_entries=24))

    tutorial_domains = [
        "openai.com",
        "anthropic.com",
        "blog.langchain.com",
        "www.llamaindex.ai",
        "dify.ai",
        "blog.n8n.io",
        "www.pinecone.io",
        "weaviate.io",
        "crewai.com",
        "vercel.com",
        "replicate.com",
        "elevenlabs.io",
        "developer.chrome.com",
        "developer.nvidia.com",
        "developers.googleblog.com",
        "learn.microsoft.com",
        "cloud.google.com",
    ]
    for dom in tutorial_domains[:PRACTICAL_DOMAIN_LIMIT]:
        items.extend(
            _fetch_google_news_site(
                dom,
                source_name=source,
                extra_queries=SOCIAL_PRACTICAL_QUERIES[:GOOGLE_NEWS_QUERY_LIMIT],
                max_entries=3,
            )
        )

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        url = it.get("url", "").rstrip("/")
        if not url or url in seen:
            continue
        if not is_within_days(it.get("date"), 30):
            stats["date"] += 1
            continue
        if not practical_keyword_gate(it):
            stats["keyword"] += 1
            continue
        seen.add(url)
        dedup.append(it)

    print(f"      [B.5] 实用教程源完成: {len(dedup)} 条 (raw={stats['raw']}, 日期过滤={stats['date']}, 关键词过滤={stats['keyword']})")
    tracker.record(source, dedup)
    return dedup


def fetch_agent_coding_guides():
    source = "Agent/Coding AI"
    items = []
    stats = {"raw": 0, "date": 0, "keyword": 0}
    print("      [B.5] Agent/编程/自动化源抓取中...")

    items.extend(_fetch_custom_curated_candidates(source, AGENT_CODING_FEEDS, AGENT_CODING_PAGES, max_entries=22))
    for dom in [
        "openai.com",
        "anthropic.com",
        "blog.langchain.com",
        "www.llamaindex.ai",
        "dify.ai",
        "blog.n8n.io",
        "crewai.com",
        "github.blog",
        "vercel.com",
        "learn.microsoft.com",
    ][:AGENT_DOMAIN_LIMIT]:
        items.extend(
            _fetch_google_news_site(
                dom,
                source_name=source,
                extra_queries=SOCIAL_PRACTICAL_QUERIES[:GOOGLE_NEWS_QUERY_LIMIT],
                max_entries=3,
            )
        )

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        url = it.get("url", "").rstrip("/")
        if not url or url in seen:
            continue
        if not is_within_days(it.get("date"), 30):
            stats["date"] += 1
            continue
        if not practical_keyword_gate(it):
            stats["keyword"] += 1
            continue
        seen.add(url)
        dedup.append(it)

    print(f"      [B.5] Agent/编程/自动化源完成: {len(dedup)} 条 (raw={stats['raw']}, 日期过滤={stats['date']}, 关键词过滤={stats['keyword']})")
    tracker.record(source, dedup)
    return dedup


def fetch_audio_creator_guides():
    source = "Audio Creator AI"
    items = []
    stats = {"raw": 0, "date": 0, "keyword": 0}
    print("      [B.5] 音频/音乐/游戏创作源抓取中...")

    items.extend(_fetch_custom_curated_candidates(source, AUDIO_CREATOR_FEEDS, AUDIO_CREATOR_PAGES, max_entries=20))
    for dom in [
        "elevenlabs.io",
        "replicate.com",
        "suno.com",
        "descript.com",
        "runwayml.com",
        "unity.com",
        "unrealengine.com",
        "developer.nvidia.com",
    ][:AUDIO_CREATOR_DOMAIN_LIMIT]:
        items.extend(
            _fetch_google_news_site(
                dom,
                source_name=source,
                extra_queries=AUDIO_MUSIC_GAME_QUERIES[:GOOGLE_NEWS_QUERY_LIMIT],
                max_entries=3,
            )
        )

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        url = it.get("url", "").rstrip("/")
        if not url or url in seen:
            continue
        if not is_within_days(it.get("date"), 30):
            stats["date"] += 1
            continue
        if not practical_keyword_gate(it):
            stats["keyword"] += 1
            continue
        seen.add(url)
        dedup.append(it)

    print(f"      [B.5] 音频/音乐/游戏创作源完成: {len(dedup)} 条 (raw={stats['raw']}, 日期过滤={stats['date']}, 关键词过滤={stats['keyword']})")
    tracker.record(source, dedup)
    return dedup


def fetch_ai_frontier():
    source = "AI Frontier"
    items = []
    stats = {"raw": 0, "date": 0, "keyword": 0}
    print("      [B.5] 新模型/新技术前沿源抓取中...")

    frontier_domains = [
        "openai.com",
        "anthropic.com",
        "blog.google",
        "deepmind.google",
        "ai.meta.com",
        "developer.nvidia.com",
        "venturebeat.com",
        "technologyreview.com",
        "spectrum.ieee.org",
        "arstechnica.com",
        "techcrunch.com",
    ]

    for dom in frontier_domains[:FRONTIER_DOMAIN_LIMIT]:
        items.extend(
            _fetch_google_news_site(
                dom,
                source_name=source,
                extra_queries=MODEL_INNOVATION_QUERIES[:GOOGLE_NEWS_QUERY_LIMIT],
                max_entries=4,
            )
        )

    items.extend(
        parse_rss_feed_candidates(
            urls=[
                "https://openai.com/news/rss.xml",
                "https://developer.nvidia.com/blog/feed/",
                "https://venturebeat.com/category/ai/feed/",
                "https://www.technologyreview.com/feed/",
                "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",
            ],
            source_name=source,
            max_entries=5,
            ai_filter=False,
        )
    )

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        url = it.get("url", "").rstrip("/")
        if not url or url in seen:
            continue
        if not is_within_days(it.get("date"), 7):
            stats["date"] += 1
            continue
        if not frontier_innovation_gate(it):
            stats["keyword"] += 1
            continue
        seen.add(url)
        dedup.append(it)

    print(f"      [B.5] 新模型/新技术前沿源完成: {len(dedup)} 条 (raw={stats['raw']}, 日期过滤={stats['date']}, 关键词过滤={stats['keyword']})")
    tracker.record(source, dedup)
    return dedup


def fetch_wechat_articles():
    source = WECHAT_SOURCE_NAME
    items = []
    stats = {"raw": 0, "date": 0, "keyword": 0, "google": 0, "rsshub": 0, "sogou": 0, "bing": 0, "werss": 0}
    print("      [B.5] 微信公众号文章抓取中...")

    priority_accounts = [x for x in WECHAT_OFFICIAL_ACCOUNTS if x in WECHAT_PRIORITY_ACCOUNTS]
    other_accounts = [x for x in WECHAT_OFFICIAL_ACCOUNTS if x not in WECHAT_PRIORITY_ACCOUNTS]

    wechat_queries = [
        "AI 教程 实战 工作流",
        "AI agent 教程",
        "AI 音频 工作流",
        "AI 配音 工具",
        "AI 播客 工作流",
        "AI 音乐 制作",
        "AI 游戏 开发",
        "大模型 实战",
        "DeepSeek Qwen Claude 教程",
        "RAG 智能体 案例",
        "开源 AI 工具 使用指南",
        "语音 识别 合成 教程",
    ]
    for account in priority_accounts[:6]:
        wechat_queries.insert(0, f"{account} AI")
        wechat_queries.insert(1, f"{account} 音频 AI")
        wechat_queries.insert(2, f"{account} 教程")

    # 1) 搜狗微信搜索主抓
    sogou_queries = []
    for account in priority_accounts[:6]:
        sogou_queries.extend([
            f"{account} AI",
            f"{account} 音频",
            f"{account} 教程",
        ])
    sogou_queries.extend([
        "公众号 AI 音频 工作流",
        "公众号 AI 配音 教程",
        "公众号 AI 播客 工作流",
        "公众号 AI agent 实战",
        "公众号 大模型 应用 实战",
    ])
    if WERSS_ENABLE:
        werss_items = _fetch_werss_wechat_articles(
            source_name=source,
            max_entries=24,
        )
        items.extend(werss_items)
        stats["werss"] = len(werss_items)

    fallback_needed = len(items) < 10

    if WECHAT_ENABLE_SOGOU:
        if fallback_needed:
            sogou_items = _fetch_sogou_wechat_search(
                source_name=source,
                queries=sogou_queries,
                max_items=18,
            )
            items.extend(sogou_items)
            stats["sogou"] = len(sogou_items)

    # 2) Bing 站内搜索兜底
    if WECHAT_ENABLE_BING:
        if len(items) < 12:
            bing_items = _fetch_bing_wechat_search(
                source_name=source,
                queries=sogou_queries,
                max_items=18,
            )
            items.extend(bing_items)
            stats["bing"] = len(bing_items)

    # 3) Google News 仅保留为可选兜底
    if WECHAT_ENABLE_GOOGLE_NEWS:
        if len(items) < 12:
            google_items = _fetch_google_news_site(
                "mp.weixin.qq.com",
                source_name=source,
                extra_queries=wechat_queries[: max(18, VIDEO_QUERY_LIMIT + 8)],
                max_entries=20,
            )
            items.extend(google_items)
            stats["google"] = len(google_items)

    # 4) RSSHub 默认关闭，仅在手动开启时尝试
    if WECHAT_ENABLE_RSSHUB:
        if len(items) < 12:
            if priority_accounts:
                rsshub_priority_items = _fetch_rsshub_wechat_accounts(
                    source_name=source,
                    account_names=priority_accounts[:8],
                    max_entries=20,
                )
                items.extend(rsshub_priority_items)
                stats["rsshub"] += len(rsshub_priority_items)
            if other_accounts:
                rsshub_other_items = _fetch_rsshub_wechat_accounts(
                    source_name=source,
                    account_names=other_accounts[:8],
                    max_entries=12,
                )
                items.extend(rsshub_other_items)
                stats["rsshub"] += len(rsshub_other_items)

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        original_url = it.get("url", "")
        real_url = resolve_google_news_redirect(original_url) if "news.google.com/rss/articles/" in original_url else ""
        if "news.google.com/rss/articles/" in original_url and not real_url:
            continue
        if real_url:
            it["url"] = real_url

        url = it.get("url", "").rstrip("/")
        if not url or url in seen:
            continue
        if "mp.weixin.qq.com" not in url:
            continue

        page_date = extract_page_published_date(url)
        effective_date = page_date or it.get("date", "")
        if page_date:
            it["date"] = page_date
            it["date_inferred"] = False
        if not effective_date or not is_within_days(effective_date, 30):
            stats["date"] += 1
            continue

        if not practical_keyword_gate(it):
            stats["keyword"] += 1
            continue

        account_hint = get_wechat_account_hint(it)
        if account_hint:
            it["account_name"] = account_hint
            it["is_priority_wechat"] = True
        elif it.get("account_name"):
            it["is_priority_wechat"] = it["account_name"] in WECHAT_PRIORITY_ACCOUNTS
        else:
            it["is_priority_wechat"] = False

        seen.add(url)
        dedup.append(_mark_social_item(it, platform="WeChat", is_video=False))

    print(
        f"      [B.5] 微信公众号文章完成: {len(dedup)} 条 "
        f"(raw={stats['raw']}, WeRSS={stats['werss']}, 搜狗={stats['sogou']}, Bing={stats['bing']}, Google={stats['google']}, RSSHub={stats['rsshub']}, "
        f"日期过滤={stats['date']}, 关键词过滤={stats['keyword']})"
    )
    tracker.record(source, dedup)
    return dedup


def _fetch_google_news_site(site_domain, source_name, extra_queries, max_entries=8):
    urls = []
    for q in extra_queries[:GOOGLE_NEWS_QUERY_LIMIT]:
        query = f"site:{site_domain} ({q})"
        urls.append(build_google_news_rss(query))
    items = parse_rss_feed_candidates(
        urls=urls,
        source_name=source_name,
        max_entries=max_entries,
        ai_filter=False,
    )
    return items


def scrape_youtube_search_results(queries, max_items=20):
    source = "YouTube"
    items = []
    seen = set()
    for q in queries:
        try:
            url = f"https://www.youtube.com/results?search_query={quote_plus(q)}&sp=CAI%253D"
            resp = safe_request(url, timeout=15, headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
            if not resp:
                continue
            html = resp.text

            renderer_blocks = re.findall(r'(\{"videoRenderer":\{.*?\}\})', html, re.DOTALL)
            parsed_from_blocks = False
            for block in renderer_blocks:
                vid_m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', block)
                title_m = re.search(r'"title":\{"runs":\[\{"text":"([^"]{6,160})"', block)
                if not vid_m or not title_m:
                    continue
                vid = vid_m.group(1)
                video_url = f"https://www.youtube.com/watch?v={vid}"
                if video_url in seen:
                    continue
                title = title_m.group(1)
                rel_m = re.search(r'"publishedTimeText":\{"simpleText":"([^"]+)"\}', block, re.IGNORECASE)
                rel_text = rel_m.group(1) if rel_m else ""
                length_m = re.search(r'"lengthText":\{"(?:simpleText":"([^"]+)"|accessibility":\{"accessibilityData":\{"label":"([^"]+)"\}\})', block, re.IGNORECASE)
                length_text = ""
                if length_m:
                    length_text = next((g for g in length_m.groups() if g), "")
                approx_date = parse_relative_date_to_iso(rel_text) or _now_iso()
                if rel_text and not is_within_days(approx_date, YOUTUBE_MAX_AGE_DAYS):
                    continue
                seen.add(video_url)
                items.append({
                    "title": title,
                    "url": video_url,
                    "summary": f"via {source} search {rel_text} {length_text}".strip(),
                    "search_query": q,
                    "source": source,
                    "source_type": get_source_info(source)["type"],
                    "date": "",
                    "date_inferred": True,
                    "_search_rel_text": rel_text,
                    "_search_rel_date": approx_date if rel_text else "",
                    "fetched_at": _now_iso(),
                    "score": 0,
                })
                parsed_from_blocks = True
                if len(items) >= max_items:
                    return items
            if parsed_from_blocks:
                continue

            ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
            titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]{6,120})"', html)
            rel_times = re.findall(r'"publishedTimeText":\{"simpleText":"([^"]+)"\}', html, re.IGNORECASE)
            for idx, vid in enumerate(ids):
                video_url = f"https://www.youtube.com/watch?v={vid}"
                if video_url in seen:
                    continue
                title = titles[idx] if idx < len(titles) else f"YouTube video {vid}"
                rel_text = rel_times[idx] if idx < len(rel_times) else ""
                approx_date = parse_relative_date_to_iso(rel_text) or _now_iso()
                if rel_text and not is_within_days(approx_date, YOUTUBE_MAX_AGE_DAYS):
                    continue
                seen.add(video_url)
                items.append({
                    "title": title,
                    "url": video_url,
                    "summary": f"via {source} search {rel_text}".strip(),
                    "search_query": q,
                    "source": source,
                    "source_type": get_source_info(source)["type"],
                    "date": "",
                    "date_inferred": True,
                    "_search_rel_text": rel_text,
                    "_search_rel_date": approx_date if rel_text else "",
                    "fetched_at": _now_iso(),
                    "score": 0,
                })
                if len(items) >= max_items:
                    return items
        except Exception:
            continue
    return items


def scrape_youtube_by_ytdlp_search(queries, max_items=20):
    """
    用 yt-dlp 的 ytsearchdate 直接拉“最新视频”，减少网页搜索页旧内容混入。
    仅作为候选获取器，最终发布时间仍由 _extract_youtube_published_date 二次校验。
    """
    items = []
    seen = set()
    commands = []
    exe = shutil.which("yt-dlp")
    if exe:
        commands.append([exe])
    commands.append([sys.executable, "-m", "yt_dlp"])

    per_query = max(1, min(4, max_items // max(1, min(len(queries), VIDEO_QUERY_LIMIT))))
    for q in queries[:VIDEO_QUERY_LIMIT]:
        for prefix in commands:
            try:
                query_expr = f"ytsearchdate{per_query}:{q}"
                cmd = prefix + [
                    "--dump-single-json",
                    "--skip-download",
                    "--no-warnings",
                    "--extractor-args",
                    "youtube:lang=zh-CN",
                    query_expr,
                ]
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=max(YTDLP_TIMEOUT, 12),
                )
                if proc.returncode != 0 or not proc.stdout.strip():
                    continue
                data = json.loads(proc.stdout)
                entries = data.get("entries", []) if isinstance(data, dict) else []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    video_id = (entry.get("id") or "").strip()
                    webpage_url = (entry.get("webpage_url") or "").strip()
                    if not webpage_url and video_id:
                        webpage_url = f"https://www.youtube.com/watch?v={video_id}"
                    if not webpage_url or webpage_url in seen:
                        continue
                    title = (entry.get("title") or "").strip()
                    if len(title) < 6:
                        continue
                    upload_date = normalize_yt_dlp_date(entry.get("upload_date"))
                    if not upload_date:
                        upload_date = normalize_yt_dlp_timestamp(entry.get("timestamp"))
                    seen.add(webpage_url)
                    items.append({
                        "title": title,
                        "url": webpage_url,
                        "summary": _truncate_text(
                            f"{entry.get('description', '')} via YouTube yt-dlp search",
                            220,
                        ),
                        "search_query": q,
                        "source": "YouTube",
                        "source_type": get_source_info("YouTube")["type"],
                        "date": upload_date,
                        "date_inferred": not bool(upload_date),
                        "_ytdlp_has_date": bool(upload_date),
                        "fetched_at": _now_iso(),
                        "score": 0,
                    })
                    if len(items) >= max_items:
                        return items
                break
            except Exception:
                continue
    return items


def scrape_bilibili_search_results(queries, max_items=20):
    source = "B站"
    items = []
    seen = set()
    for q in queries:
        try:
            url = f"https://search.bilibili.com/all?keyword={quote_plus(q)}"
            resp = safe_request(url, timeout=15, headers={"Referer": "https://www.bilibili.com/"})
            if not resp:
                continue
            html = resp.text

            matches = re.findall(r'href="(//www\.bilibili\.com/video/[^"]+)"[^>]*title="([^"]{6,120})"', html, re.IGNORECASE)
            for link, title in matches:
                video_url = "https:" + link if link.startswith("//") else link
                if video_url in seen:
                    continue
                seen.add(video_url)
                items.append({
                    "title": unescape(title),
                    "url": video_url,
                    "summary": f"via {source} search",
                    "search_query": q,
                    "source": source,
                    "source_type": get_source_info(source)["type"],
                    "date": _now_iso(),
                    "date_inferred": True,
                    "fetched_at": _now_iso(),
                    "score": 0,
                })
                if len(items) >= max_items:
                    return items
            # 兜底：兼容 B站新版搜索页脚本数据
            script_matches = re.findall(
                r'"arcurl":"(https:\\/\\/www\.bilibili\.com\\/video\\/[^"]+)".{0,600}?"title":"([^"]{6,160})".{0,600}?"pubdate":(\d{10})',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            for raw_url, raw_title, ts in script_matches:
                video_url = raw_url.replace("\\/", "/")
                if video_url in seen:
                    continue
                seen.add(video_url)
                items.append({
                    "title": unescape(re.sub(r"<[^>]+>", "", raw_title)),
                    "url": video_url,
                    "summary": f"via {source} search",
                    "search_query": q,
                    "source": source,
                    "source_type": get_source_info(source)["type"],
                    "date": datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(BEIJING_TZ).isoformat(),
                    "date_inferred": False,
                    "fetched_at": _now_iso(),
                    "score": 0,
                })
                if len(items) >= max_items:
                    return items
            api_url = (
                "https://api.bilibili.com/x/web-interface/search/type"
                f"?search_type=video&keyword={quote_plus(q)}&page=1"
            )
            api_resp = safe_request(
                api_url,
                timeout=15,
                headers={
                    "Referer": "https://www.bilibili.com/",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            if api_resp:
                api_data = api_resp.json() if "json" in api_resp.headers.get("Content-Type", "").lower() else {}
                result_items = (((api_data or {}).get("data") or {}).get("result") or [])
                for row in result_items:
                    if not isinstance(row, dict):
                        continue
                    video_url = (row.get("arcurl") or "").strip()
                    bvid = (row.get("bvid") or "").strip()
                    if not video_url and bvid:
                        video_url = f"https://www.bilibili.com/video/{bvid}"
                    if not video_url or video_url in seen:
                        continue
                    raw_title = row.get("title") or ""
                    title = unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
                    if len(title) < 6:
                        continue
                    ts = row.get("pubdate")
                    date_str = ""
                    try:
                        if ts:
                            date_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
                    except Exception:
                        date_str = ""
                    seen.add(video_url)
                    items.append({
                        "title": title,
                        "url": video_url,
                        "summary": _truncate_text(unescape(re.sub(r"<[^>]+>", "", row.get("description", "") or "")), 220) or f"via {source} search",
                        "search_query": q,
                        "source": source,
                        "source_type": get_source_info(source)["type"],
                        "date": date_str,
                        "date_inferred": not bool(date_str),
                        "fetched_at": _now_iso(),
                        "score": 0,
                    })
                    if len(items) >= max_items:
                        return items
        except Exception:
            continue
    return items


def _fetch_rsshub_keyword(route_template, source_name, keywords, max_entries=8):
    urls = []
    for base in RSSHUB_BASES:
        for kw in keywords:
            encoded = quote_plus(kw)
            urls.append(f"{base}/{route_template.format(keyword=encoded).lstrip('/')}")
    return parse_rss_feed_candidates(
        urls=urls,
        source_name=source_name,
        max_entries=max_entries,
        ai_filter=False,
    )


def _fetch_nitter_search(source_name, keywords, max_entries=8):
    urls = []
    for base in NITTER_BASES:
        for kw in keywords:
            urls.append(f"{base}/search/rss?f=tweets&q={quote_plus(kw)}")
    return parse_rss_feed_candidates(
        urls=urls,
        source_name=source_name,
        max_entries=max_entries,
        ai_filter=False,
    )


def _build_werss_feed_urls():
    urls = []
    urls.extend(WERSS_FEED_URLS)
    for base in WERSS_BASES:
        urls.extend([
            f"{base}/rss",
            f"{base}/rss/fresh",
            f"{base}/feeds/all.xml",
            f"{base}/feeds/all.rss",
            f"{base}/feeds/all.atom",
            f"{base}/feeds/all.json",
        ])
    dedup = []
    seen = set()
    for url in urls:
        norm = url.strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        dedup.append(norm)
    return dedup


def _resolve_existing_werss_sqlite():
    for path in WERSS_SQLITE_CANDIDATES:
        try:
            if path and path.exists() and path.is_file():
                return path
        except Exception:
            continue
    return None


def _discover_werss_feed_urls_from_sqlite():
    urls = []
    db_path = _resolve_existing_werss_sqlite()
    if not db_path:
        return urls
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id FROM feeds WHERE id IS NOT NULL AND TRIM(id) != '' ORDER BY created_at DESC LIMIT ?",
            (max(1, WERSS_DISCOVERY_LIMIT),),
        ).fetchall()
        conn.close()
        feed_ids = [str(r[0]).strip() for r in rows if r and str(r[0]).strip()]
        for base in WERSS_BASES:
            for feed_id in feed_ids:
                urls.append(f"{base}/rss/{feed_id}")
                urls.append(f"{base}/feed/{feed_id}.xml")
    except Exception as e:
        print(f"  [WARN] WeRSS SQLite discovery failed: {e}")
    return urls


def _discover_werss_feed_urls_from_root():
    urls = []
    for base in WERSS_BASES:
        try:
            resp = safe_request(
                f"{base}/rss",
                timeout=RSS_FETCH_TIMEOUT,
                headers={"Accept": "application/rss+xml, application/xml, text/xml, */*"},
                trust_env=False,
            )
            if not resp:
                continue
            text = resp.text or ""
            feed_ids = re.findall(r"/rss/(MP_WXS_[A-Za-z0-9_]+)", text, flags=re.IGNORECASE)
            if not feed_ids:
                feed_ids = re.findall(r"/feed/(MP_WXS_[A-Za-z0-9_]+)\.xml", text, flags=re.IGNORECASE)
            for feed_id in feed_ids[:WERSS_DISCOVERY_LIMIT]:
                urls.append(f"{base}/rss/{feed_id}")
                urls.append(f"{base}/feed/{feed_id}.xml")
        except Exception:
            continue
    return urls


def _discover_werss_feed_urls():
    urls = []
    urls.extend(_discover_werss_feed_urls_from_root())
    urls.extend(_discover_werss_feed_urls_from_sqlite())
    dedup = []
    seen = set()
    for url in urls:
        norm = (url or "").strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        dedup.append(norm)
    return dedup


def _extract_wechat_entry_account_name(entry, feed_title=""):
    candidates = [feed_title]
    if isinstance(entry, dict):
        candidates.extend([
            entry.get("author", ""),
            entry.get("publisher", ""),
            entry.get("account_name", ""),
            entry.get("mp_name", ""),
            entry.get("feed_title", ""),
            entry.get("source_name", ""),
        ])
        source_info = entry.get("source")
        if isinstance(source_info, dict):
            candidates.extend([source_info.get("title", ""), source_info.get("name", "")])
        author_detail = entry.get("author_detail")
        if isinstance(author_detail, dict):
            candidates.extend([author_detail.get("name", ""), author_detail.get("email", "")])
        authors = entry.get("authors") or []
        if isinstance(authors, list):
            for author in authors[:3]:
                if isinstance(author, dict):
                    candidates.extend([author.get("name", ""), author.get("title", "")])
                elif isinstance(author, str):
                    candidates.append(author)
    return _extract_wechat_account_name(" | ".join(str(x or "") for x in candidates if x))


def _extract_json_feed_entries(payload):
    if isinstance(payload, list):
        return payload, ""
    if not isinstance(payload, dict):
        return [], ""

    feed_title = str(
        payload.get("title", "")
        or ((payload.get("feed") or {}).get("title", "") if isinstance(payload.get("feed"), dict) else "")
    ).strip()

    for key in ("items", "entries", "articles", "list", "posts"):
        val = payload.get(key)
        if isinstance(val, list):
            return val, feed_title

    data = payload.get("data")
    if isinstance(data, list):
        return data, feed_title
    if isinstance(data, dict):
        for key in ("items", "entries", "articles", "list", "posts"):
            val = data.get(key)
            if isinstance(val, list):
                return val, feed_title or str(data.get("title", "")).strip()

    return [], feed_title


def parse_json_feed(url, source_name, max_entries=20, ai_filter=False):
    items = []
    try:
        resp = safe_request(
            url,
            timeout=RSS_FETCH_TIMEOUT,
            headers={"Accept": "application/json,text/plain,*/*"},
        )
        if not resp:
            return items
        payload = resp.json()
        entries, feed_title = _extract_json_feed_entries(payload)
        for entry in entries[:max_entries]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "") or "").strip()
            link = str(
                entry.get("url", "")
                or entry.get("external_url", "")
                or entry.get("link", "")
                or entry.get("id", "")
            ).strip()
            if not title or not link:
                continue
            summary_raw = (
                entry.get("summary", "")
                or entry.get("content_text", "")
                or entry.get("description", "")
                or entry.get("content_html", "")
                or entry.get("content", "")
            )
            summary = unescape(re.sub(r"<[^>]+>", " ", str(summary_raw or "")))
            summary = re.sub(r"\s+", " ", summary).strip()
            if len(summary) > 250:
                summary = summary[:250] + "..."

            if ai_filter:
                text = f"{title} {summary}"
                if not (AI_KEYWORDS.search(text) or AI_KEYWORDS_ZH.search(text)):
                    continue

            if not github_with_usage_instruction({"title": title, "summary": summary, "url": link}):
                continue
            if PAPER_FILTER.search(title) or PAPER_FILTER.search(link):
                continue
            if FOOTER_TEXT_FILTER.search(title):
                continue

            normalized_date, inferred_date = normalize_entry_date(
                {
                    "published": (
                        entry.get("date_published", "")
                        or entry.get("published", "")
                        or entry.get("pubDate", "")
                        or entry.get("date", "")
                        or entry.get("publish_time", "")
                        or entry.get("created_at", "")
                    ),
                    "updated": (
                        entry.get("date_modified", "")
                        or entry.get("updated", "")
                        or entry.get("updated_at", "")
                        or entry.get("modified", "")
                    ),
                },
                link=link,
            )

            item = {
                "title": title,
                "url": link,
                "summary": summary,
                "source": source_name,
                "source_type": get_source_info(source_name)["type"],
                "date": normalized_date,
                "date_inferred": inferred_date,
                "fetched_at": _now_iso(),
                "score": 0,
            }
            if source_name == WECHAT_SOURCE_NAME:
                item["account_name"] = _extract_wechat_entry_account_name(entry, feed_title=feed_title)
            items.append(item)
    except Exception as e:
        print(f"  [WARN] {source_name} JSON feed parse failed: {e}")
    return items


def _fetch_werss_wechat_articles(source_name, max_entries=24):
    items = []
    seen = set()
    feed_urls = _build_werss_feed_urls()
    discovered_urls = _discover_werss_feed_urls()
    if discovered_urls:
        feed_urls.extend(discovered_urls)
    print(
        f"      [B.5] WeRSS feed discovery: base={len(_build_werss_feed_urls())} "
        f"+ discovered={len(discovered_urls)}"
    )
    for feed_url in feed_urls:
        try:
            if feed_url.lower().endswith(".json") or "format=json" in feed_url.lower():
                part = parse_json_feed(feed_url, source_name=source_name, max_entries=max_entries, ai_filter=False)
            else:
                part = parse_rss_feed(feed_url, source_name=source_name, max_entries=max_entries, ai_filter=False)
            for it in part:
                norm = it.get("url", "").rstrip("/")
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                items.append(it)
        except Exception:
            continue
    return items


def _fetch_rsshub_wechat_accounts(source_name, account_names, max_entries=20):
    urls = []
    route_candidates = [
        "wechat/ce/{keyword}",
        "wechat/accounts/{keyword}",
        "wechat/official/{keyword}",
    ]
    for base in RSSHUB_BASES:
        for account in account_names:
            encoded = quote_plus(account)
            for route in route_candidates:
                urls.append(f"{base}/{route.format(keyword=encoded).lstrip('/')}")
    return parse_rss_feed_candidates(
        urls=urls,
        source_name=source_name,
        max_entries=max_entries,
        ai_filter=False,
    )


def _decode_sogou_wechat_result_url(href):
    if not href:
        return ""
    full = href.strip()
    if full.startswith("/link?"):
        full = urljoin("https://weixin.sogou.com", full)
    if "mp.weixin.qq.com" in full:
        return unescape(full)
    try:
        parsed = urlparse(full)
        qs = parse_qs(parsed.query or "")
        for key in ("url", "target", "targeturl"):
            if key in qs and qs[key]:
                candidate = unquote(qs[key][0])
                if "mp.weixin.qq.com" in candidate:
                    return candidate
        for key in ("url", "target", "targeturl"):
            m = re.search(rf"(?:[?&]|amp;){key}=([^&]+)", full, re.IGNORECASE)
            if not m:
                continue
            candidate = unquote(unescape(m.group(1)))
            if "mp.weixin.qq.com" in candidate:
                return candidate
        m = re.search(r"https?%3A%2F%2Fmp\.weixin\.qq\.com%2F[^\"'&<>\s]+", full, re.IGNORECASE)
        if m:
            candidate = unquote(m.group(0))
            if "mp.weixin.qq.com" in candidate:
                return candidate
        m = re.search(r"https?://mp\.weixin\.qq\.com/[^\s\"'<>]+", full, re.IGNORECASE)
        if m:
            return unescape(m.group(0))
    except Exception:
        return ""
    return ""


def _extract_wechat_account_name(text):
    clean = unescape(re.sub(r"<[^>]+>", " ", text or ""))
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""
    for pattern in (
        r"公众号[:：\s]+([^\s|丨/]{2,40})",
        r"作者[:：\s]+([^\s|丨/]{2,40})",
        r"来源[:：\s]+([^\s|丨/]{2,40})",
    ):
        m = re.search(pattern, clean, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    for account in WECHAT_OFFICIAL_ACCOUNTS:
        if account and account.lower() in clean.lower():
            return account
    return ""


def _build_wechat_item(source_name, article_url, title, query="", summary="", account_name=""):
    return {
        "title": title,
        "url": article_url,
        "summary": summary or f"via {source_name} 搜索",
        "search_query": query,
        "source": source_name,
        "source_type": get_source_info(source_name)["type"],
        "date": "",
        "date_inferred": True,
        "account_name": account_name or "",
        "fetched_at": _now_iso(),
        "score": 0,
    }


def _fetch_sogou_wechat_search(source_name, queries, max_items=20):
    items = []
    seen = set()
    for q in queries[:WECHAT_SEARCH_QUERY_LIMIT]:
        try:
            url = f"https://weixin.sogou.com/weixin?type=2&query={quote_plus(q)}"
            resp = safe_request(
                url,
                timeout=LISTING_FETCH_TIMEOUT,
                headers={
                    "Referer": "https://weixin.sogou.com/",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            if not resp:
                continue
            html = resp.text or ""

            script_urls = re.findall(
                r"(https?://mp\.weixin\.qq\.com/s\?[^\s\"'<>]+)",
                html,
                re.IGNORECASE,
            )
            for article_url in script_urls[:8]:
                article_url = unescape(article_url).replace("\\/", "/")
                if not article_url or article_url in seen:
                    continue
                seen.add(article_url)
                items.append(
                    _build_wechat_item(
                        source_name=source_name,
                        article_url=article_url,
                        title=(q + " 相关文章").strip(),
                        query=q,
                        summary=f"via {source_name} 搜狗搜索",
                        account_name=_extract_wechat_account_name(html),
                    )
                )
                if len(items) >= max_items:
                    return items

            link_matches = re.findall(
                r'<a[^>]+href="([^"]+)"[^>]*uigs="article_title"[^>]*>(.*?)</a>',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if not link_matches:
                link_matches = re.findall(
                    r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?</h3>',
                    html,
                    re.IGNORECASE | re.DOTALL,
                )

            for href, raw_title in link_matches[:6]:
                article_url = _decode_sogou_wechat_result_url(href)
                if not article_url or article_url in seen:
                    continue
                title = unescape(re.sub(r"<[^>]+>", "", raw_title or "")).strip()
                if len(title) < 6:
                    continue
                snippet = ""
                block_re = re.escape(href)[:120]
                block_m = re.search(
                    rf"(<li[^>]*>.*?{block_re}.*?</li>)",
                    html,
                    re.IGNORECASE | re.DOTALL,
                )
                if block_m:
                    snippet = unescape(
                        re.sub(r"<[^>]+>", " ", block_m.group(1))
                    ).strip()
                seen.add(article_url)
                items.append(
                    _build_wechat_item(
                        source_name=source_name,
                        article_url=article_url,
                        title=title,
                        query=q,
                        summary=_truncate_text(snippet or f"via {source_name} 搜狗搜索", 220),
                        account_name=_extract_wechat_account_name(snippet),
                    )
                )
                if len(items) >= max_items:
                    return items
        except Exception:
            continue
    return items


def _fetch_bing_wechat_search(source_name, queries, max_items=20):
    items = []
    seen = set()
    for q in queries[:WECHAT_SEARCH_QUERY_LIMIT]:
        try:
            search_url = f"https://www.bing.com/search?q={quote_plus('site:mp.weixin.qq.com ' + q)}"
            resp = safe_request(
                search_url,
                timeout=LISTING_FETCH_TIMEOUT,
                headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            )
            if not resp:
                continue
            html = resp.text or ""
            if "mp.weixin.qq.com" in html:
                inline_urls = re.findall(
                    r"https?://mp\.weixin\.qq\.com/[^\s\"'<>]+",
                    html,
                    re.IGNORECASE,
                )
                for article_url in inline_urls[:6]:
                    article_url = unescape(article_url)
                    if not article_url or article_url in seen:
                        continue
                    seen.add(article_url)
                    items.append(
                        _build_wechat_item(
                            source_name=source_name,
                            article_url=article_url,
                            title=(q + " 微信文章").strip(),
                            query=q,
                            summary=f"via {source_name} Bing搜索",
                            account_name=_extract_wechat_account_name(html),
                        )
                    )
                    if len(items) >= max_items:
                        return items
            matches = re.findall(
                r'<li class="b_algo".*?<a href="([^"]+)"[^>]*>(.*?)</a>',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            for href, raw_title in matches[:6]:
                article_url = _decode_sogou_wechat_result_url(href)
                if "mp.weixin.qq.com" not in href and "mp.weixin.qq.com" not in article_url:
                    continue
                article_url = (article_url or href).strip()
                if not article_url or article_url in seen:
                    continue
                title = unescape(re.sub(r"<[^>]+>", "", raw_title or "")).strip()
                if len(title) < 6:
                    continue
                seen.add(article_url)
                items.append(
                    _build_wechat_item(
                        source_name=source_name,
                        article_url=article_url,
                        title=title,
                        query=q,
                        summary=f"via {source_name} Bing搜索",
                        account_name=_extract_wechat_account_name(raw_title),
                    )
                )
                if len(items) >= max_items:
                    return items
        except Exception:
            continue
    return items


def _now_iso():
    return datetime.now(BEIJING_TZ).isoformat()


def _to_iso_from_struct_time(st):
    try:
        if not st:
            return ""
        dt = datetime(st.tm_year, st.tm_mon, st.tm_mday, st.tm_hour, st.tm_min, st.tm_sec, tzinfo=timezone.utc)
        return dt.astimezone(BEIJING_TZ).isoformat()
    except Exception:
        return ""


def parse_relative_date_to_iso(text):
    """
    解析 YouTube/B站 常见相对时间文本，如：
    - 3 days ago / 7 hours ago / 2 weeks ago
    - 3天前 / 5小时前 / 1周前
    """
    if not text:
        return ""
    s = str(text).strip().lower()
    now = datetime.now(BEIJING_TZ)
    if s in {"just now", "刚刚", "刚刚发布", "刚刚更新", "today", "今日", "今天"}:
        return now.isoformat()
    if s in {"yesterday", "昨日", "昨天"}:
        return (now - timedelta(days=1)).isoformat()

    patterns = [
        (r"(\d+)\s*minute[s]?\s*ago", lambda n: now - timedelta(minutes=n)),
        (r"(\d+)\s*hour[s]?\s*ago", lambda n: now - timedelta(hours=n)),
        (r"(\d+)\s*day[s]?\s*ago", lambda n: now - timedelta(days=n)),
        (r"(\d+)\s*week[s]?\s*ago", lambda n: now - timedelta(days=7 * n)),
        (r"(\d+)\s*month[s]?\s*ago", lambda n: now - timedelta(days=30 * n)),
        (r"(\d+)\s*year[s]?\s*ago", lambda n: now - timedelta(days=365 * n)),
        (r"(\d+)\s*分钟前", lambda n: now - timedelta(minutes=n)),
        (r"(\d+)\s*小时[前内]", lambda n: now - timedelta(hours=n)),
        (r"(\d+)\s*天前", lambda n: now - timedelta(days=n)),
        (r"(\d+)\s*日前", lambda n: now - timedelta(days=n)),
        (r"(\d+)\s*周前", lambda n: now - timedelta(days=7 * n)),
        (r"(\d+)\s*个月前", lambda n: now - timedelta(days=30 * n)),
        (r"(\d+)\s*年前", lambda n: now - timedelta(days=365 * n)),
    ]
    for pattern, fn in patterns:
        m = re.search(pattern, s, re.IGNORECASE)
        if m:
            try:
                return fn(int(m.group(1))).isoformat()
            except Exception:
                return ""
    return ""


def normalize_date_like_text_to_day(raw_text):
    if not raw_text:
        return ""
    text = unescape(str(raw_text)).strip()
    if not text:
        return ""

    dt = parse_date_to_beijing(text)
    if dt:
        return dt.strftime("%Y-%m-%d")

    m = re.search(r"(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"

    m = re.search(r"(\d{4})(\d{2})(\d{2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"

    rel = parse_relative_date_to_iso(text)
    if rel:
        try:
            return datetime.fromisoformat(rel).strftime("%Y-%m-%d")
        except Exception:
            return ""
    return ""


def normalize_entry_date(entry, link=""):
    """
    统一归一化 feed 条目日期：
    1) published/updated
    2) published_parsed/updated_parsed
    3) URL 日期
    4) 兜底使用当前时间（并标记 inferred）
    """
    candidates = [
        entry.get("published", ""),
        entry.get("updated", ""),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip(), False

    parsed_candidates = [
        entry.get("published_parsed"),
        entry.get("updated_parsed"),
    ]
    for st in parsed_candidates:
        iso = _to_iso_from_struct_time(st)
        if iso:
            return iso, False

    from_url = extract_date_from_url(link or "")
    if from_url:
        return from_url, False

    return _now_iso(), True


def parse_date_to_beijing(date_val):
    """
    解析任意 date 字段为北京时间 datetime；失败返回 None
    """
    try:
        if isinstance(date_val, datetime):
            return date_val.astimezone(BEIJING_TZ) if date_val.tzinfo else date_val.replace(tzinfo=BEIJING_TZ)
        if not date_val:
            return None
        if isinstance(date_val, str):
            s = date_val.strip()
            if not s:
                return None
            if re.match(r"^\d{10}$", s):
                return datetime.fromtimestamp(int(s), tz=timezone.utc).astimezone(BEIJING_TZ)
            if re.match(r"^\d{13}$", s):
                return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc).astimezone(BEIJING_TZ)
            relative_iso = parse_relative_date_to_iso(s)
            if relative_iso:
                return datetime.fromisoformat(relative_iso)
            if "T" in s:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(BEIJING_TZ)
            if re.match(r"\d{4}-\d{2}-\d{2}$", s):
                return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
            if re.match(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", s):
                return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING_TZ)
            try:
                return parsedate_to_datetime(s).astimezone(BEIJING_TZ)
            except Exception:
                return None
    except Exception:
        return None


def extract_page_published_date(url):
    """
    从页面 HTML / JSON-LD 中提取真实发布日期，返回 YYYY-MM-DD；失败返回空字符串。
    """
    if not url:
        return ""
    cached = PAGE_DATE_CACHE.get(url)
    if cached is not None:
        return cached

    def _normalize_candidate(raw):
        if raw in (None, ""):
            return ""
        s = str(raw).strip()
        if re.match(r"^\d{10}$", s):
            try:
                return datetime.fromtimestamp(int(s), tz=timezone.utc).astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
            except Exception:
                return ""
        dt = parse_date_to_beijing(s)
        if dt:
            return dt.strftime("%Y-%m-%d")
        m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            return m.group(1)
        return ""

    patterns = [
        r'<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="publish-date"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="publish_date"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="pubdate"[^>]+content="([^"]+)"',
        r'<meta[^>]+itemprop="datePublished"[^>]+content="([^"]+)"',
        r'<time[^>]+datetime="([^"]+)"',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"uploadDate"\s*:\s*"([^"]+)"',
        r'"publishDate"\s*:\s*"([^"]+)"',
        r'"published(?:At|_at)?"\s*:\s*"([^"]+)"',
        r'"pub(?:lished)?date"\s*:\s*"([^"]+)"',
        r'"pubdate"\s*:\s*(\d{10})',
        r'\bvar\s+publish_time\s*=\s*"([^"]+)"',
        r'\bpublish_time\s*[:=]\s*"([^"]+)"',
        r'\bcreateTime\s*[:=]\s*"?(10\d{8,11})"?',
        r'\bct\s*=\s*"?(10\d{8,11})"?',
    ]
    try:
        resp = safe_request(url, timeout=ARTICLE_FETCH_TIMEOUT)
        if not resp:
            PAGE_DATE_CACHE[url] = ""
            return ""
        html = resp.text or ""
        if "mp.weixin.qq.com/" in url:
            for pattern in (
                r'\bvar\s+ct\s*=\s*"?(10\d{8,11})"?',
                r'\bct\s*=\s*"?(10\d{8,11})"?',
                r'\bpublish_time\s*[:=]\s*"?(10\d{8,11})"?',
                r'"publish_time"\s*:\s*"?(10\d{8,11})"?',
                r'"createTime"\s*:\s*"?(10\d{8,11})"?',
            ):
                m = re.search(pattern, html, re.IGNORECASE)
                if m:
                    normalized = _normalize_candidate(m.group(1))
                    if normalized:
                        PAGE_DATE_CACHE[url] = normalized
                        return normalized
            for pattern in (
                r'\bpublish_time\s*[:=]\s*"([^"]+)"',
                r'"publish_time"\s*:\s*"([^"]+)"',
            ):
                m = re.search(pattern, html, re.IGNORECASE)
                if m:
                    normalized = _normalize_candidate(m.group(1))
                    if normalized:
                        PAGE_DATE_CACHE[url] = normalized
                        return normalized
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if m:
                normalized = _normalize_candidate(unescape(m.group(1)))
                if normalized:
                    PAGE_DATE_CACHE[url] = normalized
                    return normalized
        for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
            clean = unescape(block)
            for pattern in (
                r'"datePublished"\s*:\s*"([^"]+)"',
                r'"uploadDate"\s*:\s*"([^"]+)"',
                r'"dateCreated"\s*:\s*"([^"]+)"',
            ):
                m = re.search(pattern, clean, re.IGNORECASE | re.DOTALL)
                if m:
                    normalized = _normalize_candidate(m.group(1))
                    if normalized:
                        PAGE_DATE_CACHE[url] = normalized
                        return normalized
    except Exception:
        pass
    PAGE_DATE_CACHE[url] = ""
    return ""
    return None


def normalize_yt_dlp_date(value):
    if not value:
        return ""
    s = str(value).strip()
    if re.match(r"^\d{8}$", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    return ""


def normalize_yt_dlp_timestamp(value):
    if value in (None, ""):
        return ""
    try:
        ts = float(value)
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _run_yt_dlp_json(url):
    """
    优先用 yt-dlp 获取 YouTube 元数据。未安装或失败时返回 None，不影响主流程。
    """
    commands = []
    exe = shutil.which("yt-dlp")
    if exe:
        commands.append([exe])
    commands.append([sys.executable, "-m", "yt_dlp"])

    for prefix in commands:
        try:
            cmd = prefix + [
                "--dump-single-json",
                "--skip-download",
                "--no-warnings",
                "--no-playlist",
                url,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=YTDLP_TIMEOUT,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            data = json.loads(proc.stdout)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _extract_youtube_published_date_by_ytdlp(url):
    data = _run_yt_dlp_json(url)
    if not data:
        return "", "low"

    for key in ("upload_date", "release_date", "modified_date"):
        date_str = normalize_yt_dlp_date(data.get(key))
        if date_str:
            return date_str, "high"

    for key in ("release_timestamp", "timestamp"):
        date_str = normalize_yt_dlp_timestamp(data.get(key))
        if date_str:
            return date_str, "high"

    return "", "low"


def is_within_days(date_val, days):
    dt = parse_date_to_beijing(date_val)
    if not dt:
        return False
    delta = datetime.now(BEIJING_TZ) - dt
    return timedelta(0) <= delta <= timedelta(days=days)


def normalize_social_url(url):
    """
    将 Twitter/X 原链接转换为更可访问链接；不改变业务语义。
    """
    if not url:
        return url
    u = url.strip()
    if "x.com/" in u:
        return build_alt_social_url(u)
    if "twitter.com/" in u:
        return build_alt_social_url(u)
    if "weibo.com/" in u:
        return build_alt_social_url(u)
    return u


def build_reader_url(url):
    if not url:
        return url
    target = url.strip().replace("https://", "").replace("http://", "")
    return f"{JINA_READER_PREFIX}{target}"


def build_alt_social_url(url):
    """
    社媒链接公司网络兼容策略：
    - 统一用 r.jina.ai 代理阅读入口
    """
    return build_reader_url(url)


def github_with_usage_instruction(item):
    """
    GitHub 仅允许“有使用说明/教程”的优质应用内容。
    """
    title = item.get("title", "")
    summary = item.get("summary", "")
    url = item.get("url", "")
    text = f"{title} {summary} {url}"
    if not is_github_url(url) and not GITHUB_TITLE_FILTER.search(title):
        return True
    return bool(GITHUB_USAGE_FILTER.search(text))


def build_item_filter_text(item, include_query=False):
    parts = [
        item.get("title", ""),
        item.get("summary", ""),
        item.get("title_zh", ""),
        item.get("summary_zh", ""),
        item.get("url", ""),
    ]
    if include_query:
        parts.append(item.get("search_query", ""))
    return " ".join(str(p) for p in parts if p)


def is_non_actionable_page(item):
    """
    拦截营销页/白皮书/研究报告类页面。
    必须是可直接学习、可直接使用、带明确操作线索的内容才放行。
    """
    url = item.get("url", "")
    text = build_item_filter_text(item, include_query=False)
    actionable_hit = bool(PRACTICE_REQUIRED_PATTERN.search(text) or GITHUB_USAGE_FILTER.search(text))
    if NON_ACTIONABLE_URL_FILTER.search(url) and not actionable_hit:
        return True
    if NON_ACTIONABLE_TEXT_FILTER.search(text) and not actionable_hit:
        return True
    return False


def is_non_practical_news(item):
    """
    拦截事故、八卦、诉讼、治安事件等“非实践型 AI 新闻”。
    """
    text = build_item_filter_text(item, include_query=False)
    if re.search(
        r"building-trust-in-the-ai-era-with-privacy-led-ux|privacy-led\s+ux|privacy\s+led\s+ux|隐私.*用户体验|用户体验.*隐私",
        text,
        re.IGNORECASE,
    ):
        return True
    actionable_hit = bool(PRACTICE_REQUIRED_PATTERN.search(text) or GITHUB_USAGE_FILTER.search(text))
    return bool(NON_PRACTICAL_NEWS_FILTER.search(text) and not actionable_hit)


def practical_video_gate(item):
    """
    视频源轻量门槛：标题/摘要命中 AI 核心即可先进入候选。
    搜索 query 本身已经是实践型，后续摘要与总排序再二次把关。
    """
    core_text = build_item_filter_text(item, include_query=False)
    support_text = build_item_filter_text(item, include_query=True)
    if EXCLUDE_PATTERN.search(support_text):
        return False
    if is_non_actionable_page(item) or is_non_practical_news(item):
        return False
    return bool(AI_CORE_PATTERN.search(core_text) or ORDINARY_HINT_PATTERN.search(support_text))


def frontier_innovation_gate(item):
    """
    技术前沿门槛：
    - 明确是 AI 相关
    - 明确命中新模型 / 新研发 / 基准 / 发布 / 技术突破
    - 排除白皮书、营销页、事故/八卦、投资商业噪声
    """
    core_text = build_item_filter_text(item, include_query=False)
    support_text = build_item_filter_text(item, include_query=True)
    model_hit = bool(MODEL_SIGNAL.search(core_text))
    innovation_hit = bool(INNOVATION_SIGNAL.search(core_text) or TECH_BOOST.search(core_text))
    entity_hit = bool(HOT_ENTITY.search(core_text))
    if EXCLUDE_PATTERN.search(support_text):
        return False
    if is_non_actionable_page(item):
        return False
    if is_non_practical_news(item):
        return False
    if FUNDING_POLICY_FILTER.search(core_text) or ENTERPRISE_BIZ_FILTER.search(core_text):
        return False
    if not AI_CORE_PATTERN.search(core_text):
        return False
    if innovation_hit and (model_hit or entity_hit):
        return True
    return False


def practical_keyword_gate(item):
    """
    实用导向硬门槛：
    - 必须命中 AI 核心信号
    - 必须命中实操/教程/API/开源/工具等可落地信号
    - 排除营销页、白皮书、事故新闻和商业噪声
    - 搜索来源可用 query 补足“教程/工作流”语义，但 AI 核心词必须来自标题/摘要/URL 本身
    """
    core_text = build_item_filter_text(item, include_query=False)
    support_text = build_item_filter_text(item, include_query=True)

    if EXCLUDE_PATTERN.search(support_text):
        return False
    if is_non_actionable_page(item):
        return False
    if is_non_practical_news(item):
        return False
    if LOW_VALUE_SIGNAL.search(core_text) and not PRACTICE_REQUIRED_PATTERN.search(core_text):
        return False
    if not AI_CORE_PATTERN.search(core_text):
        return False
    if not PRACTICE_REQUIRED_PATTERN.search(support_text):
        return False
    return True


def resolve_google_news_redirect(url):
    """
    尝试从 Google News RSS 中转链接解析真实外链。
    解析失败时返回空字符串，避免把 news.google.com 中转链接推送出去。
    """
    if not url or "news.google.com/rss/articles/" not in url:
        return url
    try:
        resp = safe_request(
            url,
            timeout=12,
            headers={"Accept": "text/html,application/xhtml+xml"},
            trust_env=False,
        )
        if resp is not None and resp.url and "news.google.com" not in resp.url:
            return resp.url
        html = resp.text if resp is not None else ""
        m = re.search(
            r'https?://[^\s"\'<>]+',
            html,
            re.IGNORECASE,
        )
        if m:
            candidate = m.group(0)
            if "news.google.com" not in candidate:
                return candidate
    except Exception:
        pass
    return ""


def is_theverge_paywalled(item):
    """
    The Verge 付费墙检测：标题/摘要/URL 和页面 HTML 任一命中锁文标记即剔除。
    """
    t = item.get("title", "")
    s = item.get("summary", "")
    u = item.get("url", "")
    text = f"{t} {s} {u}"
    if re.search(r"subscriber|subscription|paywall|exclusive|members.?only|premium|subscribe|sign in to continue|unlock", text, re.IGNORECASE):
        return True
    try:
        resp = safe_request(u, timeout=10, headers={"Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"})
        if not resp or resp.status_code >= 400:
            return True
        html = resp.text or ""
        if re.search(
            r"paywall|subscriber.?only|members.?only|subscribe to continue|sign in to continue"
            r"|data-testid=.paywall|duet--article--paywall|unlock this article|continue reading with",
            html,
            re.IGNORECASE,
        ):
            return True
    except Exception:
        return True
    return False


def is_wired_paywalled(item):
    t = item.get("title", "")
    s = item.get("summary", "")
    u = item.get("url", "")
    text = f"{t} {s} {u}"
    # Wired 常见付费文会带 premium 或 subscriber 特征，标题也会出现 subscribers only
    if re.search(r"premium|subscriber|subscribers.?only|paywall|membership|unlock this story|continue reading", text, re.IGNORECASE):
        return True
    try:
        resp = safe_request(u, timeout=12, headers={"Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"})
        if not resp or resp.status_code >= 400:
            return True
        html = resp.text or ""
        if not html:
            return True
        # Wired 锁文页常带这些标记；宁可少推，也不要把需付费文章推送出去
        if re.search(
            r"paywall|subscriber.?only|subscribe to continue|unlimited digital access|this story is available exclusively"
            r"|client:meteredPaywall|meteredPaywall|OfferManager|requires subscription|unlock this story",
            html,
            re.IGNORECASE,
        ):
            return True
    except Exception:
        return True
    return False


def warmup_sina_homepage():
    """
    先打开新浪科技首页，获得 cookie，再抓详情页，降低直链失败概率。
    """
    try:
        safe_request("https://tech.sina.com.cn/", timeout=10)
    except Exception:
        pass


def parse_rss_feed(url, source_name, max_entries=20, ai_filter=False):
    items = []
    try:
        feed_resp = safe_request(
            url,
            timeout=RSS_FETCH_TIMEOUT,
            headers={"Accept": "application/rss+xml, application/xml, text/xml, */*"},
        )
        feed = feedparser.parse(feed_resp.content if feed_resp is not None else url)
        feed_title = str(getattr(feed, "feed", {}).get("title", "") or "").strip()
        for entry in feed.entries[:max_entries]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "")
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            if len(summary) > 250:
                summary = summary[:250] + "..."

            if "news.google.com/rss/articles/" in link:
                resolved_link = resolve_google_news_redirect(link)
                if not resolved_link or "news.google.com/rss/articles/" in resolved_link:
                    continue
                link = resolved_link

            if ai_filter:
                text = f"{title} {summary}"
                if not (AI_KEYWORDS.search(text) or AI_KEYWORDS_ZH.search(text)):
                    continue

            # GitHub 允许但需带使用说明/教程
            if not github_with_usage_instruction({"title": title, "summary": summary, "url": link}):
                continue
            if PAPER_FILTER.search(title) or PAPER_FILTER.search(link):
                continue
            # ★ v3.1 拦截底部备案文本
            if FOOTER_TEXT_FILTER.search(title):
                continue

            normalized_date, inferred_date = normalize_entry_date(entry, link=link)
            item = {
                "title": title,
                "url": link,
                "summary": summary,
                "source": source_name,
                "source_type": get_source_info(source_name)["type"],
                "date": normalized_date,
                "date_inferred": inferred_date,
                "fetched_at": _now_iso(),
                "score": 0,
            }
            if source_name == WECHAT_SOURCE_NAME:
                item["account_name"] = _extract_wechat_entry_account_name(entry, feed_title=feed_title)
            items.append(item)
    except Exception as e:
        print(f"  [WARN] {source_name} RSS parse failed: {e}")
    return items

def scrape_links_from_page(url, source_name, link_pattern=None,
                           title_min_len=10, max_items=15, ai_filter=True):
    items = []
    try:
        resp = safe_request(url)
        if not resp:
            return items
        html = resp.text

        if link_pattern:
            links = link_pattern.findall(html)
        else:
            links = re.findall(
                r'<a[^>]+href="(https?://[^"]+)"[^>]*>\s*([^<]{10,}?)\s*</a>',
                html,
            )

        seen = set()
        for link_url, title in links:
            title = title.strip()
            title = re.sub(r"\s+", " ", title)
            if (
                len(title) >= title_min_len
                and link_url not in seen
                and "advertiser" not in link_url.lower()
                and "sponsor" not in title.lower()
            ):
                if not github_with_usage_instruction({"title": title, "summary": f"via {source_name}", "url": link_url}):
                    continue
                # ★ v3.1 拦截底部备案文本和硬封禁域名
                if FOOTER_TEXT_FILTER.search(title):
                    continue
                if HARD_BLOCK_DOMAINS.search(link_url):
                    continue
                    
                if ai_filter:
                    if not (AI_KEYWORDS.search(title) or AI_KEYWORDS_ZH.search(title)):
                        continue
                seen.add(link_url)
                extracted_date = extract_date_from_url(link_url)
                items.append({
                    "title": title,
                    "url": link_url,
                    "summary": f"via {source_name}",
                    "source": source_name,
                    "source_type": get_source_info(source_name)["type"],
                    # 优先 URL 日期，缺失时回退当前抓取时间（带 inferred 标记）
                    "date": extracted_date or _now_iso(),
                    "date_inferred": not bool(extracted_date),
                    "fetched_at": _now_iso(),
                    "score": 0,
                })
                if len(items) >= max_items:
                    break
    except Exception as e:
        print(f"  [WARN] {source_name} scrape failed: {e}")
    return items

# ══════════════════════════════════════════════════════════════════════════════
# v3.0 新增：文章正文抓取（为 LLM 摘要提供充分上下文）
# ══════════════════════════════════════════════════════════════════════════════

def _truncate_text(text, max_chars):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


def _extract_youtube_subtitles(url, max_chars=ARTICLE_EXCERPT_MAX_CHARS):
    try:
        resp = safe_request(url, timeout=ARTICLE_FETCH_TIMEOUT)
        if not resp:
            return ""
        html = resp.text

        track_urls = re.findall(r'"baseUrl":"(https:[^"]+timedtext[^"]+)"', html)
        for raw in track_urls:
            subtitle_url = raw.replace("\\u0026", "&").replace("\\/", "/")
            try:
                sub_resp = safe_request(subtitle_url, timeout=ARTICLE_FETCH_TIMEOUT)
                if not sub_resp:
                    continue
                text_nodes = re.findall(r"<text[^>]*>(.*?)</text>", sub_resp.text, flags=re.DOTALL | re.IGNORECASE)
                if not text_nodes:
                    continue
                caption = " ".join(unescape(t) for t in text_nodes)
                caption = re.sub(r"<[^>]+>", " ", caption)
                caption = _truncate_text(caption, max_chars)
                if len(caption) >= 80:
                    return caption
            except Exception:
                continue

        # 回退：抓 shortDescription
        m = re.search(r'"shortDescription":"([^"]{40,})"', html)
        if m:
            desc = m.group(1).replace("\\n", " ").replace("\\/", "/")
            return _truncate_text(unescape(desc), max_chars)
    except Exception:
        pass
    return ""


def _extract_bilibili_subtitles(url, max_chars=ARTICLE_EXCERPT_MAX_CHARS):
    try:
        resp = safe_request(url, timeout=ARTICLE_FETCH_TIMEOUT, headers={"Referer": "https://www.bilibili.com/"})
        if not resp:
            return ""
        html = resp.text

        sub_urls = re.findall(r'"subtitle_url":"([^"]+)"', html)
        for raw in sub_urls:
            sub_url = raw.replace("\\u002F", "/").replace("\\/", "/")
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            elif sub_url.startswith("/"):
                sub_url = "https://api.bilibili.com" + sub_url
            try:
                sub_resp = safe_request(sub_url, timeout=ARTICLE_FETCH_TIMEOUT, headers={"Referer": "https://www.bilibili.com/"})
                if not sub_resp:
                    continue
                data = sub_resp.json() if "application/json" in sub_resp.headers.get("Content-Type", "") else {}
                body = data.get("body", []) if isinstance(data, dict) else []
                if body:
                    caption = " ".join(x.get("content", "") for x in body if isinstance(x, dict))
                    caption = _truncate_text(caption, max_chars)
                    if len(caption) >= 80:
                        return caption
            except Exception:
                continue

        # 回退：简介
        m = re.search(r'"desc":"([^"]{30,})"', html)
        if m:
            desc = m.group(1).replace("\\n", " ").replace("\\/", "/")
            return _truncate_text(unescape(desc), max_chars)
    except Exception:
        pass
    return ""


def _extract_youtube_published_date(url):
    """
    从 YouTube 页面解析真实发布日期。
    返回 (date_str, confidence)，其中 confidence 为 high / medium / low。
    """
    ytdlp_date, ytdlp_conf = _extract_youtube_published_date_by_ytdlp(url)
    if ytdlp_date:
        return ytdlp_date, ytdlp_conf

    try:
        resp = safe_request(
            url,
            timeout=ARTICLE_FETCH_TIMEOUT,
            headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36",
            },
        )
        if not resp:
            return "", "low"
        html = resp.text

        patterns = [
            r'"publishDate"\s*:\s*"(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
            r'"uploadDate"\s*:\s*"(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
            r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
            r'itemprop="datePublished"\s+content="(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
            r'itemprop="uploadDate"\s+content="(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
            r'<meta[^>]+property="og:video:release_date"[^>]+content="(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                return m.group(1), "high"

        # ytInitialPlayerResponse / microformat 中经常包含更稳定的 liveBroadcastDetails 或 publishDate
        initial_patterns = [
            r'"liveBroadcastDetails"\s*:\s*\{[^{}]*"startTimestamp"\s*:\s*"(\d{4}-\d{2}-\d{2})T',
            r'"publishDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
            r'"uploadDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
        ]
        for pattern in initial_patterns:
            m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(1), "high"

        json_blocks = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL)
        for block in json_blocks:
            clean = unescape(block)
            for pattern in (
                r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
                r'"uploadDate"\s*:\s*"(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
                r'"publishDate"\s*:\s*"(\d{4}-\d{2}-\d{2})(?:T[^"]*)?"',
            ):
                m = re.search(pattern, clean, re.IGNORECASE)
                if m:
                    return m.group(1), "medium"

        # 页面相对时间兜底（如 "3 days ago"/"3天前"/"昨日"）
        rel_patterns = [
            r'"publishedTimeText"\s*:\s*\{"simpleText":"([^"]+)"\}',
            r'"dateText"\s*:\s*\{"simpleText":"([^"]+)"\}',
            r'"relativeDateText"\s*:\s*\{"simpleText":"([^"]+)"\}',
            r'"label":"([^"]*(?:day|days|week|weeks|month|months|year|years)\s+ago[^"]*)"',
            r'"label":"([^"]*[\d一二三四五六七八九十]+\s*(?:天前|周前|个月前|年前|小时前|分钟前)[^"]*)"',
        ]
        for pattern in rel_patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if not m:
                continue
            maybe_day = normalize_date_like_text_to_day(m.group(1))
            if maybe_day:
                return maybe_day, "medium"

        # 通用页面发布日期兜底
        fallback_day = extract_page_published_date(url)
        if fallback_day:
            return fallback_day, "low"
    except Exception:
        pass
    return "", "low"


def _extract_bilibili_published_date(url):
    """
    从 B 站页面解析发布时间（优先）。
    """
    try:
        resp = safe_request(url, timeout=ARTICLE_FETCH_TIMEOUT, headers={"Referer": "https://www.bilibili.com/"})
        if not resp:
            return ""
        html = resp.text

        # 优先提取 timestamp（pubdate/ctime/publish_time）
        ts_patterns = [
            r'"pubdate"\s*:\s*(\d{10,13})',
            r'"ctime"\s*:\s*(\d{10,13})',
            r'"publish_time"\s*:\s*(\d{10,13})',
        ]
        for pattern in ts_patterns:
            m = re.search(pattern, html)
            if not m:
                continue
            ts = m.group(1)
            dt = parse_date_to_beijing(ts)
            if dt:
                return dt.strftime("%Y-%m-%d")

        # 再提取绝对日期字符串
        abs_patterns = [
            r'"pub_time"\s*:\s*"([^"]+)"',
            r'"pubTime"\s*:\s*"([^"]+)"',
            r'"datePublished"\s*:\s*"([^"]+)"',
            r'itemprop="datePublished"\s+content="([^"]+)"',
            r'"publishDate"\s*:\s*"([^"]+)"',
            r'发布时间[:：]?\s*([0-9]{4}[年/\-.][0-9]{1,2}[月/\-.][0-9]{1,2})',
        ]
        for pattern in abs_patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if not m:
                continue
            maybe_day = normalize_date_like_text_to_day(m.group(1))
            if maybe_day:
                return maybe_day

        # JSON-LD 兜底
        for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
            clean = unescape(block)
            for pattern in (
                r'"datePublished"\s*:\s*"([^"]+)"',
                r'"uploadDate"\s*:\s*"([^"]+)"',
                r'"dateCreated"\s*:\s*"([^"]+)"',
            ):
                m = re.search(pattern, clean, re.IGNORECASE)
                if not m:
                    continue
                maybe_day = normalize_date_like_text_to_day(m.group(1))
                if maybe_day:
                    return maybe_day
    except Exception:
        pass
    return ""


def fetch_content_context(item, max_chars=ARTICLE_EXCERPT_MAX_CHARS):
    """
    统一上下文抓取入口：
    1) 普通新闻页：正文抽取
    2) 视频/社媒页：优先字幕，其次页面描述，最后标题+摘要（防幻觉）
    """
    url = item.get("url", "")
    title = item.get("title", "")
    summary = item.get("summary", "")
    source = item.get("source", "")
    domain = urlparse(url).netloc.lower()

    # YouTube 字幕优先
    if "youtube.com" in domain or "youtu.be" in domain:
        sub = _extract_youtube_subtitles(url, max_chars=max_chars)
        if sub:
            item["_context_mode"] = "subtitle"
            return sub
        item["_context_mode"] = "title_only"
        return _truncate_text(f"{title}。{summary}", max_chars)

    # B站字幕优先
    if "bilibili.com" in domain or "b23.tv" in domain:
        sub = _extract_bilibili_subtitles(url, max_chars=max_chars)
        if sub:
            item["_context_mode"] = "subtitle"
            return sub
        item["_context_mode"] = "title_only"
        return _truncate_text(f"{title}。{summary}", max_chars)

    # 微博/Twitter/X 优先走标题+摘要，避免页面动态结构导致空抓取
    if any(x in domain for x in ["weibo.com", "twitter.com", "x.com"]):
        item["_context_mode"] = "title_only"
        return _truncate_text(f"{title}。{summary}", max_chars)

    # 非社媒页走正文抓取
    article_text = fetch_article_excerpt(url, max_chars=max_chars)
    if article_text:
        item["_context_mode"] = "article"
        return article_text

    item["_context_mode"] = "title_only"
    return _truncate_text(f"{title}。{summary}", max_chars)


def fetch_article_excerpt(url, max_chars=ARTICLE_EXCERPT_MAX_CHARS):
    """
    抓取文章页面并提取正文文本摘要。
    为 LLM 生成摘要提供充分的原文上下文，从根源上减少幻觉。
    """
    try:
        resp = safe_request(url, timeout=ARTICLE_FETCH_TIMEOUT)
        if not resp:
            return ""
        html = resp.text

        if "mp.weixin.qq.com/" in url:
            wechat_blocks = []
            for pattern in (
                r'<div[^>]+id="js_content"[^>]*>(.*?)</div>',
                r'<section[^>]+id="js_content"[^>]*>(.*?)</section>',
                r'<div[^>]+class="[^"]*rich_media_content[^"]*"[^>]*>(.*?)</div>',
                r'<div[^>]+class="[^"]*rich_media_area_primary_inner[^"]*"[^>]*>(.*?)</div>',
            ):
                m = re.search(pattern, html, flags=re.DOTALL | re.IGNORECASE)
                if m:
                    wechat_blocks.append(m.group(1))
            if wechat_blocks:
                wechat_text = unescape(
                    re.sub(r"<[^>]+>", " ", " ".join(wechat_blocks))
                )
                wechat_text = re.sub(r"\s+", " ", wechat_text).strip()
                wechat_text = re.sub(r"赞赏.*$", "", wechat_text)
                if len(wechat_text) >= 50:
                    return _truncate_text(wechat_text, max_chars)

        # 移除 script / style / nav / footer 等无关标签
        for tag in ["script", "style", "nav", "footer", "header", "aside", "noscript"]:
            html = re.sub(
                rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE
            )

        # 移除 HTML 注释
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

        # 优先提取 <article> 或 <main> 标签内容
        article_match = re.search(
            r"<(?:article|main)[^>]*>(.*?)</(?:article|main)>",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if article_match:
            content_html = article_match.group(1)
        else:
            # 回退：提取 <p> 标签内容
            paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.DOTALL | re.IGNORECASE)
            content_html = " ".join(paragraphs)

        # 清除剩余 HTML 标签
        text = re.sub(r"<[^>]+>", " ", content_html)
        # 清除多余空白 + HTML 实体解码
        text = unescape(re.sub(r"\s+", " ", text).strip())

        if len(text) < 50:
            return ""

        return _truncate_text(text, max_chars)
    except Exception as e:
        print(f"      [v3.0] 文章正文抓取失败 ({url[:60]}...): {e}")
        return ""

# ══════════════════════════════════════════════════════════════════════════════
# v3.0 新增：摘要事实校验（检测明显的公司-模型错误归属）
# ══════════════════════════════════════════════════════════════════════════════

def validate_summary_facts(title_zh, summary_zh, article_text=""):
    """
    校验生成的摘要中是否存在明显的公司-模型错误归属。
    返回 (is_valid, error_msg)
    """
    combined = f"{title_zh} {summary_zh}"
    errors = []

    # 检测模式："{公司A}的{模型B}"，其中模型B实际不属于公司A
    # 常见错误归属模式
    attribution_patterns = [
        # "Meta的Opus" / "Meta的Claude"
        (r"Meta.{0,5}(?:Opus|Claude|Sonnet|Haiku)", "Claude/Opus 系列属于 Anthropic，不属于 Meta"),
        # "Google的GPT" / "Google的ChatGPT"
        (r"Google.{0,5}(?:GPT|ChatGPT|DALL-E|Sora)", "GPT/ChatGPT 系列属于 OpenAI，不属于 Google"),
        # "OpenAI的Gemini"
        (r"OpenAI.{0,5}(?:Gemini|Bard|PaLM|Gemma)", "Gemini 系列属于 Google，不属于 OpenAI"),
        # "Anthropic的GPT"
        (r"Anthropic.{0,5}(?:GPT|ChatGPT|Llama|Gemini)", "GPT 属于 OpenAI，Llama 属于 Meta，Gemini 属于 Google"),
        # "Meta的Gemini"
        (r"Meta.{0,5}Gemini", "Gemini 属于 Google，不属于 Meta"),
        # "OpenAI的Llama"
        (r"OpenAI.{0,5}Llama", "Llama 属于 Meta，不属于 OpenAI"),
        # "Google的Llama"
        (r"Google.{0,5}Llama", "Llama 属于 Meta，不属于 Google"),
        # "Meta的GLM"
        (r"Meta.{0,5}GLM", "GLM 属于智谱AI，不属于 Meta"),
        # "OpenAI的GLM"
        (r"OpenAI.{0,5}GLM", "GLM 属于智谱AI，不属于 OpenAI"),
        # "百度的Qwen/通义"
        (r"百度.{0,5}(?:Qwen|通义千问)", "通义千问/Qwen 属于阿里，不属于百度"),
        # "阿里的文心"
        (r"阿里.{0,5}文心", "文心一言属于百度，不属于阿里"),
    ]

    for pattern, msg in attribution_patterns:
        if re.search(pattern, combined, re.IGNORECASE):
            errors.append(msg)

    if errors:
        return False, "; ".join(errors)
    return True, ""

# ══════════════════════════════════════════════════════════════════════════════
# A. 聚合源抓取
# ══════════════════════════════════════════════════════════════════════════════

def fetch_tldr():
    items = []
    source = "TLDR.tech"
    today = datetime.now(BEIJING_TZ)
    try:
        for delta in range(2):
            date_str = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
            try:
                resp = safe_request(f"https://tldr.tech/ai/{date_str}")
                if resp and resp.status_code == 200 and len(resp.text) > 1000:
                    items = _parse_tldr_page(resp.text, date_str, source)
                    if items:
                        break
            except Exception:
                continue

        if not items:
            resp = safe_request("https://tldr.tech/ai/archives")
            if resp:
                dates = re.findall(r"/ai/(\d{4}-\d{2}-\d{2})", resp.text)
                if dates:
                    latest = sorted(dates, reverse=True)[0]
                    detail = safe_request(f"https://tldr.tech/ai/{latest}")
                    if detail:
                        items = _parse_tldr_page(detail.text, latest, source)
    except Exception as e:
        print(f"  [WARN] {source} fetch failed: {e}")

    tracker.record(source, items)
    return items

def _parse_tldr_page(html, date_str, source):
    items = []
    links = re.findall(
        r'<a[^>]+href="(https?://(?!tldr\.tech)[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
        html,
    )
    seen = set()
    for url, title in links:
        title = title.strip()
        if (
            len(title) > 15
            and url not in seen
            and not url.startswith("https://tldr.tech")
            and "advertiser" not in url.lower()
            and "sponsor" not in title.lower()
            and not is_github_url(url)
            and not GITHUB_TITLE_FILTER.search(title)
            and not HARD_BLOCK_DOMAINS.search(url)
        ):
            seen.add(url)
            items.append({
                "title": title,
                "url": url,
                "summary": f"via TLDR AI ({date_str})",
                "source": source,
                "source_type": "intl",
                "date": date_str,
                "score": 0,
            })
    return items

def fetch_hackernews():
    items = []
    source = "Hacker News"
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10
        )
        top_ids = resp.json()[:HN_TOP_N]
        for sid in top_ids:
            try:
                story = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5
                ).json()
            except Exception:
                continue
            if not story or story.get("type") != "story":
                continue
            title = story.get("title", "")
            original_url = story.get("url", "")
            hn_url = f"https://news.ycombinator.com/item?id={sid}"
            url = original_url

            # 纯 HN 讨论串 / Ask HN / Show HN / 无外链帖子，不推送
            if not original_url:
                continue
            if is_hn_discussion_url(original_url):
                continue
            if re.search(r"^\s*(ask|show|tell|launch)\s+hn\b", title, re.IGNORECASE):
                continue

            # HN 中若外链本身是营销页、产品首页、封闭入口页，直接丢弃，不再退回讨论串
            if (PRODUCT_LANDING_FILTER.search(url) or
                PRODUCT_SITE_DOMAINS.search(url) or
                HARD_BLOCK_DOMAINS.search(url) or
                "claude.com" in url or
                "anthropic.com" in url or
                _is_product_homepage(url)):
                continue

            hn_score = story.get("score", 0)

            if not (AI_KEYWORDS.search(title) or AI_KEYWORDS.search(url)):
                continue
            if hn_score < 30:
                continue
            if PAPER_FILTER.search(title) or PAPER_FILTER.search(url):
                continue
            if FALSE_POSITIVE_FILTER.search(title):
                continue
            if is_github_url(url):
                continue
            if GITHUB_TITLE_FILTER.search(title):
                continue

            items.append({
                "title": title,
                "url": url,
                "summary": f"HN Score: {hn_score} | Comments: {story.get('descendants', 0)}",
                "source": source,
                "source_type": "intl",
                "date": datetime.fromtimestamp(
                    story.get("time", 0), tz=timezone.utc
                ).isoformat(),
                "score": hn_score,
            })
    except Exception as e:
        print(f"  [WARN] {source} fetch failed: {e}")

    tracker.record(source, items)
    return items

def fetch_wired_ai():
    source = "Wired"
    items = parse_rss_feed(
        "https://www.wired.com/feed/tag/ai/latest/rss",
        source_name=source,
        max_entries=15,
        ai_filter=False,
    )
    if not items:
        items = scrape_links_from_page(
            "https://www.wired.com/tag/ai/",
            source_name=source,
            title_min_len=10,
            max_items=10,
            ai_filter=False,
        )
    items = [it for it in items if not is_wired_paywalled(it)]
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# B. 国际权威媒体抓取
# ══════════════════════════════════════════════════════════════════════════════

def fetch_techcrunch():
    source = "TechCrunch"
    items = parse_rss_feed(
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        source_name=source,
        max_entries=20,
        ai_filter=False,
    )
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_theverge():
    source = "The Verge"
    items = parse_rss_feed(
        "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        source_name=source,
        max_entries=15,
        ai_filter=False,
    )
    if not items:
        items = scrape_links_from_page(
            "https://www.theverge.com/ai-artificial-intelligence",
            source_name=source,
            max_items=10,
            ai_filter=False,
        )
    items = [it for it in items if not is_theverge_paywalled(it)]
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_venturebeat():
    source = "VentureBeat"
    items = parse_rss_feed(
        "https://venturebeat.com/category/ai/feed/",
        source_name=source,
        max_entries=15,
        ai_filter=False,
    )
    if not items:
        items = scrape_links_from_page(
            "https://venturebeat.com/category/ai/",
            source_name=source,
            max_items=10,
            ai_filter=False,
        )
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_arstechnica():
    source = "Ars Technica"
    items = parse_rss_feed(
        "https://feeds.arstechnica.com/arstechnica/technology-lab",
        source_name=source,
        max_entries=20,
        ai_filter=True,
    )
    if not items:
        items = scrape_links_from_page(
            "https://arstechnica.com/ai/",
            source_name=source,
            max_items=10,
            ai_filter=False,
        )
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_mit_tech_review():
    source = "MIT Tech Review"
    items = parse_rss_feed(
        "https://www.technologyreview.com/feed/",
        source_name=source,
        max_entries=20,
        ai_filter=True,
    )
    if not items:
        items = scrape_links_from_page(
            "https://www.technologyreview.com/topic/artificial-intelligence/",
            source_name=source,
            max_items=10,
            ai_filter=False,
        )
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_ieee_spectrum():
    source = "IEEE Spectrum"
    items = parse_rss_feed(
        "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",
        source_name=source,
        max_entries=15,
        ai_filter=False,
    )
    if not items:
        items = scrape_links_from_page(
            "https://spectrum.ieee.org/topic/artificial-intelligence/",
            source_name=source,
            title_min_len=10,
            max_items=10,
            ai_filter=False,
        )
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

# ══════════════════════════════════════════════════════════════════════════════
# C. 国内权威媒体抓取
# ══════════════════════════════════════════════════════════════════════════════

def fetch_jiqizhixin():
    source = "机器之心"
    items = []
    try:
        items = parse_rss_feed(
            "https://www.jiqizhixin.com/rss",
            source_name=source,
            max_entries=20,
            ai_filter=False,
        )
    except Exception:
        pass

    if not items:
        items = _scrape_jiqizhixin()

    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def _scrape_jiqizhixin():
    source = "机器之心"
    items = []
    try:
        resp = safe_request("https://www.jiqizhixin.com/")
        if not resp:
            return items
        html = resp.text
        pattern = re.compile(
            r'<a[^>]+href="((?:https?://www\.jiqizhixin\.com)?/(?:articles|dailies)/[^"]+)"[^>]*>'
            r'\s*([^<]{8,}?)\s*</a>',
        )
        matches = pattern.findall(html)
        seen = set()
        for url, title in matches:
            title = re.sub(r"\s+", " ", title.strip())
            if not url.startswith("http"):
                url = "https://www.jiqizhixin.com" + url
            if url not in seen and len(title) >= 8:
                if is_github_url(url) or GITHUB_TITLE_FILTER.search(title):
                    continue
                if FOOTER_TEXT_FILTER.search(title):
                    continue
                seen.add(url)
                extracted_date = extract_date_from_url(url)
                items.append({
                    "title": title,
                    "url": url,
                    "summary": "via 机器之心",
                    "source": source,
                    "source_type": "domestic",
                    "date": extracted_date or _now_iso(),
                    "date_inferred": not bool(extracted_date),
                    "fetched_at": _now_iso(),
                    "score": 0,
                })
                if len(items) >= 15:
                    break
    except Exception as e:
        print(f"  [WARN] 机器之心 scrape failed: {e}")
    return items

def fetch_qbitai():
    source = "量子位"
    items = []
    try:
        for rss_url in [
            "https://www.qbitai.com/feed",
            "https://www.qbitai.com/rss",
            "https://www.qbitai.com/feed/",
        ]:
            items = parse_rss_feed(rss_url, source_name=source, max_entries=15, ai_filter=False)
            if items:
                break
    except Exception:
        pass

    if not items:
        items = scrape_links_from_page(
            "https://www.qbitai.com/",
            source_name=source,
            title_min_len=8,
            max_items=12,
            ai_filter=False,
        )

    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_36kr():
    source = "36氪"
    items = []
    for url in [
        "https://36kr.com/information/AI/",
        "https://www.36kr.com/information/AI/",
        "https://36kr.com/feed",
    ]:
        try:
            if "feed" in url:
                items = parse_rss_feed(url, source_name=source, max_entries=15, ai_filter=True)
            else:
                items = scrape_links_from_page(
                    url,
                    source_name=source,
                    title_min_len=8,
                    max_items=12,
                    ai_filter=False,
                )
            if items:
                break
        except Exception:
            continue

    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_ithome():
    source = "IT之家"
    items = []
    try:
        items = parse_rss_feed(
            "https://www.ithome.com/rss/",
            source_name=source,
            max_entries=30,
            ai_filter=True,
        )
    except Exception:
        pass

    if not items:
        items = scrape_links_from_page(
            "https://www.ithome.com/tag/AI/",
            source_name=source,
            title_min_len=8,
            max_items=12,
            ai_filter=False,
        )

    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_xinzhiyuan():
    source = "新智元"
    items = []
    for url in [
        "https://www.xinzhiyuan.com/",
        "https://www.xinzhiyuan.com/category/ai/",
        "https://www.aihub.cn/",
    ]:
        try:
            items = scrape_links_from_page(
                url,
                source_name=source,
                title_min_len=8,
                max_items=10,
                ai_filter=False,
            )
            if items:
                break
        except Exception:
            continue
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_infoq():
    source = "InfoQ"
    items = scrape_links_from_page(
        "https://www.infoq.cn/topic/AI",
        source_name=source,
        title_min_len=8,
        max_items=10,
        ai_filter=False,
    )
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_sina_tech():
    source = "新浪科技"
    items = []
    warmup_sina_homepage()
    try:
        items = parse_rss_feed(
            "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2515&k=&num=30&page=1",
            source_name=source,
            max_entries=20,
            ai_filter=True,
        )
    except Exception:
        pass

    if not items:
        for url in [
            "https://tech.sina.com.cn/",
            "https://tech.sina.com.cn/roll/",
        ]:
            try:
                items = scrape_links_from_page(
                    url,
                    source_name=source,
                    title_min_len=8,
                    max_items=12,
                    ai_filter=True,
                )
                if items:
                    break
            except Exception:
                continue

    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_toutiao():
    source = "今日头条"
    items = scrape_links_from_page(
        "https://www.toutiao.com/ch/news_tech/",
        source_name=source,
        title_min_len=8,
        max_items=12,
        ai_filter=True,
    )
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items

def fetch_thepaper():
    source = "澎湃新闻"
    items = scrape_links_from_page(
        "https://www.thepaper.cn/channel_25951",
        source_name=source,
        title_min_len=8,
        max_items=10,
        ai_filter=True,
    )
    items = [it for it in items if github_with_usage_instruction(it)]
    tracker.record(source, items)
    return items


def fetch_youtube():
    source = "YouTube"
    items = []
    stats = {"raw": 0, "non_video": 0, "date": 0, "keyword": 0}
    print("      [B.5] YouTube 抓取中...")

    # 1) 先用 yt-dlp 按最新时间搜索，尽量避免抓到 오래旧热视频
    items.extend(scrape_youtube_by_ytdlp_search(
        (SOCIAL_PRACTICAL_QUERIES + AUDIO_MUSIC_GAME_QUERIES)[:VIDEO_QUERY_LIMIT],
        max_items=VIDEO_CANDIDATE_MAX,
    ))

    # 2) 网页搜索兜底
    if len(items) < max(4, VIDEO_CANDIDATE_MAX // 2):
        items.extend(scrape_youtube_search_results(
            (SOCIAL_PRACTICAL_QUERIES + AUDIO_MUSIC_GAME_QUERIES)[:VIDEO_QUERY_LIMIT],
            max_items=VIDEO_CANDIDATE_MAX,
        ))

    # 3) 用户显式配置的官方频道 feed
    if YOUTUBE_FEED_URLS:
        items.extend(
            parse_rss_feed_candidates(
                urls=YOUTUBE_FEED_URLS,
                source_name=source,
                max_entries=10,
                ai_filter=False,
            )
        )

    # 4) Google News 站内检索（兜底）
    if not items:
        items.extend(
            _fetch_google_news_site(
                "youtube.com",
                source_name=source,
                extra_queries=(SOCIAL_PRACTICAL_QUERIES + MODEL_INNOVATION_QUERIES)[:VIDEO_QUERY_LIMIT],
                max_entries=5,
            )
        )

    # 5) RSSHub 关键词检索（可选）
    if len(items) < 5:
        items.extend(
            _fetch_rsshub_keyword(
                route_template="youtube/keyword/{keyword}",
                source_name=source,
                keywords=SOCIAL_PRACTICAL_QUERIES[:VIDEO_QUERY_LIMIT],
                max_entries=5,
            )
        )

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        real_url = resolve_google_news_redirect(it.get("url", ""))
        if "news.google.com/rss/articles/" in it.get("url", "") and not real_url:
            continue
        if real_url:
            it["url"] = real_url
        if not re.search(r"(youtube\.com/watch\?v=|youtu\.be/)", it.get("url", ""), re.IGNORECASE):
            stats["non_video"] += 1
            continue
        k = it.get("url", "").rstrip("/")
        if not k or k in seen:
            continue
        # 直接视频页提取真实发布时间；搜索页相对时间只做预筛，不做最终发布日期
        page_date, page_date_conf = _extract_youtube_published_date(it.get("url", ""))
        effective_date = ""
        effective_conf = page_date_conf
        if page_date:
            effective_date = page_date
        elif it.get("date"):
            effective_date = it.get("date", "")
            effective_conf = "medium" if not it.get("date_inferred") else "low"
        elif it.get("_search_rel_date"):
            effective_date = it.get("_search_rel_date", "")
            effective_conf = "low"

        # 对于 YouTube，优先真实日期；若页面抓不到但搜索结果本身明确落在时间窗内，则允许低置信度兜底
        if not effective_date:
            stats["date"] += 1
            continue
        it["date"] = effective_date
        it["date_inferred"] = effective_conf != "high"
        it["_date_confidence"] = effective_conf
        if not is_within_days(effective_date, YOUTUBE_MAX_AGE_DAYS):
            continue
        if not practical_video_gate(it):
            stats["keyword"] += 1
            continue
        seen.add(k)
        dedup.append(_mark_social_item(it, platform="YouTube", is_video=True))

    print(f"      [B.5] YouTube 完成: {len(dedup)} 条 (raw={stats['raw']}, 非视频={stats['non_video']}, 日期失败={stats['date']}, 关键词过滤={stats['keyword']})")
    tracker.record(source, dedup)
    return dedup


def fetch_bilibili():
    source = "B站"
    items = []
    stats = {"raw": 0, "date": 0, "keyword": 0}
    print("      [B.5] B站抓取中...")

    # 1) 站内搜索优先，直接拿真实视频链接
    items.extend(scrape_bilibili_search_results(
        (SOCIAL_PRACTICAL_QUERIES + AUDIO_MUSIC_GAME_QUERIES)[:VIDEO_QUERY_LIMIT],
        max_items=VIDEO_CANDIDATE_MAX,
    ))

    # 2) Google News 兜底
    if not items:
        items.extend(
            _fetch_google_news_site(
                "bilibili.com",
                source_name=source,
                extra_queries=(SOCIAL_PRACTICAL_QUERIES + MODEL_INNOVATION_QUERIES)[:VIDEO_QUERY_LIMIT],
                max_entries=5,
            )
        )

    if len(items) < 5:
        items.extend(
            _fetch_rsshub_keyword(
                route_template="bilibili/search/{keyword}",
                source_name=source,
                keywords=SOCIAL_PRACTICAL_QUERIES[:VIDEO_QUERY_LIMIT],
                max_entries=5,
            )
        )

    if len(items) < 5:
        items.extend(
            _fetch_google_news_site(
                "b23.tv",
                source_name=source,
                extra_queries=(SOCIAL_PRACTICAL_QUERIES + AUDIO_MUSIC_GAME_QUERIES)[:VIDEO_QUERY_LIMIT],
                max_entries=4,
            )
        )

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        real_url = resolve_google_news_redirect(it.get("url", ""))
        if "news.google.com/rss/articles/" in it.get("url", "") and not real_url:
            continue
        if real_url:
            it["url"] = real_url
        k = it.get("url", "").rstrip("/")
        if not k or k in seen:
            continue
        page_date = _extract_bilibili_published_date(it.get("url", ""))
        effective_date = page_date or it.get("date", "")
        if not effective_date:
            stats["date"] += 1
            continue
        it["date"] = effective_date
        it["date_inferred"] = not bool(page_date)
        if not is_within_days(effective_date, VIDEO_MAX_AGE_DAYS):
            continue
        if not practical_video_gate(it):
            stats["keyword"] += 1
            continue
        seen.add(k)
        dedup.append(_mark_social_item(it, platform="Bilibili", is_video=True))

    print(f"      [B.5] B站完成: {len(dedup)} 条 (raw={stats['raw']}, 日期失败={stats['date']}, 关键词过滤={stats['keyword']})")
    tracker.record(source, dedup)
    return dedup


def fetch_video_tutorial_sources():
    source = "Video Tutorials"
    items = []
    stats = {"raw": 0, "domain": 0, "date": 0, "keyword": 0}
    print("      [B.5] 扩展视频源抓取中...")
    items.extend(_fetch_direct_tutorial_candidates(source_name=source, max_entries=16))
    video_domains = [
        "vimeo.com",
        "dailymotion.com",
        "ted.com",
        "coursera.org",
        "udemy.com",
        "egghead.io",
        "frontendmasters.com",
        "youtube.com",
        "bilibili.com",
    ]
    for dom in video_domains[:VIDEO_DOMAIN_LIMIT]:
        try:
            items.extend(
                _fetch_google_news_site(
                    dom,
                    source_name=source,
                    extra_queries=(SOCIAL_PRACTICAL_QUERIES + AUDIO_MUSIC_GAME_QUERIES)[:VIDEO_QUERY_LIMIT],
                    max_entries=3,
                )
            )
        except Exception:
            continue

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        url = it.get("url", "").rstrip("/")
        if not url or url in seen:
            continue
        original_url = it.get("url", "")
        real_url = resolve_google_news_redirect(original_url)
        if "news.google.com/rss/articles/" in original_url and not real_url:
            stats["domain"] += 1
            continue
        if real_url:
            it["url"] = real_url
            url = real_url.rstrip("/")
        domain = (urlparse(url).netloc or "").lower()
        if not (SOCIAL_VIDEO_DOMAINS.search(url) or any(d in domain for d in video_domains)):
            stats["domain"] += 1
            continue
        effective_date = it.get("date", "")
        if "youtube.com" in domain or "youtu.be" in domain:
            page_date, conf = _extract_youtube_published_date(url)
            if page_date:
                effective_date = page_date
                it["date"] = page_date
                it["date_inferred"] = conf != "high"
            elif it.get("_search_rel_date"):
                effective_date = it.get("_search_rel_date", "")
                it["date"] = effective_date
                it["date_inferred"] = True
            if not effective_date or not is_within_days(effective_date, YOUTUBE_MAX_AGE_DAYS):
                stats["date"] += 1
                continue
        elif "bilibili.com" in domain or "b23.tv" in domain:
            page_date = _extract_bilibili_published_date(url)
            if page_date:
                effective_date = page_date
                it["date"] = page_date
                it["date_inferred"] = False
            if not effective_date or not is_within_days(effective_date, VIDEO_MAX_AGE_DAYS):
                stats["date"] += 1
                continue
        elif not is_within_days(effective_date, VIDEO_MAX_AGE_DAYS):
            stats["date"] += 1
            continue
        if not practical_video_gate(it):
            stats["keyword"] += 1
            continue
        seen.add(url)
        dedup.append(_mark_social_item(it, platform="Video", is_video=True))

    print(f"      [B.5] 扩展视频源完成: {len(dedup)} 条 (raw={stats['raw']}, 域名过滤={stats['domain']}, 日期过滤={stats['date']}, 关键词过滤={stats['keyword']})")
    tracker.record(source, dedup)
    return dedup


def fetch_weibo():
    source = "微博"
    items = _fetch_google_news_site(
        "weibo.com",
        source_name=source,
        extra_queries=SOCIAL_PRACTICAL_QUERIES,
        max_entries=8,
    )

    if len(items) < 5:
        items.extend(
            _fetch_rsshub_keyword(
                route_template="weibo/search/{keyword}",
                source_name=source,
                keywords=SOCIAL_PRACTICAL_QUERIES,
                max_entries=8,
            )
        )

    dedup = []
    seen = set()
    for it in items:
        original_url = it.get("url", "")
        it["original_url"] = original_url
        # 公司网络兼容：微博统一走可访问阅读代理
        it["url"] = build_alt_social_url(original_url)
        k = it.get("url", "").rstrip("/")
        if not k or k in seen:
            continue
        seen.add(k)
        dedup.append(_mark_social_item(it, platform="Weibo", is_video=False))

    tracker.record(source, dedup)
    return dedup


def fetch_twitter():
    source = "Twitter"
    items = _fetch_nitter_search(
        source_name=source,
        keywords=SOCIAL_PRACTICAL_QUERIES + MODEL_INNOVATION_QUERIES,
        max_entries=8,
    )

    if not items:
        items = _fetch_google_news_site(
            "twitter.com",
            source_name=source,
            extra_queries=SOCIAL_PRACTICAL_QUERIES + MODEL_INNOVATION_QUERIES,
            max_entries=8,
        )

    if len(items) < 5:
        items.extend(
            _fetch_rsshub_keyword(
                route_template="twitter/search/{keyword}",
                source_name=source,
                keywords=SOCIAL_PRACTICAL_QUERIES,
                max_entries=8,
            )
        )

    dedup = []
    seen = set()
    for it in items:
        original_url = it.get("url", "")
        it["original_url"] = original_url
        # 公司网络兼容：X/Twitter 统一走可访问阅读代理
        it["url"] = build_alt_social_url(original_url)
        k = it.get("url", "").rstrip("/")
        if not k or k in seen:
            continue
        seen.add(k)
        dedup.append(_mark_social_item(it, platform="Twitter", is_video=False))

    tracker.record(source, dedup)
    return dedup


def fetch_x():
    source = "X"
    items = _fetch_nitter_search(
        source_name=source,
        keywords=SOCIAL_PRACTICAL_QUERIES + MODEL_INNOVATION_QUERIES,
        max_entries=8,
    )

    if not items:
        items = _fetch_google_news_site(
            "x.com",
            source_name=source,
            extra_queries=SOCIAL_PRACTICAL_QUERIES + MODEL_INNOVATION_QUERIES,
            max_entries=8,
        )

    # X 与 Twitter 在 RSSHub 中通常复用 twitter 路由
    if len(items) < 5:
        items.extend(
            _fetch_rsshub_keyword(
                route_template="twitter/search/{keyword}",
                source_name=source,
                keywords=SOCIAL_PRACTICAL_QUERIES,
                max_entries=8,
            )
        )

    dedup = []
    seen = set()
    for it in items:
        original_url = it.get("url", "")
        it["original_url"] = original_url
        # 公司网络兼容：X/Twitter 统一走可访问阅读代理
        it["url"] = build_alt_social_url(original_url)
        k = it.get("url", "").rstrip("/")
        if not k or k in seen:
            continue
        seen.add(k)
        dedup.append(_mark_social_item(it, platform="X", is_video=False))

    tracker.record(source, dedup)
    return dedup


def fetch_audio_music_game_tutorials():
    source = "Audio/Music/Game AI"
    items = []
    stats = {"raw": 0, "date": 0, "keyword": 0}
    print("      [B.5] 音频/音乐/游戏 AI 教程源抓取中...")

    # 教程社区与技术媒体（偏实用）
    tutorial_domains = [
        "towardsdatascience.com",
        "github.blog",
        "blog.google",
        "openai.com",
        "replicate.com",
        "elevenlabs.io",
        "suno.com",
        "audiocraft.metademolab.com",
        "unity.com",
        "unrealengine.com",
        "learn.microsoft.com",
        "developers.googleblog.com",
        "developer.nvidia.com",
        "developer.chrome.com",
        "aws.amazon.com",
        "cloud.google.com",
    ]

    items.extend(_fetch_direct_tutorial_candidates(source_name=source, max_entries=18))
    for dom in tutorial_domains[:AUDIO_MUSIC_DOMAIN_LIMIT]:
        items.extend(
            _fetch_google_news_site(
                dom,
                source_name=source,
                extra_queries=AUDIO_MUSIC_GAME_QUERIES[:GOOGLE_NEWS_QUERY_LIMIT],
                max_entries=3,
            )
        )

    dedup = []
    seen = set()
    stats["raw"] = len(items)
    for it in items:
        url = it.get("url", "").rstrip("/")
        if not url or url in seen:
            continue
        if not is_within_days(it.get("date"), 30):
            stats["date"] += 1
            continue
        if not practical_keyword_gate(it):
            stats["keyword"] += 1
            continue
        seen.add(url)
        dedup.append(it)

    print(f"      [B.5] 音频/音乐/游戏 AI 教程源完成: {len(dedup)} 条 (raw={stats['raw']}, 日期过滤={stats['date']}, 关键词过滤={stats['keyword']})")
    tracker.record(source, dedup)
    return dedup


def supplementary_search_intl():
    print("  [INFO] 国际源不足，尝试补充搜索...")
    return []

def supplementary_search_domestic():
    print("  [INFO] 国内源不足，尝试补充搜索...")
    return []

# ══════════════════════════════════════════════════════════════════════════════
# 标题相似度去重
# ══════════════════════════════════════════════════════════════════════════════

def title_similarity(t1, t2):
    def normalize(t):
        t = t.lower().strip()
        t = re.sub(r"[^\w\s\u4e00-\u9fff]", "", t)
        t = re.sub(r"\s+", " ", t)
        return t

    n1, n2 = normalize(t1), normalize(t2)
    if not n1 or not n2:
        return 0.0
    return SequenceMatcher(None, n1, n2).ratio()

def is_duplicate_title(new_title, existing_titles, threshold=0.65):
    for existing in existing_titles:
        if title_similarity(new_title, existing) > threshold:
            return True
    return False


def extract_content_fingerprint(item):
    """
    跨来源同事件去重指纹：
    1) URL slug 关键词
    2) 标题归一化关键词
    """
    title = (item.get("title") or "").lower()
    url = (item.get("url") or "").lower()

    # 取 URL 最后两段路径中的字母数字 token
    path = urlparse(url).path or ""
    segments = [s for s in path.split("/") if s][-2:]
    tokens = []
    for seg in segments:
        tokens.extend(re.findall(r"[a-z0-9\u4e00-\u9fff]{3,}", seg))

    # 加入标题 token
    tokens.extend(re.findall(r"[a-z0-9\u4e00-\u9fff]{3,}", title))

    # 去掉常见噪声词
    stop = {
        "https", "http", "www", "com", "cn", "html", "shtml", "tech", "news",
        "article", "post", "video", "ai", "the", "and", "for", "with",
    }
    tokens = [t for t in tokens if t not in stop]
    if not tokens:
        return ""
    # 保留前10个关键词作为指纹
    return "|".join(sorted(set(tokens))[:10])


def practical_relevance_score(item):
    title = item.get("title", "")
    summary = item.get("summary", "")
    title_zh = item.get("title_zh", "")
    summary_zh = item.get("summary_zh", "")
    url = item.get("url", "")
    search_query = item.get("search_query", "")
    text = f"{title} {summary} {title_zh} {summary_zh}"
    support_text = f"{text} {search_query}"

    score = 0
    if AI_CORE_PATTERN.search(text):
        score += 3
    if PRACTICAL_SIGNAL.search(text):
        score += 3
    if REUSABLE_SIGNAL.search(text):
        score += 2
    if INNOVATION_SIGNAL.search(text):
        score += 2
    if PRACTICE_REQUIRED_PATTERN.search(support_text):
        score += 4
    if re.search(r"\b(skill|skills|agent|agentic|workflow|tutorial|how.to)\b|教程|实战|工作流|智能体", text, re.IGNORECASE):
        score += 2
    if re.search(r"音频|播客|podcast|voice|配音|ASR|TTS|DAW|VST|混音|母带|转写", text, re.IGNORECASE):
        score += 2
    if ORDINARY_HINT_PATTERN.search(support_text):
        score += 2
    if REQUIRED_PATTERN.search(support_text):
        score += 3
    if TECH_BOOST.search(text):
        score += 1
    if HOT_ENTITY.search(text):
        score += 1
    if item.get("is_priority_wechat"):
        score += 3
    if get_wechat_account_hint(item):
        score += 2

    if LOW_VALUE_SIGNAL.search(text):
        score -= 3
    if FUNDING_POLICY_FILTER.search(text):
        score -= 8
    if ENTERPRISE_BIZ_FILTER.search(text):
        score -= 6
    if EXCLUDE_PATTERN.search(support_text):
        score -= 12
    if is_non_actionable_page(item):
        score -= 12
    if is_non_practical_news(item):
        score -= 14
    if not AI_CORE_PATTERN.search(text):
        score -= 8

    # 社媒/视频来源如果没有实践信号，额外降权，减少“标题党”进入
    if (item.get("is_social") or SOCIAL_VIDEO_DOMAINS.search(url)) and not PRACTICE_REQUIRED_PATTERN.search(support_text):
        score -= 2

    return score


def audio_relevance_score(item):
    text = build_item_filter_text(item, include_query=True)
    score = 0
    strong_terms = [
        r"音频", r"语音", r"voice", r"speech", r"配音", r"dubbing", r"播客", r"podcast",
        r"asr", r"tts", r"转写", r"字幕", r"降噪", r"denoise", r"混音", r"母带",
        r"music", r"音乐", r"sound design", r"soundtrack", r"game audio", r"游戏音频",
    ]
    practical_terms = [
        r"workflow", r"工作流", r"教程", r"实战", r"案例", r"指南", r"部署", r"集成",
        r"plugin", r"vst", r"daw", r"automation", r"agent", r"智能体", r"runway",
        r"elevenlabs", r"suno", r"descript", r"udio", r"audiocraft",
    ]
    for pat in strong_terms:
        if re.search(pat, text, re.IGNORECASE):
            score += 2
    for pat in practical_terms:
        if re.search(pat, text, re.IGNORECASE):
            score += 1
    if item.get("source") in {"Audio/Music/Game AI", "Audio Creator AI"}:
        score += 3
    if item.get("is_video"):
        score += 1
    return score


def pool_bucket(item):
    if item.get("_pool"):
        return item["_pool"]
    practical_score = item.get("practical_score", practical_relevance_score(item))
    audio_score = item.get("audio_score", audio_relevance_score(item))
    date_ok = bool(item.get("date"))
    reliable_source = item.get("source") in SOURCE_REGISTRY
    practical_hit = is_practical_candidate(item)
    frontier_hit = frontier_innovation_gate(item)
    practice_required_hit = bool(PRACTICE_REQUIRED_PATTERN.search(build_item_filter_text(item, include_query=True)))
    ordinary_hit = bool(ORDINARY_HINT_PATTERN.search(build_item_filter_text(item, include_query=True)))

    if date_ok and reliable_source and (practical_hit or frontier_hit) and (practice_required_hit or practical_score >= max(PRACTICAL_MIN_SCORE, 2)):
        return "A"
    if date_ok and reliable_source and not is_non_actionable_page(item) and not is_non_practical_news(item):
        if audio_score >= 2 or ordinary_hit or practical_score >= max(1, PRACTICAL_MIN_SCORE - 1):
            return "B"
    return "DROP"


def is_practical_candidate(item):
    """
    实用硬门槛：
    - 必须明确是 AI 相关
    - 必须带教程/案例/API/开源/工具/工作流等可落地线索
    - 白皮书/营销页/事故类新闻直接过滤
    """
    text = build_item_filter_text(item, include_query=False)
    support_text = build_item_filter_text(item, include_query=True)
    practical_hit = bool(PRACTICAL_SIGNAL.search(text))
    reusable_hit = bool(REUSABLE_SIGNAL.search(text))
    innovation_hit = bool(INNOVATION_SIGNAL.search(text))
    model_hit = bool(MODEL_SIGNAL.search(text))
    app_hit = bool(APPLICATION_SIGNAL.search(text))
    ai_core_hit = bool(AI_CORE_PATTERN.search(text))
    practice_required_hit = bool(PRACTICE_REQUIRED_PATTERN.search(support_text))
    excluded_hit = bool(EXCLUDE_PATTERN.search(support_text))

    if excluded_hit:
        return False
    if is_non_actionable_page(item):
        return False
    if is_non_practical_news(item):
        return False
    if not ai_core_hit:
        return False
    if frontier_innovation_gate(item):
        return True
    if practice_required_hit:
        return True
    if practical_hit or reusable_hit:
        return True
    if innovation_hit and (model_hit or app_hit):
        return True
    return False


def allowed_item_age_hours(item):
    source = item.get("source", "")
    if source in {"AI Frontier", "Practical Guides", "Agent/Coding AI", "Audio Creator AI", "Audio/Music/Game AI", "Video Tutorials"}:
        return 24 * 7
    if item.get("is_video"):
        platform = str(item.get("platform", "")).lower()
        if platform == "youtube":
            return 24 * YOUTUBE_MAX_AGE_DAYS
        return 24 * VIDEO_MAX_AGE_DAYS
    return OLD_NEWS_HOURS

# ══════════════════════════════════════════════════════════════════════════════
# 质量筛选
# ══════════════════════════════════════════════════════════════════════════════

def quality_filter(items):
    filtered = []
    pool_stats = {"A": 0, "B": 0}
    today = datetime.now(BEIJING_TZ)
    funding_policy_count = 0
    non_tech_filtered_count = 0
    enterprise_biz_filtered_count = 0
    practical_filtered_count = 0
    hard_practical_filtered_count = 0
    # 日期无法解析/超过时效统计
    date_missing_filtered_count = 0
    old_date_filtered_count = 0

    for item in items:
        title = item.get("title", "")
        url = item.get("url", "")
        summary = item.get("summary", "")
        text = f"{title} {summary}"

        if HARD_BLOCK_DOMAINS.search(url):
            continue
        if REMOVED_SOURCE_DOMAINS.search(url):
            continue
        if is_hn_discussion_url(url):
            continue
        if item.get("source") == "Hacker News" and re.search(r"^\s*(ask|show|tell|launch)\s+hn\b", title, re.IGNORECASE):
            continue

        if not github_with_usage_instruction({"title": title, "summary": summary, "url": url}):
            continue
        if is_non_actionable_page(item):
            continue
        if is_non_practical_news(item):
            continue
        if SOFT_AD_FILTER.search(text):
            continue
        # ★ v3.1 拦截底部备案文本
        if FOOTER_TEXT_FILTER.search(title):
            continue
            
        if len(title) < 8:
            continue
        if PAPER_FILTER.search(title) or PAPER_FILTER.search(url):
            continue
        if FALSE_POSITIVE_FILTER.search(title):
            continue

        # ══════════════════════════════════════════════════════════════
        # ★ v3.2 修改：72小时时效性过滤（修复日期缺失静默放行问题）
        # ══════════════════════════════════════════════════════════════
        date_val = item.get("date")
        if not date_val:
            # 兜底：使用抓取时间，避免大规模误杀
            date_val = item.get("fetched_at", _now_iso())
            item["date"] = date_val
            item["date_inferred"] = True

        try:
            date_str = date_val
            article_date = None
            if isinstance(date_str, str):
                if "T" in date_str:
                    article_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(BEIJING_TZ)
                elif re.match(r"\d{4}-\d{2}-\d{2}", date_str):
                    article_date = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
                else:
                    try:
                        article_date = parsedate_to_datetime(date_str).astimezone(BEIJING_TZ)
                    except Exception:
                        pass

            if article_date:
                item_video_window_days = None
                if item.get("is_video"):
                    if str(item.get("platform", "")).lower() == "youtube":
                        item_video_window_days = YOUTUBE_MAX_AGE_DAYS
                    else:
                        item_video_window_days = VIDEO_MAX_AGE_DAYS
                if item_video_window_days is not None:
                    if not (timedelta(0) <= (today - article_date) <= timedelta(days=item_video_window_days)):
                        old_date_filtered_count += 1
                        continue
                else:
                    allowed_hours = allowed_item_age_hours(item)
                    if (today - article_date).total_seconds() > allowed_hours * 3600:
                        old_date_filtered_count += 1
                        continue
            else:
                # 日期未知的条目不再默认放行为今天，直接丢弃，避免 1 月旧文被误判成当天
                date_missing_filtered_count += 1
                continue
        except Exception:
            date_missing_filtered_count += 1
            continue

        if FUNDING_POLICY_FILTER.search(text):
            # 投资/商业类直接过滤
            funding_policy_count += 1
            continue
        if INVESTMENT_URL_FILTER.search(url):
            funding_policy_count += 1
            continue

        if NON_TECH_FILTER.search(text) and not AI_EXEMPT.search(text):
            non_tech_filtered_count += 1
            continue

        if ENTERPRISE_BIZ_FILTER.search(text):
            enterprise_biz_filtered_count += 1
            continue

        # 🚀 严格拦截：只要匹配到产品特征，或者被智能识别为产品首页，直接丢弃！
        if PRODUCT_LANDING_FILTER.search(url) or PRODUCT_SITE_DOMAINS.search(url) or _is_product_homepage(url):
            continue  # 直接跳过，不加入 filtered 列表

        pscore = practical_relevance_score(item)
        item["practical_score"] = pscore
        item["audio_score"] = audio_relevance_score(item)
        if PRACTICAL_STRICT_ONLY:
            item_pool = pool_bucket(item)
            item["_pool"] = item_pool
            if item_pool == "DROP":
                if not is_practical_candidate(item):
                    hard_practical_filtered_count += 1
                else:
                    practical_filtered_count += 1
                continue
            dynamic_threshold = PRACTICAL_MIN_SCORE
            if frontier_innovation_gate(item):
                dynamic_threshold = max(1, PRACTICAL_MIN_SCORE - 1)
            if item.get("source") in {"Practical Guides", "Agent/Coding AI", "Audio Creator AI", "Audio/Music/Game AI", "AI Frontier", "Video Tutorials"}:
                dynamic_threshold = max(1, dynamic_threshold - 1)
            if item_pool == "B":
                dynamic_threshold = max(1, dynamic_threshold - 1)
            if pscore < dynamic_threshold and item_pool != "B":
                practical_filtered_count += 1
                continue
            pool_stats[item_pool] = pool_stats.get(item_pool, 0) + 1

        filtered.append(item)

    if non_tech_filtered_count > 0:
        print(f"      [v2.6] 非技术向内容过滤: {non_tech_filtered_count} 条")
    if enterprise_biz_filtered_count > 0:
        print(f"      [v2.7] 企业商务类新闻过滤: {enterprise_biz_filtered_count} 条")
    if practical_filtered_count > 0:
        print(f"      [v3.3] 实用/复用/创新不足过滤: {practical_filtered_count} 条")
    if hard_practical_filtered_count > 0:
        print(f"      [v3.3] 未命中实用硬门槛过滤: {hard_practical_filtered_count} 条")
    if date_missing_filtered_count > 0:
        print(f"      [v3.3] 日期缺失/解析失败（已兜底）: {date_missing_filtered_count} 条")
    if old_date_filtered_count > 0:
        print(f"      [v3.2] 超过72小时过滤: {old_date_filtered_count} 条")
    if pool_stats["A"] or pool_stats["B"]:
        print(f"      [v3.4] 放行池统计: A池 {pool_stats['A']} 条 | B池 {pool_stats['B']} 条")
    # 视频站时效由来源抓取阶段执行硬过滤：B站默认 5 天，YouTube 默认 7 天

    return filtered

def calculate_heat_score(item):
    base_score = item.get("score", 0)
    source = item.get("source", "")
    title = item.get("title", "")
    summary = item.get("summary", "")
    practical_score = item.get("practical_score", 0)
    text = f"{title} {summary}"

    if source == "Hacker News" and base_score > 0:
        heat = base_score
    else:
        heat = SOURCE_WEIGHT.get(source, 50)

    if TECH_BOOST.search(text):
        heat += 20
    if HOT_ENTITY.search(text):
        heat += 15
    if FUNDING_POLICY_FILTER.search(text):
        heat -= 120
    if PRACTICE_BOOST.search(text):
        heat += 25
    if re.search(r"\b(skill|skills|agent|agentic|workflow|tutorial|how.to)\b|教程|实战|工作流|智能体", text, re.IGNORECASE):
        heat += 18
    if re.search(r"音频|播客|podcast|voice|配音|ASR|TTS|DAW|VST|混音|母带|转写", text, re.IGNORECASE):
        heat += 22
    if item.get("is_priority_wechat"):
        heat += 20
    if get_wechat_account_hint(item):
        heat += 10
    if practical_score > 0:
        heat += practical_score * 12
    if item.get("is_social") and practical_score < PRACTICAL_MIN_SCORE:
        heat -= 40

    return heat

def deduplicate_and_rank(all_items):
    items = quality_filter(all_items)
    feedback_profile = build_feedback_profile()

    for item in items:
        item.setdefault("_pool", pool_bucket(item))
        item["heat_score"] = calculate_heat_score(item)
        item["audio_score"] = item.get("audio_score", audio_relevance_score(item))
        item["feedback_bias"] = feedback_bias_score(item, feedback_profile)
        item["heat_score"] += item["audio_score"] * 8 + item["feedback_bias"] * 6
        if item.get("_pool") == "A":
            item["heat_score"] += 12
        elif item.get("_pool") == "B":
            item["heat_score"] += 4
        # 偏好信息更完整的来源（摘要更长、非聚合跳转）
        completeness = len((item.get("summary") or "").strip())
        if "news.google.com" in (item.get("url") or ""):
            completeness -= 80
        if item.get("is_social"):
            completeness += 20
        item["_completeness"] = completeness

    items = [
        it for it in items
        if not it.get("_is_product_landing")
        or it.get("heat_score", 0) >= PRODUCT_HEAT_THRESHOLD
    ]

    # ── 加载历史记录，进行隔日去重 ──
    history_urls = load_history()
    seen_urls = set()
    seen_titles = []
    seen_fingerprints = {}
    deduped = []

    items.sort(
        key=lambda x: (x.get("heat_score", 0), x.get("_completeness", 0)),
        reverse=True,
    )

    for item in items:
        url = item["url"].rstrip("/")
        title = item.get("title", "")
        fp = extract_content_fingerprint(item)

        if not title:
            continue
        if url in seen_urls or url in history_urls:
            continue
        if is_duplicate_title(title, seen_titles):
            continue

        # 同事件跨来源只保留最优一条（热度+信息完整度）
        if fp:
            prev = seen_fingerprints.get(fp)
            if prev is not None:
                old_score = prev.get("heat_score", 0) + prev.get("_completeness", 0) / 100.0
                new_score = item.get("heat_score", 0) + item.get("_completeness", 0) / 100.0
                if new_score > old_score:
                    try:
                        deduped.remove(prev)
                    except ValueError:
                        pass
                    seen_fingerprints[fp] = item
                else:
                    continue
            else:
                seen_fingerprints[fp] = item

        seen_urls.add(url)
        seen_titles.append(title)
        deduped.append(item)

    return enforce_diversity_with_pool(deduped)

def enforce_diversity(items):
    source_groups = {}
    for item in items:
        src = item["source"]
        source_groups.setdefault(src, []).append(item)

    capped = []
    for src, src_items in source_groups.items():
        capped.extend(src_items[:MAX_PER_SOURCE])

    domestic_items = [it for it in capped if it.get("source_type") == "domestic"]
    intl_items = [it for it in capped if it.get("source_type") != "domestic"]

    total_target = min(MAX_ITEMS, len(capped))
    total_target = max(total_target, MIN_ITEMS)
    total_target = min(total_target, len(capped))

    domestic_min = max(1, int(total_target * DOMESTIC_RATIO_MIN))
    domestic_max = int(total_target * DOMESTIC_RATIO_MAX)

    domestic_count = min(len(domestic_items), domestic_max)
    domestic_count = max(domestic_count, min(domestic_min, len(domestic_items)))

    intl_count = total_target - domestic_count
    intl_count = min(intl_count, len(intl_items))

    if intl_count < total_target - domestic_count:
        domestic_count = min(len(domestic_items), total_target - intl_count)

    final = intl_items[:intl_count] + domestic_items[:domestic_count]

    unique_sources = set(it["source"] for it in final)
    if len(unique_sources) < MIN_SOURCES and len(capped) > len(final):
        remaining = [it for it in capped if it not in final]
        remaining_sources = set(it["source"] for it in remaining) - unique_sources
        for src in remaining_sources:
            if len(final) >= MAX_ITEMS:
                break
            for it in remaining:
                if it["source"] == src:
                    final.append(it)
                    unique_sources.add(src)
                    break
            if len(unique_sources) >= MIN_SOURCES:
                break

    final.sort(key=lambda x: x.get("heat_score", 0), reverse=True)

    return final[:MAX_ITEMS]


def enforce_diversity_with_pool(items):
    a_items = [it for it in items if it.get("_pool") == "A"]
    b_items = [it for it in items if it.get("_pool") == "B"]
    final = enforce_diversity(a_items)
    if len(final) >= POOL_A_MIN_PUSH:
        print(f"      [v3.4] A池直推充足: {len(final)} 条")
        return final

    need = max(POOL_A_MIN_PUSH - len(final), 0)
    remaining_b = [it for it in b_items if it not in final]
    b_fill = enforce_diversity(remaining_b)[:need]
    merged = final + b_fill

    if len(merged) < MIN_ITEMS:
        need_more = MIN_ITEMS - len(merged)
        spillover = [it for it in items if it not in merged]
        merged.extend(spillover[:need_more])

    merged = enforce_diversity(merged)
    a_count = sum(1 for it in merged if it.get("_pool") == "A")
    b_count = sum(1 for it in merged if it.get("_pool") == "B")
    print(f"      [v3.4] 二级放行池补齐: A池 {a_count} 条 | B池 {b_count} 条 | 目标至少 {POOL_A_MIN_PUSH} 条")
    return merged

# ══════════════════════════════════════════════════════════════════════════════
# Ollama 生成中文标题与摘要（v3.0 重构）
# ══════════════════════════════════════════════════════════════════════════════

def _generate_single_summary(item, index, total):
    src_info = get_source_info(item["source"])
    src_tag = "国内" if src_info["type"] == "domestic" else "国际"

    # ── v3.3：按来源类型抽取上下文（新闻正文 / 视频字幕 / 标题兜底） ──
    article_excerpt = fetch_content_context(item)
    context_mode = item.get("_context_mode", "article")
    has_article = bool(article_excerpt)
    context_label_map = {
        "article": "文章正文",
        "subtitle": "视频字幕",
        "title_only": "标题与来源摘要",
    }
    context_label = context_label_map.get(context_mode, "资讯上下文")

    # ── v3.0 新增：构建已知实体映射参考 ──
    entity_reference = (
        "【已知AI公司-模型归属表（严格遵守，禁止混淆）】\n"
        "- Anthropic → Claude、Opus、Sonnet、Haiku\n"
        "- OpenAI → GPT、ChatGPT、DALL-E、Sora、o1、o3\n"
        "- Google → Gemini、Bard、PaLM、Gemma、Veo\n"
        "- Meta → Llama、LLaMA、SAM\n"
        "- Mistral → Mistral、Mixtral\n"
        "- xAI → Grok\n"
        "- 智谱AI/Zhipu → GLM、ChatGLM、CogView\n"
        "- 百度 → 文心一言、ERNIE\n"
        "- 阿里 → 通义千问、Qwen\n"
        "- 字节跳动 → 豆包、Doubao\n"
        "- 月之暗面 → Kimi、Moonshot\n"
        "- DeepSeek → DeepSeek\n"
        "- 百川智能 → Baichuan\n"
        "- Stability AI → Stable Diffusion\n"
        "- Nvidia → NeMo、Nemotron\n"
        "- Microsoft → Copilot、Phi\n"
    )

    # ── v3.0 修改：增强版 Prompt，包含原文上下文和反幻觉规则 ──
    article_context_section = ""
    if has_article:
        article_context_section = f"""
【{context_label}（最重要的参考依据，摘要必须基于此内容）】：
{article_excerpt}
"""

    prompt = f"""你是资深AI行业记者，精通中英文。将以下资讯转化为中文精华版。

{entity_reference}

⚠️ 最重要的规则 —— 反幻觉/反捏造：
1. 【严禁捏造归属关系】绝对不能将一个公司的模型/产品错误归属给另一个公司。
   例如：Claude/Opus 是 Anthropic 的，不是 Meta 的；Llama 是 Meta 的，不是 OpenAI 的。
   如果你不确定某个模型属于哪家公司，就不要在摘要中写归属关系。
2. 【严格基于原文】标题和摘要必须严格基于下方提供的原始资讯内容和文章正文（如有），
   不得添加原文中没有的事实、数据、或公司关系。
3. 【宁缺毋错】如果原文信息不足以判断具体细节，用更概括的表述代替，
   绝不能编造具体数字、公司名称、模型归属等关键信息。

其他规则：
4. 先判断是否真正与AI/人工智能直接相关
5. 国际新闻：不要简单翻译英文标题，要提炼中文读者最关心的信息点
6. 国内新闻：突出事件的行业影响和背景，避免公关稿式堆砌
7. 融资类、政策法规类新闻，除非涉及重大事件，否则标记 ai_related: false
8. 产品发布要说明核心功能和与竞品的差异
9. 技术突破要说明实际意义和潜在应用场景
10. 只保留“可学习、可复用、可实践”的信息；纯观点、纯营销、纯八卦，标记 ai_related: false
11. 如果上下文是“标题与来源摘要”（没有正文/字幕），摘要必须使用保守表述，不得编造细节或数据
12. 如果来源是视频平台，优先依据字幕；无字幕时仅基于标题和描述总结，不得幻想
13. 重点偏向 AI skill、AI skills、AI agent、AI tutorial、开源案例、教程指南、工作流与可复用实践
14. 如果内容不利于学习、复用或落地，即使是AI新闻也标记 ai_related: false

返回以下JSON（只输出JSON，不要输出其他任何内容）：
{{"ai_related":true,"practical_reusable":true,"emoji":"🤖","title_zh":"中文标题15-25字，像新闻编辑写的标题，不要直译","summary_zh":"中文摘要50-100字，严格基于原文，回答这件事为什么重要","category":"分类标签"}}

分类标签从以下选择：技术突破/融资/产品发布/政策法规/行业变动/应用落地/开源/研究

好标题示例：「OpenAI 关停 Sora：日烧百万美元，用户不到50万」
好摘要示例：「据华尔街日报调查，Sora 上线仅半年，全球用户从百万骤降至不足50万，每日运营成本高达100万美元。这揭示了AI视频生成领域叫好不叫座的残酷现实。」

注意：title_zh和summary_zh都必须是中文，绝对不能是英文！
{article_context_section}
资讯元信息：
标题: {item['title']}
RSS摘要: {item['summary']}
来源: {item['source']}({src_tag})
URL: {item['url']}"""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.3},
        }, timeout=120)
        text = resp.json()["message"]["content"].strip()

        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if not json_match:
            json_match = re.search(r'\{.*\}', text, re.DOTALL)

        if json_match:
            r = json.loads(json_match.group())

            if not r.get("ai_related", True):
                item["_remove"] = True
                print(f"      [{index}/{total}] 🚫 非AI相关，已过滤: {item['title'][:40]}")
                return

            if not r.get("practical_reusable", True):
                item["_remove"] = True
                print(f"      [{index}/{total}] 🚫 非实用/不可复用，已过滤: {item['title'][:40]}")
                return

            title_zh = r.get("title_zh", item["title"])
            summary_zh = r.get("summary_zh", item["summary"])

            # ── v3.0 新增：事实校验 ──
            is_valid, error_msg = validate_summary_facts(
                title_zh, summary_zh, article_excerpt
            )
            if not is_valid:
                print(f"      [{index}/{total}] ⚠️ 事实校验不通过: {error_msg}")
                print(f"      [{index}/{total}] 🔄 触发重新生成（去除错误归属）...")

                # 重新生成：在 prompt 中追加纠错指令
                correction_prompt = (
                    f"\n\n⚠️ 你上次生成的摘要存在严重事实错误：{error_msg}\n"
                    f"请重新生成，严格按照【已知AI公司-模型归属表】纠正错误。\n"
                    f"上次错误的输出：标题=\"{title_zh}\"，摘要=\"{summary_zh}\"\n"
                    f"请输出修正后的JSON："
                )

                try:
                    retry_resp = requests.post(OLLAMA_URL, json={
                        "model": OLLAMA_MODEL,
                        "messages": [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": text},
                            {"role": "user", "content": correction_prompt},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.1},
                    }, timeout=120)
                    retry_text = retry_resp.json()["message"]["content"].strip()
                    retry_match = re.search(r'\{[^{}]*\}', retry_text, re.DOTALL)
                    if retry_match:
                        r2 = json.loads(retry_match.group())
                        title_zh = r2.get("title_zh", title_zh)
                        summary_zh = r2.get("summary_zh", summary_zh)

                        # 二次校验
                        is_valid_2, error_msg_2 = validate_summary_facts(
                            title_zh, summary_zh, article_excerpt
                        )
                        if not is_valid_2:
                            print(f"      [{index}/{total}] ❌ 二次校验仍不通过，强制移除错误归属")
                            # 强制清除已知的错误归属文本
                            for pattern_str, _ in [
                                (r"Meta.{0,5}(?:Opus|Claude|Sonnet|Haiku)", ""),
                                (r"Google.{0,5}(?:GPT|ChatGPT|DALL-E|Sora)", ""),
                                (r"OpenAI.{0,5}(?:Gemini|Bard|PaLM|Gemma)", ""),
                            ]:
                                title_zh = re.sub(pattern_str, "", title_zh, flags=re.IGNORECASE).strip()
                                summary_zh = re.sub(pattern_str, "", summary_zh, flags=re.IGNORECASE).strip()
                        else:
                            print(f"      [{index}/{total}] ✅ 二次生成通过校验")
                except Exception as e2:
                    print(f"      [{index}/{total}] ❌ 重新生成失败: {e2}")

            item["title_zh"] = title_zh
            item["summary_zh"] = summary_zh
            item["emoji_override"] = r.get("emoji", "")
            item["category"] = r.get("category", "AI")

            context_flag = {"article": "📄", "subtitle": "🎞️", "title_only": "🧾"}.get(context_mode, "📋")
            print(f"      [{index}/{total}] ✅ {context_flag} {item['title_zh'][:40]}")
        else:
            print(f"      [{index}/{total}] ⚠️ JSON解析失败，使用原文: {item['title'][:40]}")
            item["title_zh"] = item["title"]
            item["summary_zh"] = item["summary"]
    except Exception as e:
        print(f"      [{index}/{total}] ❌ Ollama调用失败: {e}")
        item["title_zh"] = item["title"]
        item["summary_zh"] = item["summary"]

def generate_chinese_summaries(items):
    total = len(items)
    print(f"      逐条调用 Ollama ({OLLAMA_MODEL})，共 {total} 条...")
    print(f"      [v3.3] 已启用文章正文/视频字幕抓取 + 实用导向 + 反幻觉校验")

    for i, item in enumerate(items, 1):
        _generate_single_summary(item, i, total)
        if i < total:
            time.sleep(0.5)

    filtered_count = sum(1 for it in items if it.get("_remove"))
    items = [it for it in items if not it.get("_remove")]
    print(f"      完成: {total} 条已处理, {filtered_count} 条被过滤为非AI相关")

    for item in items:
        if "title_zh" not in item:
            item["title_zh"] = item["title"]
        if "summary_zh" not in item:
            item["summary_zh"] = item["summary"]
        if "category" not in item:
            item["category"] = "AI"

    return items

# ══════════════════════════════════════════════════════════════════════════════
# 标签推断
# ══════════════════════════════════════════════════════════════════════════════

TAG_RULES = [
    (re.compile(r"llm|gpt|claude|gemini|model|mistral|anthropic|openai|大模型|qwen|glm|deepseek|baichuan", re.I),
     "大模型", "tag-llm", "\U0001f916"),
    (re.compile(r"教程|实战|部署|接入|workflow|agent|RAG|自动化|复用|模板|api|sdk|落地|案例", re.I),
     "实用", "tag-product", "\U0001f6e0\ufe0f"),
    (re.compile(r"音频|播客|podcast|voice|配音|asr|tts|daw|vst|混音|母带|转写", re.I),
     "音频AI", "tag-product", "\U0001f3a7"),
    (re.compile(r"fund|rais|invest|ipo|valuat|\$\d|billion|million|serie|融资|估值|上市", re.I),
     "融资", "tag-biz", "\U0001f4b0"),
    (re.compile(r"open.?source|hugging|apache|mit.license|开源", re.I),
     "开源", "tag-open", "\U0001f331"),
    (re.compile(r"regulat|policy|govern|law|eu.ai|congress|senate|ban|court|政策|监管|法规", re.I),
     "政策", "tag-policy", "\U0001f3db"),
    (re.compile(r"launch|releas|announc|introduc|new.feature|product|发布|上线|推出", re.I),
     "产品", "tag-product", "\U0001f680"),
    (re.compile(r"research|study|scientif|danger|risk|warning|研究|论文|突破", re.I),
     "研究", "tag-research", "\U0001f52c"),
    (re.compile(r"secur|privacy|hack|exploit|vulnerab|data.collect|track|安全|隐私", re.I),
     "安全", "tag-policy", "\U0001f512"),
    (re.compile(r"chip|gpu|nvidia|hardware|data.center|infra|芯片|算力|基础设施", re.I),
     "基础设施", "tag-other", "\u2699\ufe0f"),
    (re.compile(r"video|image|generat|sora|diffusion|creative|视频|图像|生成", re.I),
     "创作", "tag-product", "\U0001f3a8"),
    (re.compile(r"agent|autonom|coding.agent|智能体|具身智能|机器人", re.I),
     "Agent", "tag-llm", "\U0001f9e0"),
    (re.compile(r"国产|中国|百度|阿里|腾讯|字节|华为|讯飞|智谱|月之暗面", re.I),
     "国产AI", "tag-domestic", "\U0001f3ee"),
]

def infer_tags(item):
    text = f"{item['title']} {item.get('summary', '')} {item.get('title_zh', '')} {item.get('summary_zh', '')}"
    tags = []
    for pattern, label, css, emoji in TAG_RULES:
        if pattern.search(text):
            tags.append((label, css, emoji))
    if not tags:
        tags.append(("AI", "tag-other", "\u2728"))
    return tags[:3]

def pick_emoji(item):
    if item.get("emoji_override"):
        return item["emoji_override"]
    tags = infer_tags(item)
    return tags[0][2]

# ══════════════════════════════════════════════════════════════════════════════
# HTML 生成
# ══════════════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <meta name="aimok-generated-at" content="{generated_at}">
    <title>AI'm OK-{date}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                         "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            background: #0a0a0f;
            min-height: 100vh;
            padding: 24px 16px;
        }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        .header {{
            text-align: center;
            padding: 48px 0 36px;
            color: white;
        }}
        .header .logo {{ font-size: 56px; margin-bottom: 8px; }}
        .header h1 {{
            font-size: 2.6em;
            font-weight: 800;
            letter-spacing: 2px;
            text-shadow: 0 2px 12px rgba(0,0,0,0.15);
        }}
        .header .subtitle {{
            font-size: 1.05em;
            opacity: 0.9;
            margin-top: 10px;
            font-weight: 400;
            letter-spacing: 1px;
        }}
        .stats {{
            display: flex;
            justify-content: center;
            gap: 24px;
            margin-top: 18px;
            flex-wrap: wrap;
        }}
        .stat {{
            background: rgba(255,255,255,0.08);
            backdrop-filter: blur(10px);
            padding: 6px 18px;
            border-radius: 20px;
            font-size: 13px;
            color: rgba(255,255,255,0.7);
        }}
        .tabs {{
            display: flex;
            justify-content: center;
            gap: 16px;
            margin-bottom: 28px;
        }}
        .tab-btn {{
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.12);
            color: rgba(255,255,255,0.6);
            padding: 12px 32px;
            border-radius: 28px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            letter-spacing: 1px;
            outline: none;
        }}
        .tab-btn:hover {{
            background: rgba(255,255,255,0.12);
            color: rgba(255,255,255,0.85);
            border-color: rgba(255,255,255,0.2);
        }}
        .tab-btn.active {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-color: transparent;
            box-shadow: 0 4px 18px rgba(102, 126, 234, 0.4);
        }}
        .tab-count {{
            display: inline-block;
            background: rgba(255,255,255,0.2);
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 12px;
            margin-left: 6px;
        }}
        .tab-btn.active .tab-count {{
            background: rgba(255,255,255,0.25);
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
        .cards-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 18px;
        }}
        @media (max-width: 640px) {{
            .cards-grid {{ grid-template-columns: 1fr; }}
            .header h1 {{ font-size: 1.8em; }}
            .header .logo {{ font-size: 42px; }}
            .stats {{ gap: 12px; }}
            .tabs {{ gap: 10px; }}
            .tab-btn {{ padding: 10px 24px; font-size: 14px; }}
        }}
        .card {{
            background: #16161e;
            border-radius: 20px;
            padding: 22px 24px;
            box-shadow: 0 2px 16px rgba(0,0,0,0.3);
            transition: transform 0.25s ease, box-shadow 0.25s ease;
            cursor: pointer;
            text-decoration: none;
            color: inherit;
            display: block;
            border: 1px solid rgba(255,255,255,0.06);
            position: relative;
        }}
        .card:hover {{
            transform: translateY(-6px);
            box-shadow: 0 12px 36px rgba(0,0,0,0.5);
            border-color: rgba(255,255,255,0.12);
        }}
        .source-badge {{
            position: absolute;
            top: 12px;
            right: 14px;
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 10px;
            font-weight: 600;
        }}
        .badge-intl {{
            background: rgba(26, 115, 232, 0.15);
            color: #4a9eff;
        }}
        .badge-domestic {{
            background: rgba(255, 107, 107, 0.15);
            color: #ff6b6b;
        }}
        .tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; align-items: center; }}
        .tag {{
            font-size: 11px;
            padding: 4px 12px;
            border-radius: 20px;
            font-weight: 600;
            letter-spacing: 0.3px;
        }}
        .tag-llm {{ background: #e8f0fe; color: #1a73e8; }}
        .tag-biz {{ background: #fce8e6; color: #d93025; }}
        .tag-open {{ background: #e6f4ea; color: #137333; }}
        .tag-policy {{ background: #e8f0fe; color: #1a73e8; }}
        .tag-product {{ background: #f3e8fd; color: #7c3aed; }}
        .tag-research {{ background: #e0f7fa; color: #00838f; }}
        .tag-other {{ background: #f5f5f5; color: #666666; }}
        .tag-domestic {{ background: #ffe8e8; color: #d93025; }}
        .tag-emoji {{
            font-size: 16px;
            line-height: 1;
        }}
        .card-title {{
            font-size: 16px;
            font-weight: 700;
            color: #e8e8ed;
            margin-bottom: 10px;
            line-height: 1.5;
            padding-right: 50px;
        }}
        .card-summary {{
            font-size: 13.5px;
            color: #a0a0b0;
            line-height: 1.75;
            margin-bottom: 14px;
        }}
        .card-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
            color: #aaa;
        }}
        .card-source {{
            background: #1e1e2a;
            padding: 2px 10px;
            border-radius: 10px;
            font-size: 11px;
            color: #6a6a80;
        }}
        .card-arrow {{
            color: #6a6a80;
            font-size: 14px;
        }}
        .footer {{
            text-align: center;
            color: rgba(255,255,255,0.35);
            padding: 36px 0;
            font-size: 13px;
        }}
        .build-info {{
            margin-top: 10px;
            font-size: 12px;
            color: rgba(255,255,255,0.45);
            letter-spacing: 0.2px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">\U0001f955</div>
            <h1>AI'm OK-{date}</h1>
            <div class="subtitle">每日AI行业资讯精选 | 国内外多源聚合 | 用最少的时间掌握最新动态</div>
            <div class="stats">
                <span class="stat">{count} 条精选</span>
                <span class="stat">🌐 {intl_count} 条国际</span>
                <span class="stat">🏮 {domestic_count} 条国内</span>
                <span class="stat">{source_count} 个来源</span>
            </div>
        </div>
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('intl', this)">🌐国际资讯<span class="tab-count">{intl_count}</span></button>
            <button class="tab-btn" onclick="switchTab('domestic', this)">🏮国内资讯<span class="tab-count">{domestic_count}</span></button>
        </div>
        <div id="tab-intl" class="tab-content active">
            <div class="cards-grid">
{intl_cards}
            </div>
        </div>
        <div id="tab-domestic" class="tab-content">
            <div class="cards-grid">
{domestic_cards}
            </div>
        </div>
        <div class="footer">
            \U0001f955 由 AI'm OK v3.2 自动生成 | {date} | 国内外 {source_count} 源聚合
            <div class="build-info">生成时间：{generated_at} | Build: {build_id}</div>
        </div>
    </div>
    <script>
        function switchTab(tab, btn) {{
            document.querySelectorAll('.tab-content').forEach(function(el) {{
                el.classList.remove('active');
            }});
            document.querySelectorAll('.tab-btn').forEach(function(el) {{
                el.classList.remove('active');
            }});
            document.getElementById('tab-' + tab).classList.add('active');
            btn.classList.add('active');
        }}
    </script>
</body>
</html>"""

CARD_TEMPLATE = """            <a class="card" href="{url}" target="_blank" rel="noopener">
                <span class="source-badge {badge_class}">{source_icon}</span>
                <div class="tags"><span class="tag-emoji">{emoji}</span> {tags_html}</div>
                <div class="card-title"><strong>{title}</strong></div>
                <div class="card-summary">{summary}</div>
                <div class="card-meta">
                    <span class="card-source">{source_display}</span>
                    <span class="card-arrow">\u2192</span>
                </div>
            </a>"""

def _build_card_html(item):
    tags = infer_tags(item)
    tags_html = "".join(
        f'<span class="tag {css}">{escape(label)}</span>' for label, css, emoji in tags
    )
    src_info = get_source_info(item["source"])
    badge_class = "badge-domestic" if src_info["type"] == "domestic" else "badge-intl"
    source_icon = src_info["icon"]

    return CARD_TEMPLATE.format(
        url=escape(item["url"]),
        tags_html=tags_html,
        emoji=pick_emoji(item),
        title=escape(item.get("title_zh", item["title"])),
        summary=escape(item.get("summary_zh", item["summary"])),
        source_display=escape(src_info["display"]),
        badge_class=badge_class,
        source_icon=source_icon,
    )

def generate_html(items, date_str):
    intl_items = [it for it in items if it.get("source_type") != "domestic"]
    domestic_items = [it for it in items if it.get("source_type") == "domestic"]

    intl_count = len(intl_items)
    domestic_count = len(domestic_items)
    source_count = len(set(it["source"] for it in items))
    generated_at = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    build_id = str(int(time.time()))

    intl_cards = "\n".join(_build_card_html(item) for item in intl_items)
    domestic_cards = "\n".join(_build_card_html(item) for item in domestic_items)

    return HTML_TEMPLATE.format(
        date=date_str,
        generated_at=generated_at,
        build_id=build_id,
        intl_cards=intl_cards,
        domestic_cards=domestic_cards,
        count=len(items),
        intl_count=intl_count,
        domestic_count=domestic_count,
        source_count=source_count,
    )


def build_review_feedback_records(all_review_items, selected_items):
    selected_urls = {str(it.get("url", "") or "").rstrip("/") for it in selected_items}
    rows = []
    ts = datetime.now(BEIJING_TZ).isoformat()
    for item in all_review_items:
        url = str(item.get("url", "") or "").rstrip("/")
        labels = _normalize_feedback_labels(item.get("_review_feedback_labels", []))
        rows.append({
            "timestamp": ts,
            "selected": url in selected_urls,
            "source": item.get("source", ""),
            "category": item.get("category", ""),
            "pool": item.get("_pool", ""),
            "url": url,
            "title": item.get("title", ""),
            "title_zh": item.get("title_zh", ""),
            "labels": labels,
            "terms": _extract_feedback_terms(item),
            "practical_score": item.get("practical_score", 0),
            "audio_score": item.get("audio_score", 0),
            "heat_score": item.get("heat_score", 0),
            "is_video": bool(item.get("is_video")),
        })
    return rows

# ══════════════════════════════════════════════════════════════════════════════
# 飞书推送
# ══════════════════════════════════════════════════════════════════════════════

def build_feishu_card(items, date_str):
    feishu_items = sorted(items, key=lambda x: x.get("heat_score", 0), reverse=True)[:FEISHU_TOP_N]

    total_count = len(items)
    feishu_count = len(feishu_items)

    source_count = len(set(it["source"] for it in feishu_items))

    intl_items = [it for it in feishu_items if it.get("source_type") != "domestic"]
    domestic_items = [it for it in feishu_items if it.get("source_type") == "domestic"]

    elements = []

    def _append_news_items(news_items):
        for i, item in enumerate(news_items):
            tags = infer_tags(item)
            tag_str = " | ".join(label for label, _, _ in tags)
            emoji = pick_emoji(item)
            title_zh = item.get("title_zh", item["title"])
            summary_zh = item.get("summary_zh", item["summary"])
            src_info = get_source_info(item["source"])
            source_display = src_info["display"]

            elements.append({
                "tag": "markdown",
                "content": f"<font color='grey'>{emoji} {tag_str} · 🥕 {source_display}</font>",
            })
            elements.append({
                "tag": "markdown",
                "content": f"<font color='indigo'>**{title_zh}**</font>",
                "text_size": "large",
            })
            elements.append({
                "tag": "markdown",
                "content": summary_zh,
            })
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看原文"},
                    "type": "primary",
                    "url": item["url"],
                }],
            })
            if i < len(news_items) - 1:
                elements.append({"tag": "hr"})

    if intl_items:
        elements.append({
            "tag": "markdown",
            "content": "<font color='orange'>**🌐国际资讯**</font>",
            "text_size": "heading",
        })
        elements.append({"tag": "hr"})
        _append_news_items(intl_items)

    if intl_items and domestic_items:
        elements.append({"tag": "hr"})

    if domestic_items:
        elements.append({
            "tag": "markdown",
            "content": "<font color='orange'>**🏮国内资讯**</font>",
            "text_size": "heading",
        })
        elements.append({"tag": "hr"})
        _append_news_items(domestic_items)

    elements.append({"tag": "hr"})

    if total_count > feishu_count:
        elements.append({
            "tag": "markdown",
            "content": f"<font color='grey'> 🥕 以上为今日热度最高的 {feishu_count} 条精选，完整 {total_count} 条资讯请查看网页版 👇</font>",
        })

    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看完整网页版"},
            "type": "default",
            "url": f"{PAGES_URL}/latest.html",
        }],
    })

    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": f"由 AI'm OK 自动生成 | {date_str} | {source_count}源聚合 | 飞书精选Top{feishu_count}",
        }],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"\U0001f955 AI'm OK-{date_str} | Today's AI",
                },
                "template": "orange",
            },
            "elements": elements,
        },
    }

def push_feishu(payload):
    success_count = 0
    for i, webhook in enumerate(FEISHU_WEBHOOKS, 1):
        try:
            resp = requests.post(
                webhook.strip(),
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            result = resp.json()
            if result.get("StatusCode") == 0 or result.get("code") == 0:
                print(f"[OK] Feishu push succeeded ✅ -> 群{i}")
                success_count += 1
            else:
                print(f"[WARN] Feishu response -> 群{i}: {result}")
        except Exception as e:
            print(f"[ERROR] Feishu push failed -> 群{i}: {e}")
    return success_count > 0

def publish_to_pages(html_content, date_str):
    try:
        pages = PAGES_DIR
        pages.mkdir(parents=True, exist_ok=True)
        (pages / "latest.html").write_text(html_content, encoding="utf-8")
        (pages / "index.html").write_text(html_content, encoding="utf-8")
        (pages / f"AI-m-OK-{date_str}.html").write_text(html_content, encoding="utf-8")
        git_dir = pages / ".git"
        if not git_dir.exists():
            print(f"[WARN] GitHub Pages 目录不是 git 仓库，仅写入静态文件: {pages}")
            return

        subprocess.run(["git", "add", "-A"], cwd=str(pages), check=True, capture_output=True)
        diff_proc = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(pages),
            capture_output=True,
        )
        if diff_proc.returncode == 0:
            print(f"      Published locally: {PAGES_URL}/latest.html (无新增变更，仍检查远端同步)")
        else:
            subprocess.run(
                ["git", "commit", "-m", f"update: AI-m-OK {date_str}"],
                cwd=str(pages), check=True, capture_output=True,
            )

        try:
            subprocess.run(
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                cwd=str(pages),
                check=True,
                capture_output=True,
                timeout=60,
            )
        except Exception as pull_err:
            print(f"[WARN] GitHub Pages pull --rebase failed: {pull_err}")
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(pages), check=True, capture_output=True, timeout=60,
        )
        print(f"      Published: {PAGES_URL}/latest.html ✅")
    except Exception as e:
        print(f"[WARN] GitHub Pages push failed: {e}")


def republish_latest_pages_only(date_str):
    candidates = [
        OUTPUT_DIR / f"AI-m-OK-{date_str}.html",
        PAGES_DIR / f"AI-m-OK-{date_str}.html",
        PAGES_DIR / "latest.html",
        PAGES_DIR / "index.html",
    ]
    source_path = next((p for p in candidates if p.exists()), None)
    if not source_path:
        print("[ERROR] 找不到可重发的 HTML，请先完整运行一次脚本生成网页。")
        return

    html = source_path.read_text(encoding="utf-8", errors="ignore")
    stamp = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    marker = (
        "\n<div style=\"text-align:center;color:rgba(255,255,255,0.45);"
        "font-size:12px;padding:0 0 24px;\">"
        f"网页刷新时间：{escape(stamp)}</div>\n"
    )
    if "网页刷新时间：" in html:
        html = re.sub(
            r"<div style=\"text-align:center;color:rgba\(255,255,255,0\.45\);font-size:12px;padding:0 0 24px;\">网页刷新时间：.*?</div>",
            marker.strip(),
            html,
            count=1,
            flags=re.DOTALL,
        )
    elif "</body>" in html:
        html = html.replace("</body>", marker + "</body>", 1)
    else:
        html += marker

    print(f"      重发 HTML 来源: {source_path}")
    publish_to_pages(html, date_str)

# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════

def main():
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    if "--republish-pages" in sys.argv:
        print(f"\n{'='*60}")
        print(f"  AI'm OK 网页重发模式 | {today}")
        print(f"{'='*60}\n")
        print(f"🗂️  发布目录: {PAGES_DIR}")
        republish_latest_pages_only(today)
        return

    print(f"\n{'='*60}")
    print(f"  🥕AI'm OK v3.3 | {today}")
    print(f"  多源聚合 · 实用导向筛选 · 视频字幕抽取 · 反幻觉校验 · 72h时效 · 隔日去重")
    print(f"{'='*60}\n")
    print(f"🗂️  发布目录: {PAGES_DIR}")

    print("📡 [Phase A] 聚合源抓取...")
    tldr = fetch_tldr()
    hn = fetch_hackernews()
    wired = fetch_wired_ai()

    print("\n📰 [Phase B] 国际权威媒体抓取...")
    tc = fetch_techcrunch()
    tv = fetch_theverge()
    ars = fetch_arstechnica()
    vb = fetch_venturebeat()
    mit = fetch_mit_tech_review()
    ieee = fetch_ieee_spectrum()

    print("\n🎬 [Phase B.5] 社媒与视频平台抓取（实用导向）...")
    yt = fetch_youtube()
    bz = fetch_bilibili()
    wx_articles = fetch_wechat_articles()
    video_extra = fetch_video_tutorial_sources()
    amg = fetch_audio_music_game_tutorials()
    practical_guides = fetch_practical_guides()
    agent_guides = fetch_agent_coding_guides()
    audio_creator_guides = fetch_audio_creator_guides()
    ai_frontier = fetch_ai_frontier()

    print("\n🏮 [Phase C] 国内权威媒体抓取...")
    jqzx = fetch_jiqizhixin()
    qb = fetch_qbitai()
    kr = fetch_36kr()
    ith = fetch_ithome()
    xzy = fetch_xinzhiyuan()
    iq = fetch_infoq()
    sina = fetch_sina_tech()
    tt = fetch_toutiao()
    pp = fetch_thepaper()

    print("\n📊 [Phase D] 抓取状态检查...")
    tracker.print_report()

    supp_intl = []
    supp_domestic = []
    if tracker.intl_success_count < MIN_INTL_SUCCESS:
        supp_intl = supplementary_search_intl()
    if tracker.domestic_success_count < MIN_DOMESTIC_SUCCESS:
        supp_domestic = supplementary_search_domestic()

    print("\n🔄 [Phase E] 合并去重排序（实用筛选 + 热度排序 + 72小时 + 隔日去重）...")
    all_items = (
        tldr + hn + wired +
        tc + tv + ars + vb + mit + ieee +
        yt + bz + wx_articles + video_extra + amg + practical_guides + agent_guides + audio_creator_guides + ai_frontier +
        jqzx + qb + kr + ith + xzy + iq +
        sina + tt + pp +
        supp_intl + supp_domestic
    )
    print(f"      Total raw: {len(all_items)}")

    final = deduplicate_and_rank(all_items)
    print(f"      After dedup + diversity + heat sort + filters: {len(final)}")

    if not final:
        print("[ERROR] No items fetched. Check network. ❌")
        return

    print(f"\n✍️  [Phase F] Generating Chinese summaries (v3.3 正文/字幕抽取 + 反幻觉 + 实用导向)...")
    final = generate_chinese_summaries(final)
    review_candidates = [dict(item) for item in final]

     # ══════════════════════════════════════════════════════════════
    # ★ 新增 Phase F.5：本地 Web 审核（传入 --auto 参数可跳过）
    # ══════════════════════════════════════════════════════════════
    if "--auto" not in sys.argv and start_review_server is not None:
        print("\n🔍 [Phase F.5] 启动本地审核页面...")
        final = start_review_server(
            items=review_candidates,
            infer_tags_func=infer_tags,
            pick_emoji_func=pick_emoji,
            get_source_info_func=get_source_info,
            port=18088,
        )
        if not final:
            print("[INFO] 所有条目被过滤或用户取消，本次不推送。")
            return
        print(f"      审核后保留 {len(final)} 条，继续推送流程...")
        append_review_feedback(build_review_feedback_records(review_candidates, final))
    elif "--auto" not in sys.argv and start_review_server is None:
        print("\n⏩ [Phase F.5] 未找到 review_server.py，自动跳过人工审核")
    else:
        print("\n⏩ [Phase F.5] --auto 模式，跳过人工审核")

    html = generate_html(final, today)
    output_path = OUTPUT_DIR / f"AI-m-OK-{today}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"\n📄 [Phase G] HTML saved: {output_path}")

    print("\n🚀 [Phase H] Publishing...")
    publish_to_pages(html, today)

    card = build_feishu_card(final, today)
    feishu_ok = push_feishu(card)
    print(f"      飞书推送: Top {min(FEISHU_TOP_N, len(final))} 条 | 网页版: 全部 {len(final)} 条")

    # ── 只有飞书真正推送成功后，才保存历史；审核阶段不算推送 ──
    if feishu_ok:
        pushed_urls = {it["url"].rstrip("/") for it in final}
        save_history(pushed_urls)
        print(f"      已保存 {len(pushed_urls)} 条飞书推送记录到历史文件，防止后续重复推送。")
    else:
        print("      飞书推送未成功，本次不写入历史，避免审核过但未送达的内容被误去重。")

    intl_final = sum(1 for it in final if it.get("source_type") != "domestic")
    dom_final = sum(1 for it in final if it.get("source_type") == "domestic")
    print(f"\n{'='*60}")
    print(f"  ✅ Done! {len(final)} items ({intl_final} intl + {dom_final} domestic)")
    print(f"  📲 飞书推送: Top {min(FEISHU_TOP_N, len(final))} 条热点")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()

