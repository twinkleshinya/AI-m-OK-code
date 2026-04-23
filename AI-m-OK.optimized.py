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
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from html import escape, unescape
from pathlib import Path
from urllib.parse import quote_plus, urlparse
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
    "https://open.feishu.cn/open-apis/bot/v2/hook/30bd0594-8318-4475-9f34-e0ed5a65de00"
).split(",")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAwesMzAFIU45qjxw0ISW92L-ufU4tFG78")
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3:14b"

OUTPUT_DIR = Path.home()
PAGES_DIR = Path.home() / "AI-m-OK"
PAGES_URL = "https://twinkleshinya.github.io/AI-m-OK"
HISTORY_FILE = PAGES_DIR / "push_history.json"

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
OLD_NEWS_HOURS = 72
MAX_FUNDING_POLICY = 2
PRODUCT_HEAT_THRESHOLD = 90
FEISHU_TOP_N = 15

# ── 实用导向筛选（v3.3） ──
PRACTICAL_STRICT_ONLY = os.environ.get("PRACTICAL_STRICT_ONLY", "1").strip().lower() not in {"0", "false", "no"}
PRACTICAL_MIN_SCORE = int(os.environ.get("PRACTICAL_MIN_SCORE", "2"))
VIDEO_MAX_AGE_DAYS = int(os.environ.get("VIDEO_MAX_AGE_DAYS", "7"))

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

NITTER_BASES = [
    x.strip().rstrip("/")
    for x in os.environ.get(
        "NITTER_BASES",
        "https://nitter.poast.org,https://nitter.privacydev.net,https://nitter.net"
    ).split(",")
    if x.strip()
]

# ── v3.0 新增：文章正文抓取配置 ──
ARTICLE_EXCERPT_MAX_CHARS = 1200
ARTICLE_FETCH_TIMEOUT = 10

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
    r"|融资|估值|上市|A轮|B轮|C轮|D轮|天使轮|种子轮|pre-A"
    r"|regulat|policy|govern|law|eu.ai|congress|senate|ban|court"
    r"|政策|监管|法规|合规|立法|审查|治理",
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
    r"|skill|skills|skillset|agentic|copilot|n8n|zapier|make\.com|dify|coze"
    r"|音频|播客|podcast|voice|配音|降噪|混音|母带|转写|ASR|TTS|DAW|VST|MIDI|采样",
    re.IGNORECASE,
)

REUSABLE_SIGNAL = re.compile(
    r"open.?source|repo|github|模板|template|脚手架|boilerplate|sdk|api|示例代码|代码仓库"
    r"|插件市场|workflow模板|automation模板|prompt模板|agent模板|工程模板",
    re.IGNORECASE,
)

INNOVATION_SIGNAL = re.compile(
    r"新模型|模型发布|技术突破|新范式|架构创新|推理能力|多模态|agentic"
    r"|reasoning|benchmark|SOTA|state.of.the.art|latency|成本下降|效率提升"
    r"|发布|launch|release|introduce|rollout",
    re.IGNORECASE,
)

MODEL_SIGNAL = re.compile(
    r"model|llm|gpt|claude|gemini|qwen|deepseek|mistral|agent|多模态|大模型|推理模型",
    re.IGNORECASE,
)

APPLICATION_SIGNAL = re.compile(
    r"应用|场景|落地|部署|workflow|自动化|效率|生产力|集成|api|sdk|tool|agent|RAG|实战|教程|案例"
    r"|音频制作|播客制作|配音工作流|语音克隆|字幕转写|音频后期|音乐生成",
    re.IGNORECASE,
)

LOW_VALUE_SIGNAL = re.compile(
    r"融资|估值|人事|任命|合作签约|生态合作|政策|监管|法案|诉讼|广告|赞助|带货"
    r"|明星|八卦|营销|发布会回顾",
    re.IGNORECASE,
)

SOCIAL_VIDEO_DOMAINS = re.compile(
    r"bilibili\.com|b23\.tv|youtube\.com|youtu\.be|weibo\.com|twitter\.com|x\.com",
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
]

