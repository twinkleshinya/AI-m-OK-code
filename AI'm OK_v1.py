"""
AI'm OK v2.6 — 每日 AI 资讯抓取、HTML 生成与飞书推送脚本
================================================================================
修复内容（v2.6 新增）：
  1. 禁止自动联想：强化摘要生成 Prompt，严禁编造/猜测原文未提及的信息，
     title_zh 必须忠实反映原始标题核心信息，不得替换为其他话题
  2. 产品类网站增强过滤：扩充产品官网域名黑名单（含 anthropic.com 非博客页面等），
     高热度产品页保留但强制排序至末尾
  3. 定时运行：新增 schedule 定时模式，支持每日 15:50 自动运行
修复内容（v2.5 新增）：
  1. PDF过滤：严格禁止输出 .pdf 类型的网址（如 CDN 托管论文）
  2. 飞书卡片：热点标题颜色从深红色(carmine)改为深蓝色(indigo)
  3. 飞书卡片：仅推送热度最高的10条热点新闻，其余内容只在网页版展示
修复内容（v2.4 新增）：
  1. 飞书卡片：热点标题使用 text_size="large"，比其他文字大两个号
  2. 飞书卡片：标题和摘要之间不再空开一行（拆分为独立元素紧凑排列）
  3. 飞书卡片：国际资讯和国内资讯分开两个部分展示，中间隔开
  4. 飞书卡片：来源行去掉国内外 emoji（🌐/🇨🇳），只显示来源名称
修复内容（v2.3.1 新增）：
  1. 新增 HARD_BLOCK_DOMAINS 硬封禁域名黑名单，完全禁止指定产品官网
  2. wawawriter.com 加入硬封禁黑名单
修复内容（v2.3 新增）：
  1. 摘要生成改为逐条调用：彻底消除批量处理导致的标题/摘要与新闻错位问题
  2. 飞书卡片标题加粗：使用 **加粗** markdown 格式，增强可读性
  3. 版本号升级至 v2.3
优化内容（v2.2 新增）：
  1. 模型升级：qwen2.5:7b → qwen3:14b
  2. 产品官网/Landing Page 过滤：减少产品类网站，除非热度特别高
优化内容（v2.1 新增）：
  1. 融资/政策类新闻限流：新增 FUNDING_POLICY_FILTER，单次最多保留 2 条
  2. 综合热度评分排序：新增 calculate_heat_score()，替代单一 HN score 排序
  3. 技术实践类内容加权：新增 PRACTICE_BOOST，优先展示技术突破/大模型/实际应用
  4. 飞书卡片移除统计信息头
  5. 新增国内源：新浪科技、今日头条、澎湃新闻
  6. 优化国际源：Ben's Bites → Wired，ScienceDaily → IEEE Spectrum
  7. 国内外平衡参数调整：国内占比 35%-55%
原有优化内容：
  - 数据源扩展：国际 9 源 + 国内 9 源，共 18 个数据源
  - 智能去重：URL去重 + 标题相似度去重（difflib）
  - 来源多样性约束：单源上限 3 条、最终至少 5 个不同来源
  - 国内外平衡控制
  - 质量筛选：过滤软文、旧闻、标题党、GitHub 链接（增强版）
  - 抓取状态追踪：自动检测成功/失败源数量，不足时触发补充搜索
  - 摘要生成优化：增强 Prompt，区分国内外新闻撰写风格
  - 来源展示名规范化 + 来源类型标识（🌐 国际 / 🇨🇳 国内）
  - GitHub 链接增强过滤：覆盖 gist/raw/pages 等全部 GitHub 域名
依赖：pip install feedparser requests schedule difflib(标准库)
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
import schedule  # v2.6 新增：定时运行依赖

# ══════════════════════════════════════════════════════════════════════════════
# 配置区域（建议敏感信息迁移至环境变量）
# ══════════════════════════════════════════════════════════════════════════════

FEISHU_WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/30bd0594-8318-4475-9f34-e0ed5a65de00",
)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAwesMzAFIU45qjxw0ISW92L-ufU4tFG78")
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3:14b"

OUTPUT_DIR = Path.home()
PAGES_DIR = Path.home() / "AI-m-OK"
PAGES_URL = "https://twinkleshinya.github.io/AI-m-OK"

# ── 数量与多样性约束 ──
MAX_ITEMS = 18
MIN_ITEMS = 12
HN_TOP_N = 50
MAX_PER_SOURCE = 3
MIN_SOURCES = 5
DOMESTIC_RATIO_MIN = 0.35
DOMESTIC_RATIO_MAX = 0.55
MIN_DOMESTIC_SUCCESS = 2
MIN_INTL_SUCCESS = 3
OLD_NEWS_DAYS = 7
MAX_FUNDING_POLICY = 2
PRODUCT_HEAT_THRESHOLD = 90
FEISHU_TOP_N = 10

# ── 定时运行配置（v2.6 新增） ──
SCHEDULE_TIME = "16:30"  # 每日定时运行时间

# ── 北京时区 ──
BEIJING_TZ = timezone(timedelta(hours=8))

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

