"""
AI-Scream v2.0 — 每日 AI 资讯抓取、HTML 生成与飞书推送脚本（优化版）
================================================================================
优化内容：
  1. 数据源扩展：国际 9 源 + 国内 6 源，共 15 个数据源
  2. 智能去重：URL去重 + 标题相似度去重（difflib）
  3. 来源多样性约束：单源上限 3 条、最终至少 5 个不同来源
  4. 国内外平衡控制：国内源占比 30%-50%
  5. 质量筛选：过滤软文、旧闻、标题党、GitHub 链接
  6. 抓取状态追踪：自动检测成功/失败源数量，不足时触发补充搜索
  7. 摘要生成优化：增强 Prompt，区分国内外新闻撰写风格
  8. 来源展示名规范化 + 来源类型标识（🌐 国际 / 🇨🇳 国内）
依赖：pip install feedparser requests difflib(标准库)
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

# ══════════════════════════════════════════════════════════════════════════════
# 配置区域（建议敏感信息迁移至环境变量）
# ══════════════════════════════════════════════════════════════════════════════

FEISHU_WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/30bd0594-8318-4475-9f34-e0ed5a65de00",
)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyAwesMzAFIU45qjxw0ISW92L-ufU4tFG78")
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b"

OUTPUT_DIR = Path.home()
PAGES_DIR = Path.home() / "ai-scream-pages"
PAGES_URL = "https://twinkleshinya.github.io/ai-scream-pages"

# ── 数量与多样性约束 ──
MAX_ITEMS = 18          # 最终输出上限
MIN_ITEMS = 12          # 最终输出下限
HN_TOP_N = 50           # HN 候选池扩大
MAX_PER_SOURCE = 3      # 单一来源上限
MIN_SOURCES = 5         # 最终结果最少来源数
DOMESTIC_RATIO_MIN = 0.30
DOMESTIC_RATIO_MAX = 0.50
MIN_DOMESTIC_SUCCESS = 2   # 国内源最少成功数
MIN_INTL_SUCCESS = 3       # 国际源最少成功数
OLD_NEWS_DAYS = 7          # 过滤 N 天前旧闻

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
    r"|mathematical.methods",
    re.IGNORECASE,
)

FALSE_POSITIVE_FILTER = re.compile(
    r"copilot.edited.an.ad|smart.eyeglasses|smart.glasses"
    r"|philly.courts|apple.watch",
    re.IGNORECASE,
)

GITHUB_FILTER = re.compile(r"github\.com/[\w-]+/[\w-]+", re.IGNORECASE)

SOFT_AD_FILTER = re.compile(
    r"sponsored|广告|PR稿|合作伙伴推广|赞助|soft.?article|advertorial",
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
    "ScienceDaily":     {"type": "intl", "display": "ScienceDaily",     "icon": "🌐"},
    "Ben's Bites":      {"type": "intl", "display": "Ben's Bites",      "icon": "🌐"},
    # ── 国内源 ──
    "机器之心":          {"type": "domestic", "display": "机器之心",       "icon": "🇨🇳"},
    "量子位":            {"type": "domestic", "display": "量子位",         "icon": "🇨🇳"},
    "36氪":              {"type": "domestic", "display": "36氪",           "icon": "🇨🇳"},
    "IT之家":            {"type": "domestic", "display": "IT之家",         "icon": "🇨🇳"},
    "新智元":            {"type": "domestic", "display": "新智元",         "icon": "🇨🇳"},
    "InfoQ":             {"type": "domestic", "display": "InfoQ",          "icon": "🇨🇳"},
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
        self.results = {}  # {source_name: {"status": "ok"/"fail", "count": N}}

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

            # 如果需要 AI 关键词过滤
            if ai_filter:
                text = f"{title} {summary}"
                if not (AI_KEYWORDS.search(text) or AI_KEYWORDS_ZH.search(text)):
                    continue

            # 过滤 GitHub 链接
            if GITHUB_FILTER.search(link):
                continue

            # 过滤论文
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

        # 提取所有 <a> 标签
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
            title = re.sub(r"\s+", " ", title)  # 规范化空白
            if (
                len(title) >= title_min_len
                and link_url not in seen
                and "advertiser" not in link_url.lower()
                and "sponsor" not in title.lower()
            ):
                # GitHub 链接过滤
                if GITHUB_FILTER.search(link_url):
                    continue
                # AI 关键词过滤
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
        # 方案1：直接用日期拼接 URL
        for delta in range(2):  # 尝试今天和昨天
            date_str = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
            try:
                resp = safe_request(f"https://tldr.tech/ai/{date_str}")
                if resp and resp.status_code == 200 and len(resp.text) > 1000:
                    items = _parse_tldr_page(resp.text, date_str, source)
                    if items:
                        break
            except Exception:
                continue

        # 方案2：从归档页获取最新链接
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
            and not GITHUB_FILTER.search(url)
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

            # AI 关键词匹配
            if not (AI_KEYWORDS.search(title) or AI_KEYWORDS.search(url)):
                continue
            # 低分过滤（仅保留 >30 分的帖子）
            if hn_score < 30:
                continue
            # 过滤论文
            if PAPER_FILTER.search(title) or PAPER_FILTER.search(url):
                continue
            # 过滤误匹配
            if FALSE_POSITIVE_FILTER.search(title):
                continue
            # 过滤 GitHub 仓库链接
            if GITHUB_FILTER.search(url):
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


def fetch_bensbites():
    """Ben's Bites — 备用国际聚合源。"""
    source = "Ben's Bites"
    items = scrape_links_from_page(
        "https://bensbites.com/",
        source_name=source,
        title_min_len=15,
        max_items=10,
        ai_filter=False,  # 该站全是 AI 内容
    )
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
        ai_filter=False,  # 该频道全是 AI
    )
    # 过滤 GitHub 链接
    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
    tracker.record(source, items)
    return items