MODEL_INNOVATION_QUERIES = [
    "new AI model release benchmark",
    "reasoning model launch practical usage",
    "多模态 模型 发布 实践",
    "AI 技术突破 应用场景",
    "audio AI model release",
    "voice agent workflow",
]

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
    "YouTube":          {"type": "intl", "display": "YouTube",          "icon": "📺"},
    "Twitter":          {"type": "intl", "display": "Twitter",          "icon": "💬"},
    "X":                {"type": "intl", "display": "X",                "icon": "💬"},
    "B站":               {"type": "domestic", "display": "B站",           "icon": "📺"},
    "微博":              {"type": "domestic", "display": "微博",          "icon": "📣"},
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
    "YouTube": 78,
    "Twitter": 72,
    "X": 72,
    "B站": 80,
    "微博": 68,
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

# ══════════════════════════════════════════════════════════════════════════════
# 通用抓取工具函数
# ══════════════════════════════════════════════════════════════════════════════

def safe_request(url, timeout=15, headers=None):
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if headers:
        default_headers.update(headers)
    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=timeout, headers=default_headers)
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt == 0:
                time.sleep(1)
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


def _fetch_google_news_site(site_domain, source_name, extra_queries, max_entries=8):
    urls = []
    for q in extra_queries:
        query = f"site:{site_domain} ({q})"
        urls.append(build_google_news_rss(query))
    items = parse_rss_feed_candidates(
        urls=urls,
        source_name=source_name,
        max_entries=max_entries,
        ai_filter=False,
    )
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
    return None


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
    base = NITTER_BASES[0] if NITTER_BASES else "https://nitter.net"
    base = base.rstrip("/")
    if "x.com/" in u:
        return u.replace("https://x.com/", f"{base}/").replace("http://x.com/", f"{base}/")
    if "twitter.com/" in u:
        return u.replace("https://twitter.com/", f"{base}/").replace("http://twitter.com/", f"{base}/")
    return u