# ── 产品官网域名黑名单（v2.6 增强：扩充更多产品官网域名） ──
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
    r"|gemini\.google\.com(?!/blog)"
    # ── v2.6 新增产品域名 ──
    r"|anthropic\.com(?!/blog|/research|/news|/index)"
    r"|openai\.com(?!/blog|/research|/news|/index)"
    r"|google\.com/(?!blog|research)[\w\-]+/?$"
    r"|stability\.ai(?!/blog|/research|/news)"
    r"|runwayml\.com(?!/blog)"
    r"|elevenlabs\.io(?!/blog)"
    r"|jasper\.ai(?!/blog)"
    r"|copy\.ai(?!/blog)"
    r"|writesonic\.com(?!/blog)"
    r"|descript\.com(?!/blog)"
    r"|synthesia\.io(?!/blog)"
    r"|huggingface\.co(?!/blog|/papers)"
    r"|replicate\.com(?!/blog)",
    re.IGNORECASE,
)

HARD_BLOCK_DOMAINS = re.compile(
    r"wawawriter\.com",
    re.IGNORECASE,
)

# ── 实践/技术类加分匹配器 ──
PRACTICE_BOOST = re.compile(
    r"tutorial|how.to|实战|教程|部署|fine.?tun|微调|训练|推理|inference"
    r"|benchmark|评测|对比|测评|实测|体验|上手|接入|集成|API"
    r"|应用|落地|案例|场景|实践|工具|框架|pipeline|workflow"
    r"|agent|智能体|RAG|function.call|tool.use|prompt.engineer"
    r"|技术突破|breakthrough|SOTA|刷新|超越|性能提升",
    re.IGNORECASE,
)

# ── 技术突破加分匹配器 ──
TECH_BOOST = re.compile(
    r"breakthrough|突破|首次|首发|全球首|benchmark|SOTA|超越|刷新|纪录"
    r"|发布|launch|release|推出|上线|开源|open.?source",
    re.IGNORECASE,
)

# ── 关键实体加分匹配器 ──
HOT_ENTITY = re.compile(
    r"OpenAI|Google|Meta|Apple|Microsoft|Nvidia|DeepSeek"
    r"|百度|阿里|腾讯|字节|华为|GPT-5|Claude|Gemini",
    re.IGNORECASE,
)

# ══════════════════════════════════════════════════════════════════════════════
# 来源注册表
# ══════════════════════════════════════════════════════════════════════════════

SOURCE_REGISTRY = {
    # ── 国际源 ──
    "TechCrunch":       {"type": "intl", "display": "TechCrunch",       "icon": "🌐"},
    "Hacker News":      {"type": "intl", "display": "Hacker News",      "icon": "🌐"},
    "TLDR.tech":        {"type": "intl", "display": "TLDR",             "icon": "🌐"},
    "The Verge":        {"type": "intl", "display": "The Verge",        "icon": "🌐"},
    "VentureBeat":      {"type": "intl", "display": "VentureBeat",      "icon": "🌐"},
    "Ars Technica":     {"type": "intl", "display": "Ars Technica",     "icon": "🌐"},
    "MIT Tech Review":  {"type": "intl", "display": "MIT Tech Review",  "icon": "🌐"},
    "IEEE Spectrum":    {"type": "intl", "display": "IEEE Spectrum",    "icon": "🌐"},
    "Wired":            {"type": "intl", "display": "Wired",            "icon": "🌐"},
    # ── 国内源 ──
    "机器之心":          {"type": "domestic", "display": "机器之心",       "icon": "🇨🇳"},
    "量子位":            {"type": "domestic", "display": "量子位",         "icon": "🇨🇳"},
    "36氪":              {"type": "domestic", "display": "36氪",           "icon": "🇨🇳"},
    "IT之家":            {"type": "domestic", "display": "IT之家",         "icon": "🇨🇳"},
    "新智元":            {"type": "domestic", "display": "新智元",         "icon": "🇨🇳"},
    "InfoQ":             {"type": "domestic", "display": "InfoQ",          "icon": "🇨🇳"},
    "新浪科技":          {"type": "domestic", "display": "新浪科技",       "icon": "🇨🇳"},
    "今日头条":          {"type": "domestic", "display": "今日头条",       "icon": "🇨🇳"},
    "澎湃新闻":          {"type": "domestic", "display": "澎湃新闻",       "icon": "🇨🇳"},
}

# ── 来源基础热度权重 ──
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
    """获取来源的规范化信息。"""
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
    """追踪每个数据源的抓取状态。"""

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
# 通用抓取工具函数
# ══════════════════════════════════════════════════════════════════════════════

def safe_request(url, timeout=15, headers=None):
    """带重试的安全 HTTP 请求。"""
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
    """统一判断是否为 GitHub 相关链接（增强版）。"""
    return bool(GITHUB_FILTER.search(url))


def parse_rss_feed(url, source_name, max_entries=20, ai_filter=False):
    """通用 RSS 解析函数。"""
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

            items.append({
                "title": title,
                "url": link,
                "summary": summary,
                "source": source_name,
                "source_type": get_source_info(source_name)["type"],
                "date": entry.get("published", ""),
                "score": 0,
            })
    except Exception as e:
        print(f"  [WARN] {source_name} RSS parse failed: {e}")
    return items