def fetch_theverge():
    """The Verge AI RSS feed。"""
    source = "The Verge"
    # 尝试 RSS
    items = parse_rss_feed(
        "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        source_name=source,
        max_entries=15,
        ai_filter=False,
    )
    # RSS 失败则爬取网页
    if not items:
        items = scrape_links_from_page(
            "https://www.theverge.com/ai-artificial-intelligence",
            source_name=source,
            max_items=10,
            ai_filter=False,
        )
    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
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
    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
    tracker.record(source, items)
    return items


def fetch_arstechnica():
    """Ars Technica AI RSS feed。"""
    source = "Ars Technica"
    items = parse_rss_feed(
        "https://feeds.arstechnica.com/arstechnica/technology-lab",
        source_name=source,
        max_entries=20,
        ai_filter=True,  # 该 feed 含非 AI 内容，需过滤
    )
    if not items:
        items = scrape_links_from_page(
            "https://arstechnica.com/ai/",
            source_name=source,
            max_items=10,
            ai_filter=False,
        )
    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
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
    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
    tracker.record(source, items)
    return items


def fetch_sciencedaily():
    """ScienceDaily AI RSS feed。"""
    source = "ScienceDaily"
    items = parse_rss_feed(
        "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
        source_name=source,
        max_entries=15,
        ai_filter=False,
    )
    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
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
        # 尝试 RSS
        items = parse_rss_feed(
            "https://www.jiqizhixin.com/rss",
            source_name=source,
            max_entries=20,
            ai_filter=False,
        )
    except Exception:
        pass

    # RSS 失败则爬取首页
    if not items:
        items = _scrape_jiqizhixin()

    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
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
        # 匹配文章链接：/articles/xxx 或 /dailies/xxx
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
        # 尝试 RSS
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
            ai_filter=False,  # 该站全是 AI 内容
        )

    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
    tracker.record(source, items)
    return items