def is_theverge_paywalled(item):
    """
    粗粒度付费墙检测：标题/摘要/URL 命中 paywall 相关词则剔除。
    """
    t = item.get("title", "")
    s = item.get("summary", "")
    u = item.get("url", "")
    text = f"{t} {s} {u}"
    return bool(re.search(r"subscriber|subscription|paywall|exclusive|members.?only|premium", text, re.IGNORECASE))


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
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_entries]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "")
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            if len(summary) > 250:
                summary = summary[:250] + "..."

            if ai_filter:
                text = f"{title} {summary}"
                if not (AI_KEYWORDS.search(text) or AI_KEYWORDS_ZH.search(text)):
                    continue

            if is_github_url(link):
                continue
            if GITHUB_TITLE_FILTER.search(title):
                continue
            if PAPER_FILTER.search(title) or PAPER_FILTER.search(link):
                continue
            # ★ v3.1 拦截底部备案文本
            if FOOTER_TEXT_FILTER.search(title):
                continue

            normalized_date, inferred_date = normalize_entry_date(entry, link=link)
            items.append({
                "title": title,
                "url": link,
                "summary": summary,
                "source": source_name,
                "source_type": get_source_info(source_name)["type"],
                "date": normalized_date,
                "date_inferred": inferred_date,
                "fetched_at": _now_iso(),
                "score": 0,
            })
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
                if is_github_url(link_url):
                    continue
                if GITHUB_TITLE_FILTER.search(title):
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
    从 YouTube 页面解析发布时间（优先）。
    """
    try:
        resp = safe_request(url, timeout=ARTICLE_FETCH_TIMEOUT)
        if not resp:
            return ""
        html = resp.text

        m = re.search(r'"publishDate":"(\d{4}-\d{2}-\d{2})"', html)
        if m:
            return m.group(1)
        m = re.search(r'"uploadDate":"(\d{4}-\d{2}-\d{2})"', html)
        if m:
            return m.group(1)
        m = re.search(r'"datePublished":"(\d{4}-\d{2}-\d{2})"', html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _extract_bilibili_published_date(url):
    """
    从 B 站页面解析发布时间（优先）。
    """
    try:
        resp = safe_request(url, timeout=ARTICLE_FETCH_TIMEOUT, headers={"Referer": "https://www.bilibili.com/"})
        if not resp:
            return ""
        html = resp.text

        # pubdate 可能是 unix 时间戳
        m = re.search(r'"pubdate":\s*(\d{10})', html)
        if m:
            ts = int(m.group(1))
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(BEIJING_TZ).strftime("%Y-%m-%d")

        m = re.search(r'"pub_time":"(\d{4}-\d{2}-\d{2})', html)
        if m:
            return m.group(1)
        m = re.search(r'"datePublished":"(\d{4}-\d{2}-\d{2})"', html)
        if m:
            return m.group(1)
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
            original_url = story.get("url", f"https://news.ycombinator.com/item?id={sid}")
            hn_url = f"https://news.ycombinator.com/item?id={sid}"
            url = original_url

            # ══════════════════════════════════════════════════════════════
            # ★ v3.1 修改：HackerNews 映射修复（增强版，新增产品官网自动检测）
            # ══════════════════════════════════════════════════════════════
            if (PRODUCT_LANDING_FILTER.search(url) or
                PRODUCT_SITE_DOMAINS.search(url) or
                HARD_BLOCK_DOMAINS.search(url) or
                "claude.com" in url or
                "anthropic.com" in url or
                _is_product_homepage(url)):        # ← v3.1 新增：通用产品官网自动检测
                url = hn_url

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
    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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

    items = [it for it in items if not is_github_url(it["url"])]
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

    items = [it for it in items if not is_github_url(it["url"])]
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

    items = [it for it in items if not is_github_url(it["url"])]
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

    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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

    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
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
    items = [it for it in items if not is_github_url(it["url"])]
    tracker.record(source, items)
    return items


def fetch_youtube():
    source = "YouTube"
    items = []

    # 1) 优先使用用户显式配置的官方频道 feed
    if YOUTUBE_FEED_URLS:
        items.extend(
            parse_rss_feed_candidates(
                urls=YOUTUBE_FEED_URLS,
                source_name=source,
                max_entries=10,
                ai_filter=False,
            )
        )

    # 2) Google News 站内检索（无需 API key）
    if not items:
        items.extend(
            _fetch_google_news_site(
                "youtube.com",
                source_name=source,
                extra_queries=SOCIAL_PRACTICAL_QUERIES + MODEL_INNOVATION_QUERIES,
                max_entries=8,
            )
        )

    # 3) RSSHub 关键词检索（可选）
    if len(items) < 5:
        items.extend(
            _fetch_rsshub_keyword(
                route_template="youtube/keyword/{keyword}",
                source_name=source,
                keywords=SOCIAL_PRACTICAL_QUERIES,
                max_entries=8,
            )
        )

    dedup = []
    seen = set()
    for it in items:
        k = it.get("url", "").rstrip("/")
        if not k or k in seen:
            continue
        # 先尝试从视频页提取真实发布时间，提升命中率
        page_date = _extract_youtube_published_date(it.get("url", ""))
        if page_date:
            it["date"] = page_date
            it["date_inferred"] = False
        if not is_within_days(it.get("date"), VIDEO_MAX_AGE_DAYS):
            continue
        # YouTube 视频必须有可解析发布时间，防止用抓取时间冒充
        if it.get("date_inferred"):
            continue
        seen.add(k)
        dedup.append(_mark_social_item(it, platform="YouTube", is_video=True))

    tracker.record(source, dedup)
    return dedup


def fetch_bilibili():
    source = "B站"
    items = []

    items.extend(
        _fetch_google_news_site(
            "bilibili.com",
            source_name=source,
            extra_queries=SOCIAL_PRACTICAL_QUERIES + MODEL_INNOVATION_QUERIES,
            max_entries=8,
        )
    )

    if len(items) < 5:
        items.extend(
            _fetch_rsshub_keyword(
                route_template="bilibili/search/{keyword}",
                source_name=source,
                keywords=SOCIAL_PRACTICAL_QUERIES,
                max_entries=8,
            )
        )

    dedup = []
    seen = set()
    for it in items:
        k = it.get("url", "").rstrip("/")
        if not k or k in seen:
            continue
        page_date = _extract_bilibili_published_date(it.get("url", ""))
        if page_date:
            it["date"] = page_date
            it["date_inferred"] = False
        if not is_within_days(it.get("date"), VIDEO_MAX_AGE_DAYS):
            continue
        if it.get("date_inferred"):
            continue
        seen.add(k)
        dedup.append(_mark_social_item(it, platform="Bilibili", is_video=True))

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
        it["url"] = normalize_social_url(original_url)
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
        it["url"] = normalize_social_url(original_url)
        k = it.get("url", "").rstrip("/")
        if not k or k in seen:
            continue
        seen.add(k)
        dedup.append(_mark_social_item(it, platform="X", is_video=False))

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
    text = f"{title} {summary} {title_zh} {summary_zh}"

    score = 0
    if PRACTICAL_SIGNAL.search(text):
        score += 3
    if REUSABLE_SIGNAL.search(text):
        score += 2
    if INNOVATION_SIGNAL.search(text):
        score += 2
    if re.search(r"\b(skill|skills|agent|agentic|workflow|tutorial|how.to)\b|教程|实战|工作流|智能体", text, re.IGNORECASE):
        score += 2
    if re.search(r"音频|播客|podcast|voice|配音|ASR|TTS|DAW|VST|混音|母带|转写", text, re.IGNORECASE):
        score += 2
    if TECH_BOOST.search(text):
        score += 1
    if HOT_ENTITY.search(text):
        score += 1

    if LOW_VALUE_SIGNAL.search(text):
        score -= 3
    if FUNDING_POLICY_FILTER.search(text):
        score -= 2
    if ENTERPRISE_BIZ_FILTER.search(text):
        score -= 2

    # 社媒/视频来源如果没有实践信号，额外降权，减少“标题党”进入
    if (item.get("is_social") or SOCIAL_VIDEO_DOMAINS.search(url)) and not PRACTICAL_SIGNAL.search(text):
        score -= 2

    return score


def is_practical_candidate(item):
    """
    实用硬门槛：
    - 命中实践/复用信号，或
    - 命中创新信号且同时出现模型/应用信号
    """
    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('title_zh', '')} {item.get('summary_zh', '')}"
    practical_hit = bool(PRACTICAL_SIGNAL.search(text))
    reusable_hit = bool(REUSABLE_SIGNAL.search(text))
    innovation_hit = bool(INNOVATION_SIGNAL.search(text))
    model_hit = bool(MODEL_SIGNAL.search(text))
    app_hit = bool(APPLICATION_SIGNAL.search(text))

    if practical_hit or reusable_hit:
        return True
    if innovation_hit and (model_hit or app_hit):
        return True
    return False

# ══════════════════════════════════════════════════════════════════════════════
# 质量筛选
# ══════════════════════════════════════════════════════════════════════════════

def quality_filter(items):
    filtered = []
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

        if is_github_url(url):
            continue
        if GITHUB_TITLE_FILTER.search(title):
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
                if (today - article_date).total_seconds() > OLD_NEWS_HOURS * 3600:
                    old_date_filtered_count += 1
                    continue
            else:
                # 日期格式无法解析时，回退为抓取时间并打标记
                item["date"] = item.get("fetched_at", _now_iso())
                item["date_inferred"] = True
                date_missing_filtered_count += 1
        except Exception:
            item["date"] = item.get("fetched_at", _now_iso())
            item["date_inferred"] = True
            date_missing_filtered_count += 1

        if FUNDING_POLICY_FILTER.search(text):
            funding_policy_count += 1
            if funding_policy_count > MAX_FUNDING_POLICY:
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
        if PRACTICAL_STRICT_ONLY:
            if not is_practical_candidate(item):
                hard_practical_filtered_count += 1
                continue
            dynamic_threshold = PRACTICAL_MIN_SCORE
            if item.get("is_social") or SOCIAL_VIDEO_DOMAINS.search(url):
                dynamic_threshold += 0
            if pscore < dynamic_threshold:
                practical_filtered_count += 1
                continue

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
    # 视频站（B站/YouTube）由来源抓取阶段执行 5 天内硬过滤

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
        heat -= 30
    if PRACTICE_BOOST.search(text):
        heat += 25
    if re.search(r"\b(skill|skills|agent|agentic|workflow|tutorial|how.to)\b|教程|实战|工作流|智能体", text, re.IGNORECASE):
        heat += 18
    if re.search(r"音频|播客|podcast|voice|配音|ASR|TTS|DAW|VST|混音|母带|转写", text, re.IGNORECASE):
        heat += 22
    if practical_score > 0:
        heat += practical_score * 12
    if item.get("is_social") and practical_score < PRACTICAL_MIN_SCORE:
        heat -= 40

    return heat

def deduplicate_and_rank(all_items):
    items = quality_filter(all_items)

    for item in items:
        item["heat_score"] = calculate_heat_score(item)
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

    return enforce_diversity(deduped)

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
                <div class="card-title">{title}</div>
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

    intl_cards = "\n".join(_build_card_html(item) for item in intl_items)
    domestic_cards = "\n".join(_build_card_html(item) for item in domestic_items)

    return HTML_TEMPLATE.format(
        date=date_str,
        intl_cards=intl_cards,
        domestic_cards=domestic_cards,
        count=len(items),
        intl_count=intl_count,
        domestic_count=domestic_count,
        source_count=source_count,
    )

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
                "content": f"<font color='indigo'>{title_zh}</font>",
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
            else:
                print(f"[WARN] Feishu response -> 群{i}: {result}")
        except Exception as e:
            print(f"[ERROR] Feishu push failed -> 群{i}: {e}")

def publish_to_pages(html_content, date_str):
    try:
        pages = PAGES_DIR
        (pages / "latest.html").write_text(html_content, encoding="utf-8")
        (pages / f"AI-m-OK-{date_str}.html").write_text(html_content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(pages), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"update: AI-m-OK {date_str}"],
            cwd=str(pages), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=str(pages), check=True, capture_output=True, timeout=30,
        )
        print(f"      Published: {PAGES_URL}/latest.html ✅")
    except Exception as e:
        print(f"[WARN] GitHub Pages push failed: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════

def main():
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"  🥕AI'm OK v3.3 | {today}")
    print(f"  多源聚合 · 实用导向筛选 · 视频字幕抽取 · 反幻觉校验 · 72h时效 · 隔日去重")
    print(f"{'='*60}\n")

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
    tw = fetch_twitter()
    xsrc = fetch_x()
    bz = fetch_bilibili()
    wb = fetch_weibo()

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
        yt + tw + xsrc + bz + wb +
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

     # ══════════════════════════════════════════════════════════════
    # ★ 新增 Phase F.5：本地 Web 审核（传入 --auto 参数可跳过）
    # ══════════════════════════════════════════════════════════════
    if "--auto" not in sys.argv and start_review_server is not None:
        print("\n🔍 [Phase F.5] 启动本地审核页面...")
        final = start_review_server(
            items=final,
            infer_tags_func=infer_tags,
            pick_emoji_func=pick_emoji,
            get_source_info_func=get_source_info,
            port=18088,
        )
        if not final:
            print("[INFO] 所有条目被过滤或用户取消，本次不推送。")
            return
        print(f"      审核后保留 {len(final)} 条，继续推送流程...")
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
    push_feishu(card)
    print(f"      飞书推送: Top {min(FEISHU_TOP_N, len(final))} 条 | 网页版: 全部 {len(final)} 条")

    # ── 保存本次推送记录，用于后续隔日去重 ──
    pushed_urls = {it["url"].rstrip("/") for it in final}
    save_history(pushed_urls)
    print(f"      已保存 {len(pushed_urls)} 条推送记录到历史文件，防止隔日重复推送。")

    intl_final = sum(1 for it in final if it.get("source_type") != "domestic")
    dom_final = sum(1 for it in final if it.get("source_type") == "domestic")
    print(f"\n{'='*60}")
    print(f"  ✅ Done! {len(final)} items ({intl_final} intl + {dom_final} domestic)")
    print(f"  📲 飞书推送: Top {min(FEISHU_TOP_N, len(final))} 条热点")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