def scrape_links_from_page(url, source_name, link_pattern=None,
                           title_min_len=10, max_items=15, ai_filter=True):
    """通用网页爬取函数：提取页面中的标题+链接。"""
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
                if ai_filter:
                    if not (AI_KEYWORDS.search(title) or AI_KEYWORDS_ZH.search(title)):
                        continue
                seen.add(link_url)
                items.append({
                    "title": title,
                    "url": link_url,
                    "summary": f"via {source_name}",
                    "source": source_name,
                    "source_type": get_source_info(source_name)["type"],
                    "date": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d"),
                    "score": 0,
                })
                if len(items) >= max_items:
                    break
    except Exception as e:
        print(f"  [WARN] {source_name} scrape failed: {e}")
    return items


# ══════════════════════════════════════════════════════════════════════════════
# A. 聚合源抓取（优先执行）
# ══════════════════════════════════════════════════════════════════════════════

def fetch_tldr():
    """TLDR.tech AI — 核心国际聚合源。"""
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
    """解析 TLDR 详情页内容。"""
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
    """Hacker News API — 筛选 AI 相关高分帖子。"""
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
            url = story.get("url", f"https://news.ycombinator.com/item?id={sid}")
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
    """Wired AI — 优质国际科技媒体。"""
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
    """TechCrunch AI RSS feed。"""
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
    """The Verge AI RSS feed。"""
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
    items = [it for it in items if not is_github_url(it["url"])]
    tracker.record(source, items)
    return items


def fetch_venturebeat():
    """VentureBeat AI RSS feed。"""
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
    """Ars Technica AI RSS feed。"""
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
    """MIT Technology Review AI。"""
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
    """IEEE Spectrum AI — 面向工程师的技术媒体。"""
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
    """机器之心 — 国内 AI 领域最权威的专业媒体之一。"""
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
    """爬取机器之心首页文章列表。"""
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
                seen.add(url)
                items.append({
                    "title": title,
                    "url": url,
                    "summary": "via 机器之心",
                    "source": source,
                    "source_type": "domestic",
                    "date": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d"),
                    "score": 0,
                })
                if len(items) >= 15:
                    break
    except Exception as e:
        print(f"  [WARN] 机器之心 scrape failed: {e}")
    return items


def fetch_qbitai():
    """量子位 — 国内头部 AI 科技媒体。"""
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
    """36氪 AI 频道 — 国内领先的科技商业媒体。"""
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
    """IT之家 AI 频道 — 综合科技资讯媒体。"""
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
    """新智元 — 国内 AI 行业资讯媒体。"""
    source = "新智元"
    items = scrape_links_from_page(
        "https://www.aihub.cn/",
        source_name=source,
        title_min_len=8,
        max_items=10,
        ai_filter=False,
    )
    items = [it for it in items if not is_github_url(it["url"])]
    tracker.record(source, items)
    return items


def fetch_infoq():
    """InfoQ AI前线 — 面向开发者的技术媒体。"""
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
    """新浪科技 — 综合性科技新闻门户。"""
    source = "新浪科技"
    items = []
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
        items = scrape_links_from_page(
            "https://tech.sina.com.cn/ai/",
            source_name=source,
            title_min_len=8,
            max_items=12,
            ai_filter=False,
        )

    items = [it for it in items if not is_github_url(it["url"])]
    tracker.record(source, items)
    return items


def fetch_toutiao():
    """今日头条科技频道 — 大众科技资讯。"""
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
    """澎湃新闻科技频道 — 深度报道类媒体。"""
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


# ══════════════════════════════════════════════════════════════════════════════
# D. 补充搜索（当抓取源不足时启用）
# ══════════════════════════════════════════════════════════════════════════════

def supplementary_search_intl():
    """国际源补充：使用搜索 API 补充。"""
    print("  [INFO] 国际源不足，尝试补充搜索...")
    items = []
    return items


def supplementary_search_domestic():
    """国内源补充：使用搜索接口补充国内 AI 新闻。"""
    print("  [INFO] 国内源不足，尝试补充搜索...")
    items = []
    return items


# ══════════════════════════════════════════════════════════════════════════════
# 标题相似度去重
# ══════════════════════════════════════════════════════════════════════════════

def title_similarity(t1, t2):
    """计算两个标题的相似度 (0.0 ~ 1.0)。"""
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
    """检查新标题是否与已有标题重复。"""
    for existing in existing_titles:
        if title_similarity(new_title, existing) > threshold:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# 质量筛选（含融资/政策限流 + 产品官网标记 + 硬封禁）
# ══════════════════════════════════════════════════════════════════════════════