def fetch_36kr():
    """36氪 AI 频道 — 国内领先的科技商业媒体。"""
    source = "36氪"
    items = []
    # 尝试多个 URL
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

    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
    tracker.record(source, items)
    return items


def fetch_ithome():
    """IT之家 AI 频道 — 综合科技资讯媒体。"""
    source = "IT之家"
    items = []
    try:
        # 尝试 RSS
        items = parse_rss_feed(
            "https://www.ithome.com/rss/",
            source_name=source,
            max_entries=30,
            ai_filter=True,  # 该站内容广泛，需 AI 过滤
        )
    except Exception:
        pass

    if not items:
        items = scrape_links_from_page(
            "https://www.ithome.com/tag/AI/",
            source_name=source,
            title_min_len=8,
            max_items=12,
            ai_filter=False,  # AI 标签页已筛选
        )

    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
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
    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
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
    items = [it for it in items if not GITHUB_FILTER.search(it["url"])]
    tracker.record(source, items)
    return items


# ══════════════════════════════════════════════════════════════════════════════
# D. 补充搜索（当抓取源不足时启用，需自行接入搜索 API）
# ══════════════════════════════════════════════════════════════════════════════

def supplementary_search_intl():
    """国际源补充：使用 Gemini 搜索 API 或其他搜索接口。"""
    print("  [INFO] 国际源不足，尝试补充搜索...")
    items = []
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    # 如果有搜索 API，在此调用
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
    # 归一化：小写、去标点、去多余空格
    def normalize(t):
        t = t.lower().strip()
        t = re.sub(r"[^\w\s\u4e00-\u9fff]", "", t)  # 保留中英文和空格
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
# 质量筛选
# ══════════════════════════════════════════════════════════════════════════════

def quality_filter(items):
    """过滤低质量内容。"""
    filtered = []
    today = datetime.now(BEIJING_TZ)
    for item in items:
        title = item.get("title", "")
        url = item.get("url", "")
        summary = item.get("summary", "")
        text = f"{title} {summary}"

        # 过滤 GitHub 仓库链接
        if GITHUB_FILTER.search(url):
            continue
        # 过滤软文广告
        if SOFT_AD_FILTER.search(text):
            continue
        # 过滤标题太短的内容（可能是标题党或导航链接）
        if len(title) < 8:
            continue
        # 过滤论文
        if PAPER_FILTER.search(title) or PAPER_FILTER.search(url):
            continue
        # 过滤误匹配
        if FALSE_POSITIVE_FILTER.search(title):
            continue
        # 过滤旧闻（如果有日期信息）
        if item.get("date"):
            try:
                date_str = item["date"]
                if isinstance(date_str, str) and re.match(r"\d{4}-\d{2}-\d{2}", date_str):
                    article_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                    article_date = article_date.replace(tzinfo=BEIJING_TZ)
                    if (today - article_date).days > OLD_NEWS_DAYS:
                        continue
            except (ValueError, TypeError):
                pass  # 日期解析失败，不过滤

        filtered.append(item)
    return filtered


# ══════════════════════════════════════════════════════════════════════════════
# 去重与排序（含多样性约束）
# ══════════════════════════════════════════════════════════════════════════════

def deduplicate_and_rank(all_items):
    """
    智能去重与排序：
      1. URL 去重
      2. 标题相似度去重（阈值 0.65）
      3. HN 高分优先
      4. 来源多样性约束（单源上限 3，最终至少 5 个来源）
      5. 国内外平衡（国内占 30%-50%）
    """
    # 先做质量过滤
    items = quality_filter(all_items)

    # ── 第一轮：URL 去重 + 标题相似度去重 ──
    seen_urls = set()
    seen_titles = []
    deduped = []

    # 排序：HN 高分优先 → 其他按来源顺序
    items.sort(key=lambda x: x.get("score", 0), reverse=True)

    for item in items:
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

    # ── 第二轮：来源多样性约束 ──
    return enforce_diversity(deduped)