def quality_filter(items):
    """过滤低质量内容（含融资/政策限流 + 产品官网标记 + 硬封禁域名过滤）。"""
    filtered = []
    today = datetime.now(BEIJING_TZ)
    funding_policy_count = 0

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
        if len(title) < 8:
            continue
        if PAPER_FILTER.search(title) or PAPER_FILTER.search(url):
            continue
        if FALSE_POSITIVE_FILTER.search(title):
            continue
        if item.get("date"):
            try:
                date_str = item["date"]
                if isinstance(date_str, str) and re.match(r"\d{4}-\d{2}-\d{2}", date_str):
                    article_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                    article_date = article_date.replace(tzinfo=BEIJING_TZ)
                    if (today - article_date).days > OLD_NEWS_DAYS:
                        continue
            except (ValueError, TypeError):
                pass

        if FUNDING_POLICY_FILTER.search(text):
            funding_policy_count += 1
            if funding_policy_count > MAX_FUNDING_POLICY:
                continue

        if PRODUCT_LANDING_FILTER.search(url) or PRODUCT_SITE_DOMAINS.search(url):
            item["_is_product_landing"] = True

        filtered.append(item)
    return filtered


# ══════════════════════════════════════════════════════════════════════════════
# 综合热度评分
# ══════════════════════════════════════════════════════════════════════════════

def calculate_heat_score(item):
    """计算综合热度评分。"""
    base_score = item.get("score", 0)
    source = item.get("source", "")
    title = item.get("title", "")
    summary = item.get("summary", "")
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

    return heat


# ══════════════════════════════════════════════════════════════════════════════
# 去重与排序（v2.6 修改：产品类高热度条目保留但强制排到末尾）
# ══════════════════════════════════════════════════════════════════════════════

def deduplicate_and_rank(all_items):
    """智能去重与排序。"""
    items = quality_filter(all_items)

    for item in items:
        item["heat_score"] = calculate_heat_score(item)

    # v2.6 修改：产品类条目不再直接过滤，而是分离出来
    non_product_items = [
        it for it in items
        if not it.get("_is_product_landing")
    ]
    product_high_heat_items = [
        it for it in items
        if it.get("_is_product_landing")
        and it.get("heat_score", 0) >= PRODUCT_HEAT_THRESHOLD
    ]

    seen_urls = set()
    seen_titles = []
    deduped = []

    # 先处理非产品类条目（按热度排序）
    non_product_items.sort(key=lambda x: x.get("heat_score", 0), reverse=True)
    for item in non_product_items:
        url = item["url"].rstrip("/")
        title = item.get("title", "")
        if not title:
            continue
        if url in seen_urls:
            continue
        if is_duplicate_title(title, seen_titles):
            continue
        seen_urls.add(url)
        seen_titles.append(title)
        deduped.append(item)

    # v2.6 新增：产品类高热度条目去重后追加到末尾
    product_deduped = []
    product_high_heat_items.sort(key=lambda x: x.get("heat_score", 0), reverse=True)
    for item in product_high_heat_items:
        url = item["url"].rstrip("/")
        title = item.get("title", "")
        if not title:
            continue
        if url in seen_urls:
            continue
        if is_duplicate_title(title, seen_titles):
            continue
        seen_urls.add(url)
        seen_titles.append(title)
        product_deduped.append(item)

    # 合并：非产品条目在前，产品条目在末尾
    all_deduped = deduped + product_deduped

    return enforce_diversity(all_deduped)