def enforce_diversity(items):
    """
    执行来源多样性约束：
      - 单一来源最多 MAX_PER_SOURCE 条
      - 最终至少 MIN_SOURCES 个不同来源
      - 国内源占比 DOMESTIC_RATIO_MIN ~ DOMESTIC_RATIO_MAX
    """
    # 按来源分组
    source_groups = {}
    for item in items:
        src = item["source"]
        source_groups.setdefault(src, []).append(item)

    # 第一轮：每个来源最多取 MAX_PER_SOURCE 条（取最高分/最前面的）
    capped = []
    for src, src_items in source_groups.items():
        capped.extend(src_items[:MAX_PER_SOURCE])

    # 分为国内和国际两组
    domestic_items = [it for it in capped if it.get("source_type") == "domestic"]
    intl_items = [it for it in capped if it.get("source_type") != "domestic"]

    # 计算目标数量
    total_target = min(MAX_ITEMS, len(capped))
    total_target = max(total_target, MIN_ITEMS)
    total_target = min(total_target, len(capped))

    domestic_min = max(1, int(total_target * DOMESTIC_RATIO_MIN))
    domestic_max = int(total_target * DOMESTIC_RATIO_MAX)

    # 确定国内条数
    domestic_count = min(len(domestic_items), domestic_max)
    domestic_count = max(domestic_count, min(domestic_min, len(domestic_items)))

    intl_count = total_target - domestic_count
    intl_count = min(intl_count, len(intl_items))

    # 如果国际条数不够，补充国内
    if intl_count < total_target - domestic_count:
        domestic_count = min(len(domestic_items), total_target - intl_count)

    # 最终组合
    final = intl_items[:intl_count] + domestic_items[:domestic_count]

    # 检查来源数
    unique_sources = set(it["source"] for it in final)
    if len(unique_sources) < MIN_SOURCES and len(capped) > len(final):
        # 从未选中的来源中补充
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

    return final[:MAX_ITEMS]


# ══════════════════════════════════════════════════════════════════════════════
# Ollama 生成中文标题与摘要（优化版 Prompt）
# ══════════════════════════════════════════════════════════════════════════════