def enforce_diversity(items):
    """执行来源多样性约束（v2.6：保持产品类条目在末尾的顺序）。"""
    # 先分离产品类条目，保证它们最终在末尾
    product_items_tail = [it for it in items if it.get("_is_product_landing")]
    normal_items = [it for it in items if not it.get("_is_product_landing")]

    source_groups = {}
    for item in normal_items:
        src = item["source"]
        source_groups.setdefault(src, []).append(item)

    capped_normal = []
    for src, src_items in source_groups.items():
        capped_normal.extend(src_items[:MAX_PER_SOURCE])

    domestic_items = [it for it in capped_normal if it.get("source_type") == "domestic"]
    intl_items = [it for it in capped_normal if it.get("source_type") != "domestic"]

    total_target = min(MAX_ITEMS, len(capped_normal) + len(product_items_tail))
    total_target = max(total_target, MIN_ITEMS)
    total_target = min(total_target, len(capped_normal) + len(product_items_tail))

    # 为产品条目预留位置
    product_slots = min(len(product_items_tail), max(1, total_target // 6))
    normal_target = total_target - product_slots

    domestic_min = max(1, int(normal_target * DOMESTIC_RATIO_MIN))
    domestic_max = int(normal_target * DOMESTIC_RATIO_MAX)

    domestic_count = min(len(domestic_items), domestic_max)
    domestic_count = max(domestic_count, min(domestic_min, len(domestic_items)))

    intl_count = normal_target - domestic_count
    intl_count = min(intl_count, len(intl_items))

    if intl_count < normal_target - domestic_count:
        domestic_count = min(len(domestic_items), normal_target - intl_count)

    final_normal = intl_items[:intl_count] + domestic_items[:domestic_count]

    unique_sources = set(it["source"] for it in final_normal)
    if len(unique_sources) < MIN_SOURCES and len(capped_normal) > len(final_normal):
        remaining = [it for it in capped_normal if it not in final_normal]
        remaining_sources = set(it["source"] for it in remaining) - unique_sources
        for src in remaining_sources:
            if len(final_normal) >= normal_target:
                break
            for it in remaining:
                if it["source"] == src:
                    final_normal.append(it)
                    unique_sources.add(src)
                    break
            if len(unique_sources) >= MIN_SOURCES:
                break

    # 非产品条目按热度排序
    final_normal.sort(key=lambda x: x.get("heat_score", 0), reverse=True)

    # v2.6：产品类条目追加到末尾
    final = final_normal + product_items_tail[:product_slots]

    return final[:MAX_ITEMS]


# ══════════════════════════════════════════════════════════════════════════════
# Ollama 生成中文标题与摘要（v2.6 核心修复：禁止自动联想）
# ══════════════════════════════════════════════════════════════════════════════

def _generate_single_summary(item, index, total):
    """
    v2.6 修复：为单条资讯生成中文标题和摘要。
    强化 Prompt 禁止自动联想，严格要求忠实于原始标题和摘要。
    """
    src_info = get_source_info(item["source"])
    src_tag = "国内" if src_info["type"] == "domestic" else "国际"

    # ═══════════════════════════════════════════════════════════
    # v2.6 核心修改：强化 Prompt，禁止自动联想
    # ═══════════════════════════════════════════════════════════
    prompt = f"""你是资深AI行业记者，精通中英文。将以下资讯转化为中文精华版。

⚠️⚠️⚠️ 最高优先级规则 — 禁止自动联想 ⚠️⚠️⚠️
1. 你只能基于下方提供的【原始标题】和【原始摘要】进行改写，严禁编造、猜测、补充任何原文中未明确提及的信息
2. title_zh 必须忠实反映原始标题的核心信息，不得替换为其他话题或关联话题
3. summary_zh 只能基于原始摘要中已有的事实进行改写，不得自行脑补数据、人物、事件
4. 如果原始信息不足以生成高质量摘要，就简短改写原文即可，绝对不要编造内容来填充字数
5. 如果原始标题是英文，直接翻译其含义即可，不要替换成你认为"更好"的中文话题

其他规则：
1. 先判断是否真正与AI/人工智能直接相关
2. 国际新闻：翻译原标题核心含义，可适当补充"对中国市场/开发者的影响"（但不得编造）
3. 国内新闻：突出事件的行业影响和背景，避免公关稿式堆砌
4. 融资类、政策法规类新闻，除非涉及重大事件（如超10亿美元融资或全球性法规），否则标记 ai_related: false
5. 产品发布要说明核心功能（仅限原文已提及的功能）
6. 技术突破要说明实际意义（仅限原文已提及的意义）

返回以下JSON（只输出JSON，不要输出其他任何内容）：
{{"ai_related":true,"emoji":"🤖","title_zh":"中文标题15-25字，必须忠实于原始标题含义","summary_zh":"中文摘要50-100字，只能基于原始摘要改写","category":"分类标签"}}

分类标签从以下选择：技术突破/融资/产品发布/政策法规/行业变动/应用落地/开源/研究

注意：title_zh和summary_zh都必须是中文，绝对不能是英文！

【原始标题】：{item['title']}
【原始摘要】：{item['summary']}
【来源】：{item['source']}({src_tag})
【原始URL】：{item['url']}"""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.3},
        }, timeout=120)
        text = resp.json()["message"]["content"].strip()

        # 提取 JSON 对象（单个 {} 而非数组）
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if not json_match:
            # 尝试匹配嵌套的 JSON
            json_match = re.search(r'\{.*\}', text, re.DOTALL)

        if json_match:
            r = json.loads(json_match.group())

            if not r.get("ai_related", True):
                item["_remove"] = True
                print(f"      [{index}/{total}] 🚫 非AI相关，已过滤: {item['title'][:40]}")
                return

            item["title_zh"] = r.get("title_zh", item["title"])
            item["summary_zh"] = r.get("summary_zh", item["summary"])
            item["emoji_override"] = r.get("emoji", "")
            item["category"] = r.get("category", "AI")
            print(f"      [{index}/{total}] ✅ {item['title_zh'][:40]}")
        else:
            print(f"      [{index}/{total}] ⚠️ JSON解析失败，使用原文: {item['title'][:40]}")
            item["title_zh"] = item["title"]
            item["summary_zh"] = item["summary"]
    except Exception as e:
        print(f"      [{index}/{total}] ❌ Ollama调用失败: {e}")
        item["title_zh"] = item["title"]
        item["summary_zh"] = item["summary"]


def generate_chinese_summaries(items):
    """
    v2.3 核心修复：逐条调用 Ollama 生成中文标题和摘要。
    v2.6 强化：Prompt 中禁止自动联想。
    """
    total = len(items)
    print(f"      逐条调用 Ollama ({OLLAMA_MODEL})，共 {total} 条...")

    for i, item in enumerate(items, 1):
        _generate_single_summary(item, i, total)
        # 适当间隔，避免 Ollama 过载
        if i < total:
            time.sleep(0.5)

    # 移除被标记为非AI相关的条目
    filtered_count = sum(1 for it in items if it.get("_remove"))
    items = [it for it in items if not it.get("_remove")]
    print(f"      完成: {total} 条已处理, {filtered_count} 条被过滤为非AI相关")

    # 兜底：确保所有条目都有中文字段
    for item in items:
        if "title_zh" not in item:
            item["title_zh"] = item["title"]
        if "summary_zh" not in item:
            item["summary_zh"] = item["summary"]
        if "category" not in item:
            item["category"] = "AI"

    return items


def _fallback_titles(items):
    """Ollama 失败时的降级方案：直接用原文。"""
    for item in items:
        item["title_zh"] = item["title"]
        item["summary_zh"] = item["summary"]


# ══════════════════════════════════════════════════════════════════════════════
# 标签推断（增强版）
# ══════════════════════════════════════════════════════════════════════════════

TAG_RULES = [
    (re.compile(r"llm|gpt|claude|gemini|model|mistral|anthropic|openai|大模型|qwen|glm|deepseek|baichuan", re.I),
     "大模型", "tag-llm", "\U0001f916"),
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
     "国产AI", "tag-domestic", "\U0001f1e8\U0001f1f3"),
]

SOURCE_EMOJI = {
    "Hacker News": "\U0001f525",
    "TechCrunch": "\U0001f4f0",
    "TLDR.tech": "\U0001f4e8",
    "The Verge": "\U0001f4f1",
    "VentureBeat": "\U0001f4ca",
    "Ars Technica": "\U0001f4bb",
    "MIT Tech Review": "\U0001f393",
    "IEEE Spectrum": "\U0001f4e1",
    "Wired": "\U0001f310",
    "机器之心": "\U0001f916",
    "量子位": "\u26a1",
    "36氪": "\U0001f4b9",
    "IT之家": "\U0001f4f1",
    "新智元": "\U0001f31f",
    "InfoQ": "\U0001f4bb",
    "新浪科技": "\U0001f4f0",
    "今日头条": "\U0001f4f1",
    "澎湃新闻": "\U0001f4e1",
}


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
    """优先用 Ollama 返回的 emoji，否则按标签规则推断。"""
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
                <span class="stat">🇨🇳 {domestic_count} 条国内</span>
                <span class="stat">{source_count} 个来源</span>
            </div>
        </div>
        <div class="cards-grid">
{cards}
        </div>
        <div class="footer">
            \U0001f955 由 AI'm OK v2.6 自动生成 | {date} | 国内外 {source_count} 源聚合
        </div>
    </div>
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