def generate_chinese_summaries(items):
    """用 Ollama 本地模型为每条资讯生成编辑式中文标题和摘要。"""

    news_list = ""
    for i, item in enumerate(items):
        src_info = get_source_info(item["source"])
        src_tag = "国内" if src_info["type"] == "domestic" else "国际"
        news_list += (
            f"[{i+1}] {item['title']} | {item['summary']} "
            f"| 来源:{item['source']}({src_tag})\n"
        )

    prompt = f"""你是资深AI行业记者，精通中英文。将以下{len(items)}条资讯转化为中文精华版。

重要规则：
1. 先判断每条是否真正与AI/人工智能直接相关
2. 国际新闻：不要简单翻译英文标题，要提炼中文读者最关心的信息点，适当补充"对中国市场/开发者的影响"
3. 国内新闻：突出事件的行业影响和背景，避免公关稿式堆砌
4. 融资新闻要包含金额、估值、投资方等关键数据
5. 产品发布要说明核心功能和与竞品的差异
6. 技术突破要说明实际意义和潜在应用场景
7. 政策法规要说明影响范围和合规要求

每条必须包含以下字段：
- ai_related: true或false（是否与AI直接相关）
- emoji: 一个贴切的emoji
- title_zh: 中文标题（必须是中文！15-25字，像新闻编辑写的标题，不要直译）
- summary_zh: 中文摘要（必须是中文！50-100字，回答"这件事为什么重要"和"对行业有什么影响"）
- category: 分类标签（从以下选择：技术突破/融资/产品发布/政策法规/行业变动/应用落地/开源/研究）

好标题示例：「OpenAI 关停 Sora：日烧百万美元，用户不到50万」
好摘要示例：「据华尔街日报调查，Sora 上线仅半年，全球用户从百万骤降至不足50万，每日运营成本高达100万美元。这揭示了AI视频生成领域叫好不叫座的残酷现实。」

注意：title_zh和summary_zh都必须是中文，绝对不能是英文！

只输出JSON数组，不要其他任何内容：
[{{"id":1,"ai_related":true,"emoji":"🤖","title_zh":"中文标题","summary_zh":"中文摘要50到100字","category":"技术突破"}}]

资讯列表：
{news_list}"""

    try:
        print("      Calling Ollama (qwen2.5:7b)...")
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.3},
        }, timeout=300)
        text = resp.json()["message"]["content"].strip()
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            results = json.loads(json_match.group())
            filtered_count = 0
            for r in results:
                idx = r.get("id", 0) - 1 if isinstance(r.get("id"), int) else -1
                if idx < 0 or idx >= len(items):
                    continue
                if not r.get("ai_related", True):
                    items[idx]["_remove"] = True
                    filtered_count += 1
                    continue
                items[idx]["title_zh"] = r.get("title_zh", items[idx]["title"])
                items[idx]["summary_zh"] = r.get("summary_zh", items[idx]["summary"])
                items[idx]["emoji_override"] = r.get("emoji", "")
                items[idx]["category"] = r.get("category", "AI")
            items = [it for it in items if not it.get("_remove")]
            print(f"      Done: {len(results)} processed, {filtered_count} filtered as non-AI")
        else:
            print("[WARN] Ollama response not valid JSON, falling back")
            _fallback_titles(items)
    except Exception as e:
        print(f"[WARN] Ollama failed: {e}, falling back")
        _fallback_titles(items)

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
    (re.compile(r"open.?source|github|hugging|apache|mit.license|开源", re.I),
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
    "ScienceDaily": "\U0001f52c",
    "Ben's Bites": "\U0001f36a",
    "机器之心": "\U0001f916",
    "量子位": "\u26a1",
    "36氪": "\U0001f4b9",
    "IT之家": "\U0001f4f1",
    "新智元": "\U0001f31f",
    "InfoQ": "\U0001f4bb",
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
# HTML 生成（增强版 — 显示来源类型标识）
# ══════════════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI-Scream-{date}</title>
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
            <h1>AI-Scream-{date}</h1>
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
            \U0001f955 由 AI-Scream v2.0 自动生成 | {date} | 国内外 {source_count} 源聚合
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
# 飞书推送（增强版 — 显示来源类型）
# ══════════════════════════════════════════════════════════════════════════════

def build_feishu_card(items, date_str):
    intl_count = sum(1 for it in items if it.get("source_type") != "domestic")
    domestic_count = sum(1 for it in items if it.get("source_type") == "domestic")
    source_count = len(set(it["source"] for it in items))

    elements = []

    # 统计信息头
    elements.append({
        "tag": "markdown",
        "content": (
            f"📊 {len(items)}条精选 | "
            f"🌐 国际 {intl_count}条 | 🇨🇳 国内 {domestic_count}条 | "
            f"📡 {source_count}个来源"
        ),
    })
    elements.append({"tag": "hr"})

    for i, item in enumerate(items):
        tags = infer_tags(item)
        tag_str = " | ".join(label for label, _, _ in tags)
        emoji = pick_emoji(item)
        title_zh = item.get("title_zh", item["title"])
        summary_zh = item.get("summary_zh", item["summary"])
        src_info = get_source_info(item["source"])
        source_badge = f"{src_info['icon']} {src_info['display']}"

        elements.append({
            "tag": "markdown",
            "content": (
                f"{emoji} {tag_str} · {source_badge}\n"
                f"{title_zh}\n"
                f"{summary_zh}"
            ),
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
        if i < len(items) - 1:
            elements.append({"tag": "hr"})

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "\U0001f955 查看精美网页版"},
            "type": "default",
            "url": f"{PAGES_URL}/latest.html",
        }],
    })
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": f"\U0001f955 由 AI-Scream v2.0 自动生成 | {date_str} | {source_count}源聚合",
        }],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"\U0001f955 AI-Scream-{date_str} | 今日AI资讯精选",
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
        (pages / f"AI-Scream-{date_str}.html").write_text(html_content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(pages), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"update: AI-Scream {date_str}"],
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
    print(f"  🥕 AI-Scream v2.0 | {today}")
    print(f"  多源聚合 · 智能去重 · 国内外平衡")
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

    print("  [A3] Ben's Bites...")
    bb = fetch_bensbites()
    print(f"       → {len(bb)} items")

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

    print("  [B6] ScienceDaily AI...")
    sd = fetch_sciencedaily()
    print(f"       → {len(sd)} items")

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

    # ═══════════════════════════════════════════════════════════
    # Phase D: 抓取状态检查 + 补充搜索
    # ═══════════════════════════════════════════════════════════
    print("\n📊 [Phase D] 抓取状态检查...")
    tracker.print_report()

    # 补充搜索
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
    print("\n🔄 [Phase E] 合并去重排序...")
    all_items = (
        tldr + hn + bb +
        tc + tv + ars + vb + mit + sd +
        jqzx + qb + kr + ith + xzy + iq +
        supp_intl + supp_domestic
    )
    print(f"      Total raw: {len(all_items)}")

    final = deduplicate_and_rank(all_items)
    print(f"      After dedup + diversity: {len(final)}")

    # 打印来源分布
    source_dist = {}
    for it in final:
        src = it["source"]
        source_dist[src] = source_dist.get(src, 0) + 1
    print("      来源分布:")
    for src, cnt in sorted(source_dist.items(), key=lambda x: -x[1]):
        icon = get_source_info(src)["icon"]
        print(f"        {icon} {src}: {cnt}")

    if not final:
        print("[ERROR] No items fetched. Check network. ❌")
        return

    # ═══════════════════════════════════════════════════════════
    # Phase F: 生成中文摘要
    # ═══════════════════════════════════════════════════════════
    print(f"\n✍️  [Phase F] Generating Chinese summaries...")
    final = generate_chinese_summaries(final)

    # ═══════════════════════════════════════════════════════════
    # Phase G: 生成 HTML
    # ═══════════════════════════════════════════════════════════
    html = generate_html(final, today)
    output_path = OUTPUT_DIR / f"AI-Scream-{today}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"\n📄 [Phase G] HTML saved: {output_path}")

    # ═══════════════════════════════════════════════════════════
    # Phase H: 发布
    # ═══════════════════════════════════════════════════════════
    print("\n🚀 [Phase H] Publishing...")
    publish_to_pages(html, today)

    card = build_feishu_card(final, today)
    push_feishu(card)

    # ═══════════════════════════════════════════════════════════
    # 完成摘要
    # ═══════════════════════════════════════════════════════════
    intl_final = sum(1 for it in final if it.get("source_type") != "domestic")
    dom_final = sum(1 for it in final if it.get("source_type") == "domestic")
    print(f"\n{'='*60}")
    print(f"  ✅ Done! {len(final)} items ({intl_final} intl + {dom_final} domestic)")
    print(f"  📡 Sources: {len(source_dist)}")
    print(f"{'='*60}")

    print(f"\n  Top 5 Stories:")
    for i, item in enumerate(final[:5], 1):
        src_info = get_source_info(item["source"])
        title = item.get("title_zh", item["title"])
        print(f"    {i}. {src_info['icon']} [{src_info['display']}] {title}")

    print(f"\n  🥕 AI-Scream v2.0 — All done!\n")


if __name__ == "__main__":
    main()