def generate_html(items, date_str):
    cards = []
    intl_count = sum(1 for it in items if it.get("source_type") != "domestic")
    domestic_count = sum(1 for it in items if it.get("source_type") == "domestic")
    source_count = len(set(it["source"] for it in items))

    for item in items:
        tags = infer_tags(item)
        tags_html = "".join(
            f'<span class="tag {css}">{escape(label)}</span>' for label, css, emoji in tags
        )
        src_info = get_source_info(item["source"])
        badge_class = "badge-domestic" if src_info["type"] == "domestic" else "badge-intl"
        source_icon = src_info["icon"]

        cards.append(
            CARD_TEMPLATE.format(
                url=escape(item["url"]),
                tags_html=tags_html,
                emoji=pick_emoji(item),
                title=escape(item.get("title_zh", item["title"])),
                summary=escape(item.get("summary_zh", item["summary"])),
                source_display=escape(src_info["display"]),
                badge_class=badge_class,
                source_icon=source_icon,
            )
        )
    return HTML_TEMPLATE.format(
        date=date_str,
        cards="\n".join(cards),
        count=len(items),
        intl_count=intl_count,
        domestic_count=domestic_count,
        source_count=source_count,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 飞书推送（v2.5 修改：标题深蓝色 + 仅推送Top10 + 网页版引导）
# ══════════════════════════════════════════════════════════════════════════════

def build_feishu_card(items, date_str):
    feishu_items = sorted(items, key=lambda x: x.get("heat_score", 0), reverse=True)[:FEISHU_TOP_N]

    total_count = len(items)
    feishu_count = len(feishu_items)

    intl_count = sum(1 for it in feishu_items if it.get("source_type") != "domestic")
    domestic_count = sum(1 for it in feishu_items if it.get("source_type") == "domestic")
    source_count = len(set(it["source"] for it in feishu_items))

    intl_items = [it for it in feishu_items if it.get("source_type") != "domestic"]
    domestic_items = [it for it in feishu_items if it.get("source_type") == "domestic"]

    elements = []

    def _append_news_items(news_items):
        """将一组新闻条目添加到 elements 中。"""
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
            "content": "<font color='orange'>**国际资讯**</font>",
            "text_size": "heading",
        })
        elements.append({"tag": "hr"})
        _append_news_items(intl_items)

    if intl_items and domestic_items:
        elements.append({"tag": "hr"})

    if domestic_items:
        elements.append({
            "tag": "markdown",
            "content": "<font color='orange'>**国内资讯**</font>",
            "text_size": "heading",
        })
        elements.append({"tag": "hr"})
        _append_news_items(domestic_items)

    elements.append({"tag": "hr"})

    if total_count > feishu_count:
        elements.append({
            "tag": "markdown",
            "content": f"<font color='grey'>📌 以上为今日热度最高的 {feishu_count} 条精选，完整 {total_count} 条资讯请查看网页版 👇</font>",
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
            "content": f"\U0001f955 由 AI'm OK 自动生成 | {date_str} | {source_count}源聚合 | 飞书精选Top{feishu_count}",
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
    try:
        resp = requests.post(
            FEISHU_WEBHOOK,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        result = resp.json()
        if result.get("StatusCode") == 0 or result.get("code") == 0:
            print("[OK] Feishu push succeeded ✅")
        else:
            print(f"[WARN] Feishu response: {result}")
    except Exception as e:
        print(f"[ERROR] Feishu push failed: {e}")


def publish_to_pages(html_content, date_str):
    """将 HTML 推送到 GitHub Pages 仓库。"""
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
    print(f"  🥕AI'm OK v2.6 | {today}")
    print(f"  多源聚合 · 逐条摘要 · 禁止联想 · 热度排序 · 技术优先 · 产品末排 · 定时运行")
    print(f"{'='*60}\n")

    # ═══════════════════════════════════════════════════════════
    # Phase A: 聚合源抓取（优先执行）
    # ═══════════════════════════════════════════════════════════
    print("📡 [Phase A] 聚合源抓取...")

    print("  [A1] TLDR.tech AI (核心国际聚合源)...")
    tldr = fetch_tldr()
    print(f"       → {len(tldr)} items")

    print("  [A2] Hacker News...")
    hn = fetch_hackernews()
    print(f"       → {len(hn)} items")

    print("  [A3] Wired AI...")
    wired = fetch_wired_ai()
    print(f"       → {len(wired)} items")

    # ═══════════════════════════════════════════════════════════
    # Phase B: 国际权威媒体抓取
    # ═══════════════════════════════════════════════════════════
    print("\n📰 [Phase B] 国际权威媒体抓取...")

    print("  [B1] TechCrunch AI...")
    tc = fetch_techcrunch()
    print(f"       → {len(tc)} items")

    print("  [B2] The Verge AI...")
    tv = fetch_theverge()
    print(f"       → {len(tv)} items")

    print("  [B3] Ars Technica AI...")
    ars = fetch_arstechnica()
    print(f"       → {len(ars)} items")

    print("  [B4] VentureBeat AI...")
    vb = fetch_venturebeat()
    print(f"       → {len(vb)} items")

    print("  [B5] MIT Tech Review AI...")
    mit = fetch_mit_tech_review()
    print(f"       → {len(mit)} items")

    print("  [B6] IEEE Spectrum AI...")
    ieee = fetch_ieee_spectrum()
    print(f"       → {len(ieee)} items")

    # ═══════════════════════════════════════════════════════════
    # Phase C: 国内权威媒体抓取
    # ═══════════════════════════════════════════════════════════
    print("\n🇨🇳 [Phase C] 国内权威媒体抓取...")

    print("  [C1] 机器之心...")
    jqzx = fetch_jiqizhixin()
    print(f"       → {len(jqzx)} items")

    print("  [C2] 量子位...")
    qb = fetch_qbitai()
    print(f"       → {len(qb)} items")

    print("  [C3] 36氪 AI频道...")
    kr = fetch_36kr()
    print(f"       → {len(kr)} items")

    print("  [C4] IT之家 AI频道...")
    ith = fetch_ithome()
    print(f"       → {len(ith)} items")

    print("  [C5] 新智元...")
    xzy = fetch_xinzhiyuan()
    print(f"       → {len(xzy)} items")

    print("  [C6] InfoQ AI前线...")
    iq = fetch_infoq()
    print(f"       → {len(iq)} items")

    print("  [C7] 新浪科技...")
    sina = fetch_sina_tech()
    print(f"       → {len(sina)} items")

    print("  [C8] 今日头条科技...")
    tt = fetch_toutiao()
    print(f"       → {len(tt)} items")

    print("  [C9] 澎湃新闻科技...")
    pp = fetch_thepaper()
    print(f"       → {len(pp)} items")

    # ═══════════════════════════════════════════════════════════
    # Phase D: 抓取状态检查 + 补充搜索
    # ═══════════════════════════════════════════════════════════
    print("\n📊 [Phase D] 抓取状态检查...")
    tracker.print_report()

    supp_intl = []
    supp_domestic = []
    if tracker.intl_success_count < MIN_INTL_SUCCESS:
        supp_intl = supplementary_search_intl()
        print(f"       补充国际搜索: {len(supp_intl)} items")
    if tracker.domestic_success_count < MIN_DOMESTIC_SUCCESS:
        supp_domestic = supplementary_search_domestic()
        print(f"       补充国内搜索: {len(supp_domestic)} items")

    # ═══════════════════════════════════════════════════════════
    # Phase E: 合并、去重、排序
    # ═══════════════════════════════════════════════════════════
    print("\n🔄 [Phase E] 合并去重排序（热度排序 + 产品末排 + 硬封禁 + PDF过滤）...")
    all_items = (
        tldr + hn + wired +
        tc + tv + ars + vb + mit + ieee +
        jqzx + qb + kr + ith + xzy + iq +
        sina + tt + pp +
        supp_intl + supp_domestic
    )
    print(f"      Total raw: {len(all_items)}")

    final = deduplicate_and_rank(all_items)
    print(f"      After dedup + diversity + heat sort + product tail + hard block + PDF filter: {len(final)}")

    # 打印来源分布
    source_dist = {}
    for it in final:
        src = it["source"]
        source_dist[src] = source_dist.get(src, 0) + 1
    print("      来源分布:")
    for src, cnt in sorted(source_dist.items(), key=lambda x: -x[1]):
        icon = get_source_info(src)["icon"]
        print(f"        {icon} {src}: {cnt}")

    # v2.6：打印产品类条目位置信息
    product_in_final = [it for it in final if it.get("_is_product_landing")]
    if product_in_final:
        print(f"      📦 产品类条目（已排至末尾）: {len(product_in_final)} 条")
        for it in product_in_final:
            print(f"        → [heat={it.get('heat_score', 0)}] {it['title'][:50]}")

    print("      热度 Top 5:")
    for i, item in enumerate(final[:5], 1):
        heat = item.get("heat_score", 0)
        print(f"        {i}. [heat={heat}] {item['title'][:50]}")

    if not final:
        print("[ERROR] No items fetched. Check network. ❌")
        return

    # ═══════════════════════════════════════════════════════════
    # Phase F: 生成中文摘要（v2.6：禁止自动联想）
    # ═══════════════════════════════════════════════════════════
    print(f"\n✍️  [Phase F] Generating Chinese summaries (逐条模式 + 禁止联想)...")
    final = generate_chinese_summaries(final)

    # ═══════════════════════════════════════════════════════════
    # Phase G: 生成 HTML（网页版展示全部内容）
    # ═══════════════════════════════════════════════════════════
    html = generate_html(final, today)
    output_path = OUTPUT_DIR / f"AI-m-OK-{today}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"\n📄 [Phase G] HTML saved: {output_path}")

    # ═══════════════════════════════════════════════════════════
    # Phase H: 发布（飞书仅推送Top10，网页版推送全部）
    # ═══════════════════════════════════════════════════════════
    print("\n🚀 [Phase H] Publishing...")
    publish_to_pages(html, today)

    card = build_feishu_card(final, today)
    push_feishu(card)
    print(f"      飞书推送: Top {min(FEISHU_TOP_N, len(final))} 条 | 网页版: 全部 {len(final)} 条")

    # ═══════════════════════════════════════════════════════════
    # 完成摘要
    # ═══════════════════════════════════════════════════════════
    intl_final = sum(1 for it in final if it.get("source_type") != "domestic")
    dom_final = sum(1 for it in final if it.get("source_type") == "domestic")
    print(f"\n{'='*60}")
    print(f"  ✅ Done! {len(final)} items ({intl_final} intl + {dom_final} domestic)")
    print(f"  📡 Sources: {len(source_dist)}")
    print(f"  📲 飞书推送: Top {min(FEISHU_TOP_N, len(final))} 条热点")
    print(f"  🌐 网页版: 全部 {len(final)} 条资讯")
    print(f"{'='*60}")

    print(f"\n  Top 5 Stories (by heat score):")
    for i, item in enumerate(final[:5], 1):
        src_info = get_source_info(item["source"])
        title = item.get("title_zh", item["title"])
        heat = item.get("heat_score", 0)
        print(f"    {i}. {src_info['icon']} [{src_info['display']}] [🔥{heat}] {title}")

    print(f"\n  🥕AI'm OK v2.6 — All done!\n")


# ══════════════════════════════════════════════════════════════════════════════
# v2.6 新增：定时运行入口
# ══════════════════════════════════════════════════════════════════════════════

def run_scheduled():
    """定时任务模式：每日 15:50 自动执行 main()。"""
    print(f"\n{'='*60}")
    print(f"  ⏰ AI'm OK v2.6 — 定时模式已启动")
    print(f"  📌 每日执行时间: {SCHEDULE_TIME}")
    print(f"  📌 当前时间: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📌 按 Ctrl+C 停止")
    print(f"{'='*60}\n")

    schedule.every().day.at(SCHEDULE_TIME).do(_scheduled_task)

    while True:
        schedule.run_pending()
        time.sleep(30)


def _scheduled_task():
    """定时任务包装器，捕获异常防止定时器中断。"""
    print(f"\n⏰ [{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}] 定时任务触发，开始执行...")
    try:
        # 重置 tracker 状态（每次运行需清空）
        global tracker
        tracker = SourceTracker()
        main()
    except Exception as e:
        print(f"[ERROR] 定时任务执行异常: {e}")
        import traceback
        traceback.print_exc()
    print(f"⏰ 下次执行时间: 明日 {SCHEDULE_TIME}\n")


if __name__ == "__main__":
    if "--schedule" in sys.argv:
        # 定时模式：常驻运行，每日 15:50 自动执行
        run_scheduled()
    else:
        # 立即执行模式：运行一次后退出
        main()
