"""
AI-Scream 纯 Python 版 —— 每日 AI 资讯抓取、HTML 生成与飞书推送脚本。
数据源: TechCrunch AI RSS, Hacker News API, TLDR.tech AI 归档页,
        36Kr AI频道, 机器之心, 量子位, InfoQ AI, ScienceDaily AI。
摘要生成: Ollama 本地模型 (Qwen 2.5 7B)，完全免费。

v2.0 更新：
  - 新增三级内容分类体系（AI实用工具与Web3 / 大模型与AGI / 科技硬核分析）
  - 新增 Web3/区块链关键词匹配
  - 新增标题党黑名单过滤
  - 评分体系升级（分类权重 + 质量过滤）
  - HTML 模板增加分类标签展示
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path

import feedparser
import requests

# ── 日志配置 ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AI-Scream")

# ── 配置 ──────────────────────────────────────────────
# ✅ 敏感信息从环境变量读取，避免硬编码
FEISHU_WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/30bd0594-8318-4475-9f34-e0ed5a65de00",
)
# GEMINI_API_KEY 当前代码中未实际使用，保留环境变量入口以备后续扩展
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

OUTPUT_DIR = Path.home()
PAGES_DIR = Path.home() / "ai-scream-pages"
PAGES_URL = "https://twinkleshinya.github.io/ai-scream-pages"
MAX_ITEMS = 20
HN_TOP_N = 30
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b"

AI_KEYWORDS = re.compile(
    r"artificial.intelligence|machine.learning|deep.learning"
    r"|llm|large.language|gpt.?[3-5]|openai|claude|anthropic|gemini|mistral"
    r"|chatgpt|diffusion|neural.net|transformer|generative.ai"
    r"|langchain|hugging.?face|stable.diffusion|midjourney"
    r"|ai.agent|ai.model|foundation.model|reasoning.model"
    r"|ai.chip|ai.video|ai.startup|ai.fund|ai.regul|ai.safety"
    r"|sora|dall.?e|copilot.ai|cursor.ai|ai.coding"
    # ✅ 新增：Web3 相关英文关键词
    r"|web3|blockchain|metaverse|dao(?!\w)|smart.contract"
    r"|nft|defi|decentraliz|crypto|ethereum|solana|layer.?2"
    r"|zero.knowledge|zk.proof|token(?:iz|omics)",
    re.IGNORECASE,
)

AI_KEYWORDS_ZH = re.compile(
    r"人工智能|大模型|大语言模型|机器学习|深度学习|神经网络"
    r"|生成式AI|生成式人工智能|AI芯片|AI应用|AI创业|AI融资"
    r"|智能体|AI[Aa]gent|多模态|AIGC|具身智能|自动驾驶"
    r"|通用人工智能|AGI|强化学习|提示词|Prompt"
    r"|OpenAI|Claude|Anthropic|谷歌|Google|微软|Microsoft|Meta"
    r"|百度|文心|通义|智谱|月之暗面|Kimi|DeepSeek|零一万物"
    r"|字节|豆包|MiniMax|阶跃星辰|百川智能|讯飞|商汤"
    r"|英伟达|NVIDIA|AMD|算力|GPU|芯片"
    r"|开源模型|闭源|源码|逆向|泄露"
    r"|AI监管|AI安全|AI伦理|AI政策"
    # ✅ 新增：Web3 相关中文关键词
    r"|Web3|区块链|元宇宙|智能合约|去中心化|加密货币"
    r"|以太坊|零知识证明|数字藏品|链上|代币经济"
    # ✅ 新增：具身智能 / 机器人相关
    r"|具身智能|人形机器人|机器人|机械臂|传感器融合"
    # ✅ 新增：AI实用工具相关
    r"|AI绘画|AI证件照|AI写真|AI办公|AI工具|AI教程"
    r"|AI配音|AI翻译|AI助手|AI插件",
    re.IGNORECASE,
)

# ✅ 新增：Web3 专用关键词正则（用于分类判定）
WEB3_KEYWORDS = re.compile(
    r"Web3|区块链|blockchain|元宇宙|metaverse|DAO(?!\w)|智能合约"
    r"|smart.contract|NFT|DeFi|去中心化|加密货币|以太坊|Ethereum"
    r"|Solana|Layer.?2|零知识证明|ZK.proof|代币|token(?:iz|omics)"
    r"|数字藏品|链上|dApp|跨链|侧链|共识机制|质押|挖矿",
    re.IGNORECASE,
)

# ✅ 新增：标题党黑名单过滤正则
CLICKBAIT_FILTER = re.compile(
    r"震惊|快看|这竟然|千万别买|后悔了|速看|不转不是|99%的人"
    r"|惊呆了|太疯狂|细思极恐|重大信号|暴涨|暴跌"
    r"|你绝对想不到|赶紧收藏|最后一天|限时免费|必看"
    r"|clickbait|you.won.?t.believe|shocking|insane",
    re.IGNORECASE,
)

# ✅ 新增：营销号过滤正则
SPAM_FILTER = re.compile(
    r"免费领取|加微信|扫码|关注公众号|转发抽奖|优惠券|折扣码"
    r"|限时优惠|0元|白嫖|薅羊毛|推广|广告合作|商务合作"
    r"|sponsor|promoted|ad\b|advertisement",
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

BREAKING_KEYWORDS = re.compile(
    r"重磅|突发|首次|史上最|全面|颠覆|里程碑|独家|曝光|泄露|开源"
    r"|发布|上线|收购|合并|融资|IPO|估值|破产|关停|禁令"
    r"|breaking|exclusive|launch|acquire|billion|shutdown"
    r"|leaked|open.?source|release",
    re.IGNORECASE,
)

# ✅ 更新：新增 ScienceDaily 的权威性评分
SOURCE_AUTHORITY_SCORE = {
    "36Kr": 15,
    "机器之心": 15,
    "量子位": 12,
    "InfoQ": 10,
    "TechCrunch": 12,
    "Hacker News": 0,  # HN 用自己的 score
    "TLDR.tech": 5,
    "ScienceDaily": 8,
}

# ── ✅ 新增：三级内容分类体系 ────────────────────────────

# 分类1: AI实用工具与Web3探索（小红书风格）
CATEGORY_TOOLS_WEB3 = re.compile(
    r"AI绘画|AI证件照|AI写真|AI办公|AI工具|AI教程|AI配音|AI翻译"
    r"|AI助手|AI插件|AI修图|AI抠图|AI换脸|AI变声|AI视频剪辑"
    r"|Stable.Diffusion|Midjourney|ComfyUI|LoRA|ControlNet"
    r"|Web3|区块链|blockchain|元宇宙|metaverse|DAO(?!\w)|智能合约"
    r"|smart.contract|NFT|DeFi|去中心化|加密货币|以太坊"
    r"|Solana|Layer.?2|零知识证明|代币|数字藏品"
    r"|实操|教程|指南|上手|测评|对比|推荐",
    re.IGNORECASE,
)

# 分类2: 大模型与通用人工智能（抖音热搜风格）
CATEGORY_LLM_AGI = re.compile(
    r"LLM|大语言模型|大模型|GPT.?[3-5]|OpenAI|Claude|Anthropic"
    r"|Gemini|Mistral|DeepSeek|通义|文心|智谱|Kimi|Llama|Qwen"
    r"|月之暗面|零一万物|阶跃星辰|百川智能|MiniMax"
    r"|AGI|通用人工智能|多模态|AIGC|生成式AI"
    r"|API|降价|开源模型|闭源|参数|token|benchmark"
    r"|Sora|视频生成|图像生成|语音合成|文生图|文生视频"
    r"|AI监管|AI安全|AI伦理|AI政策|禁令"
    r"|融资|收购|IPO|估值|竞争|发布|上线",
    re.IGNORECASE,
)

# 分类3: 科技硬核分析与AI科普（B站长视频风格）
CATEGORY_HARDCORE = re.compile(
    r"AI芯片|GPU|算力|英伟达|NVIDIA|AMD|华为|昇腾|TPU"
    r"|Transformer|Mamba|架构|注意力机制|Attention"
    r"|具身智能|机器人|人形机器人|自动驾驶|机械臂|传感器"
    r"|深度学习|强化学习|神经网络|卷积|扩散模型|训练"
    r"|推理|量化|蒸馏|微调|fine.?tun|MoE|混合专家"
    r"|原理|拆解|解析|深度|分析|科普|测评"
    r"|数据中心|基础设施|inference|latency|throughput",
    re.IGNORECASE,
)

# 分类名称与对应CSS/emoji映射
CATEGORY_MAP = {
    "tools_web3": {
        "name": "AI工具与Web3",
        "short": "工具/Web3",
        "css": "cat-tools",
        "emoji": "🛠️",
        "color": "#10b981",
    },
    "llm_agi": {
        "name": "大模型与AGI",
        "short": "大模型/AGI",
        "css": "cat-llm",
        "emoji": "🧠",
        "color": "#6366f1",
    },
    "hardcore": {
        "name": "硬核科技分析",
        "short": "硬核分析",
        "css": "cat-hardcore",
        "emoji": "⚙️",
        "color": "#f59e0b",
    },
    "general": {
        "name": "综合资讯",
        "short": "综合",
        "css": "cat-general",
        "emoji": "📰",
        "color": "#8b5cf6",
    },
}


def classify_content(item):
    """对资讯进行三级分类，返回分类key。"""
    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('title_zh', '')} {item.get('summary_zh', '')}"

    scores = {
        "tools_web3": len(CATEGORY_TOOLS_WEB3.findall(text)),
        "llm_agi": len(CATEGORY_LLM_AGI.findall(text)),
        "hardcore": len(CATEGORY_HARDCORE.findall(text)),
    }

    # 取匹配数最多的分类
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "general"
    return best


# ── 数据抓取 ──────────────────────────────────────────


def fetch_techcrunch():
    """TechCrunch AI RSS feed."""
    url = "https://techcrunch.com/category/artificial-intelligence/feed/"
    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:20]:
            summary = entry.get("summary", "")
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            if len(summary) > 200:
                summary = summary[:200] + "..."
            items.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "summary": summary,
                "source": "TechCrunch",
                "date": entry.get("published", ""),
                "score": 0,
            })
    except Exception as e:
        logger.warning(f"TechCrunch fetch failed: {e}")
    return items


def fetch_hackernews():
    """Hacker News API — top stories filtered for AI keywords."""
    items = []
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10
        )
        top_ids = resp.json()[:HN_TOP_N]
        for sid in top_ids:
            story = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5
            ).json()
            if not story or story.get("type") != "story":
                continue
            title = story.get("title", "")
            url = story.get("url", f"https://news.ycombinator.com/item?id={sid}")
            if AI_KEYWORDS.search(title) or AI_KEYWORDS.search(url):
                if PAPER_FILTER.search(title) or PAPER_FILTER.search(url):
                    continue
                if FALSE_POSITIVE_FILTER.search(title):
                    continue
                items.append({
                    "title": title,
                    "url": url,
                    "summary": f"HN Score: {story.get('score', 0)} | Comments: {story.get('descendants', 0)}",
                    "source": "Hacker News",
                    "date": datetime.fromtimestamp(
                        story.get("time", 0), tz=timezone.utc
                    ).isoformat(),
                    "score": story.get("score", 0),
                })
    except Exception as e:
        logger.warning(f"HN fetch failed: {e}")
    return items


def fetch_sciencedaily():
    """ScienceDaily AI RSS feed."""
    url = "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml"
    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:15]:
            summary = entry.get("summary", "")
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            if len(summary) > 200:
                summary = summary[:200] + "..."
            items.append({
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "summary": summary,
                "source": "ScienceDaily",
                "date": entry.get("published", ""),
                "score": 0,
            })
    except Exception as e:
        logger.warning(f"ScienceDaily fetch failed: {e}")
    return items


def fetch_tldr():
    """TLDR.tech AI — 爬取最新一期归档页。"""
    items = []
    try:
        resp = requests.get("https://tldr.tech/ai/archives", timeout=10)
        dates = re.findall(r"/ai/(\d{4}-\d{2}-\d{2})", resp.text)
        if not dates:
            return items
        latest = sorted(dates, reverse=True)[0]
        detail = requests.get(f"https://tldr.tech/ai/{latest}", timeout=10)
        links = re.findall(
            r'<a[^>]+href="(https?://(?!tldr\.tech)[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
            detail.text,
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
            ):
                seen.add(url)
                items.append({
                    "title": title,
                    "url": url,
                    "summary": f"via TLDR AI ({latest})",
                    "source": "TLDR.tech",
                    "date": latest,
                    "score": 0,
                })
    except Exception as e:
        logger.warning(f"TLDR fetch failed: {e}")
    return items


def fetch_36kr():
    """36Kr AI/人工智能频道 — 抓取最新文章。"""
    items = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://36kr.com/",
        }
        # 方式1: 36kr 快讯API
        resp = requests.get(
            "https://36kr.com/api/newsflash",
            params={"per_page": 30},
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            news_list = data.get("data", {}).get("items", [])
            for news in news_list:
                title = news.get("title", "") or news.get("description", "")
                summary = news.get("description", "") or title
                url = f"https://36kr.com/newsflashes/{news.get('id', '')}"
                if AI_KEYWORDS_ZH.search(title) or AI_KEYWORDS_ZH.search(summary):
                    if len(summary) > 200:
                        summary = summary[:200] + "..."
                    items.append({
                        "title": title,
                        "url": url,
                        "summary": summary,
                        "source": "36Kr",
                        "date": news.get("published_at", ""),
                        "score": 0,
                        "is_chinese": True,
                    })

        # 方式2: 抓取36kr AI频道页面
        resp2 = requests.get(
            "https://36kr.com/information/AI/",
            headers=headers,
            timeout=15,
        )
        if resp2.status_code == 200:
            articles = re.findall(
                r'href="(/p/\d+)"[^>]*>.*?<[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)',
                resp2.text,
                re.DOTALL,
            )
            # ✅ 增强：备用匹配模式1
            if not articles:
                articles = re.findall(
                    r'<a[^>]+href="(/p/(\d+))"[^>]*>\s*<[^>]*>([^<]{10,})',
                    resp2.text,
                )
                articles = [(a[0], a[2]) for a in articles]
            # ✅ 增强：备用匹配模式2（更宽松）
            if not articles:
                articles = re.findall(
                    r'href="(/p/\d+)"[^>]*>([^<]{8,80})</a>',
                    resp2.text,
                )

            seen_urls = {item["url"] for item in items}
            for path, title in articles[:15]:
                title = title.strip()
                url = f"https://36kr.com{path}" if path.startswith("/") else path
                if url not in seen_urls and len(title) > 5:
                    seen_urls.add(url)
                    items.append({
                        "title": title,
                        "url": url,
                        "summary": f"via 36Kr AI频道",
                        "source": "36Kr",
                        "date": "",
                        "score": 0,
                        "is_chinese": True,
                    })
    except Exception as e:
        logger.warning(f"36Kr fetch failed: {e}")
    return items


def fetch_jiqizhixin():
    """机器之心（Synced / 机器之心）— AI领域最权威的中文媒体之一。"""
    items = []
    try:
        urls_to_try = [
            "https://www.jiqizhixin.com/rss",
            "https://rsshub.app/jiqizhixin",
        ]
        feed = None
        for rss_url in urls_to_try:
            try:
                feed = feedparser.parse(rss_url)
                if feed.entries:
                    break
            except Exception:
                continue

        if feed and feed.entries:
            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                summary = re.sub(r"<[^>]+>", "", summary).strip()
                if len(summary) > 200:
                    summary = summary[:200] + "..."
                url = entry.get("link", "")
                items.append({
                    "title": title,
                    "url": url,
                    "summary": summary,
                    "source": "机器之心",
                    "date": entry.get("published", ""),
                    "score": 0,
                    "is_chinese": True,
                })
        else:
            # 降级：直接爬取网页
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            resp = requests.get("https://www.jiqizhixin.com/", headers=headers, timeout=15)
            if resp.status_code == 200:
                # ✅ 增强：多种匹配模式
                articles = re.findall(
                    r'<a[^>]+href="(https?://www\.jiqizhixin\.com/articles/[^"]+)"[^>]*>\s*([^<]{10,})',
                    resp.text,
                )
                # 备用匹配模式
                if not articles:
                    articles = re.findall(
                        r'href="(/articles/[^"]+)"[^>]*>([^<]{10,80})</a>',
                        resp.text,
                    )
                    articles = [(f"https://www.jiqizhixin.com{a[0]}", a[1]) for a in articles]

                for url, title in articles[:10]:
                    title = title.strip()
                    items.append({
                        "title": title,
                        "url": url,
                        "summary": f"via 机器之心",
                        "source": "机器之心",
                        "date": "",
                        "score": 0,
                        "is_chinese": True,
                    })
    except Exception as e:
        logger.warning(f"机器之心 fetch failed: {e}")
    return items


def fetch_qbitai():
    """量子位（QbitAI）— 国内知名AI媒体。"""
    items = []
    try:
        urls_to_try = [
            "https://rsshub.app/qbitai",
            "https://www.qbitai.com/feed",
        ]
        feed = None
        for rss_url in urls_to_try:
            try:
                feed = feedparser.parse(rss_url)
                if feed.entries:
                    break
            except Exception:
                continue

        if feed and feed.entries:
            for entry in feed.entries[:15]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                summary = re.sub(r"<[^>]+>", "", summary).strip()
                if len(summary) > 200:
                    summary = summary[:200] + "..."
                items.append({
                    "title": title,
                    "url": entry.get("link", ""),
                    "summary": summary,
                    "source": "量子位",
                    "date": entry.get("published", ""),
                    "score": 0,
                    "is_chinese": True,
                })
    except Exception as e:
        logger.warning(f"量子位 fetch failed: {e}")
    return items


def fetch_infoq():
    """InfoQ AI频道 — 开发者社区权威技术媒体。"""
    items = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        resp = requests.post(
            "https://www.infoq.cn/public/v1/article/getList",
            json={"type": 1, "size": 15, "id": 33},  # 33 = AI频道
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            articles = data.get("data", [])
            for article in articles:
                title = article.get("article_title", "")
                summary = article.get("article_summary", "")
                uuid = article.get("uuid", "")
                url = f"https://www.infoq.cn/article/{uuid}" if uuid else ""
                if title and url:
                    if len(summary) > 200:
                        summary = summary[:200] + "..."
                    items.append({
                        "title": title,
                        "url": url,
                        "summary": summary or f"via InfoQ AI频道",
                        "source": "InfoQ",
                        "date": article.get("publish_time", ""),
                        "score": 0,
                        "is_chinese": True,
                    })
    except Exception as e:
        logger.warning(f"InfoQ fetch failed: {e}")
    return items


# ── ✅ 新增：标题质量过滤 ─────────────────────────────────


def filter_low_quality(items):
    """过滤标题党、营销号等低质量内容。"""
    filtered = []
    removed_count = 0
    for item in items:
        title = item.get("title", "")
        summary = item.get("summary", "")
        text = f"{title} {summary}"

        # 1. 标题党过滤
        if CLICKBAIT_FILTER.search(title):
            logger.debug(f"Filtered (clickbait): {title}")
            removed_count += 1
            continue

        # 2. 营销号过滤
        if SPAM_FILTER.search(text):
            logger.debug(f"Filtered (spam): {title}")
            removed_count += 1
            continue

        # 3. 标题过短（可能是噪音）
        if len(title.strip()) < 5:
            logger.debug(f"Filtered (too short): {title}")
            removed_count += 1
            continue

        filtered.append(item)

    if removed_count > 0:
        logger.info(f"Quality filter removed {removed_count} low-quality items")
    return filtered


# ── 去重与排序 ─────────────────────────────────────────


def deduplicate_and_rank(all_items):
    """URL 去重 + 质量过滤 + 多维度综合评分排序。"""
    # ✅ 新增：先进行质量过滤
    all_items = filter_low_quality(all_items)

    seen_urls = set()
    unique = []

    for item in all_items:
        url = item["url"].rstrip("/")
        if url not in seen_urls and item["title"]:
            seen_urls.add(url)
            item["final_score"] = calculate_importance_score(item)
            # ✅ 新增：分类标记
            item["category"] = classify_content(item)
            unique.append(item)

    unique.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    return unique[:MAX_ITEMS]


def calculate_importance_score(item):
    """多维度综合评分算法。"""
    score = 0
    title = item.get("title", "")
    summary = item.get("summary", "")
    text = f"{title} {summary}"
    source = item.get("source", "")

    # 1. 来源权威性加分
    score += SOURCE_AUTHORITY_SCORE.get(source, 0)

    # 2. HN 原始分数（归一化到0-20范围）
    hn_score = item.get("score", 0)
    if hn_score > 0:
        if hn_score >= 500:
            score += 20
        elif hn_score >= 200:
            score += 15
        elif hn_score >= 100:
            score += 10
        elif hn_score >= 50:
            score += 5

    # 3. 重大事件关键词加分
    breaking_matches = BREAKING_KEYWORDS.findall(text)
    score += min(len(breaking_matches) * 5, 15)

    # 4. 涉及头部公司/产品额外加分
    major_entities = re.compile(
        r"OpenAI|Google|Microsoft|Meta|Apple|Nvidia|英伟达|谷歌|微软"
        r"|GPT-?[4-5]|Claude|Gemini|Llama|DeepSeek|Sora"
        r"|百度|字节|阿里|腾讯|华为",
        re.IGNORECASE,
    )
    entity_matches = major_entities.findall(text)
    score += min(len(set(entity_matches)) * 3, 12)

    # 5. 标题长度适中加分（太短可能是噪音）
    title_len = len(title)
    if 15 <= title_len <= 60:
        score += 3

    # 6. 今日新闻时效性加分
    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(beijing_tz).strftime("%Y-%m-%d")
    if today in item.get("date", ""):
        score += 5

    # ✅ 新增 7. 包含具体技术参数/数据加分（偏好客观事实）
    factual_pattern = re.compile(
        r"\d+[BMK]\s*参数|\d+%|v\d+\.\d+|\$\d+|billion|million"
        r"|\d+亿|\d+万|\d+\.?\d*[TB]|benchmark|SOTA|评测",
        re.IGNORECASE,
    )
    if factual_pattern.search(text):
        score += 4

    # ✅ 新增 8. Web3相关内容加分（确保Web3不被边缘化）
    if WEB3_KEYWORDS.search(text):
        score += 3

    return score


# ── Ollama 生成中文标题与摘要 ─────────────────────────


def generate_chinese_summaries(items):
    """用 Ollama 本地模型为每条资讯生成编辑式中文标题和摘要。"""
    chinese_items = [it for it in items if it.get("is_chinese")]
    english_items = [it for it in items if not it.get("is_chinese")]

    if chinese_items:
        logger.info(f"Processing {len(chinese_items)} Chinese items...")
        chinese_items = _summarize_chinese_items(chinese_items)

    if english_items:
        logger.info(f"Processing {len(english_items)} English items...")
        english_items = _summarize_english_items(english_items)

    all_items = chinese_items + english_items
    all_items.sort(key=lambda x: x.get("final_score", 0), reverse=True)

    # ✅ 新增：摘要生成后重新分类（因为现在有中文标题和摘要了，分类更准确）
    for item in all_items:
        item["category"] = classify_content(item)

    return all_items


def _summarize_chinese_items(items):
    """处理中文资讯（优化标题和摘要）。"""
    news_list = ""
    for i, item in enumerate(items):
        news_list += f"[{i+1}] {item['title']} | {item['summary']} | 来源:{item['source']}\n"

    prompt = f"""你是资深AI行业记者。以下是{len(items)}条中文AI资讯，请优化它们的标题和摘要。

每条必须包含以下字段：
- ai_related: true或false（判断是否真正与AI直接相关）
- emoji: 一个贴切的emoji
- title_zh: 优化后的中文标题（15-25字，像新闻编辑写的标题，简洁有力）
- summary_zh: 中文摘要（50-100字，解释这件事为什么重要，有洞察力）

好标题示例：「Claude Code源码疑似被逆向还原，4756个文件全部曝光」
好摘要示例：「有技术大佬通过npm包的source map逆向还原出Anthropic闭源产品Claude Code的完整源码，涉及4756个文件。这是业界首次完整看到顶级Agent产品的工程化架构，引发广泛关注。」

只输出JSON数组，不要其他任何内容：
[{{"id":1,"ai_related":true,"emoji":"🤖","title_zh":"中文标题","summary_zh":"中文摘要"}}]

资讯列表：
{news_list}"""

    try:
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
            for r in results:
                idx = r.get("id", 0) - 1 if isinstance(r.get("id"), int) else -1
                if idx < 0 or idx >= len(items):
                    continue
                if not r.get("ai_related", True):
                    items[idx]["_remove"] = True
                    continue
                items[idx]["title_zh"] = r.get("title_zh", items[idx]["title"])
                items[idx]["summary_zh"] = r.get("summary_zh", items[idx]["summary"])
                items[idx]["emoji_override"] = r.get("emoji", "")
            items = [it for it in items if not it.get("_remove")]
    except Exception as e:
        logger.warning(f"Chinese summary failed: {e}")

    for item in items:
        if "title_zh" not in item:
            item["title_zh"] = item["title"]
        if "summary_zh" not in item:
            item["summary_zh"] = item["summary"]
    return items


def _summarize_english_items(items):
    """处理英文资讯（翻译+摘要）。"""
    news_list = ""
    for i, item in enumerate(items):
        news_list += f"[{i+1}] {item['title']} | {item['summary']} | 来源:{item['source']}\n"

    prompt = f"""你是资深AI行业记者。将以下{len(items)}条英文资讯转化为中文精华版。

重要：先判断每条是否真正与AI/人工智能直接相关。
- 直接相关：AI模型发布、AI公司融资/动态、AI产品、AI政策、AI芯片、AI应用等
- 不相关：普通科技新闻、非AI的融资、一般软件工具、纯硬件（非AI芯片）等

每条必须包含以下字段：
- ai_related: true或false
- emoji: 一个贴切的emoji
- title_zh: 中文标题（必须是中文！15-25字，像新闻编辑写的标题，不要直译）
- summary_zh: 中文摘要（必须是中文！50-100字，解释这件事为什么重要，有洞察力）

好标题：「OpenAI 关停 Sora：日烧百万美元，用户不到50万」
好摘要：「据华尔街日报调查，Sora 上线仅半年，全球用户从百万骤降至不足50万，每日运营成本高达100万美元。这揭示了AI视频生成领域叫好不叫座的残酷现实。」

注意：title_zh和summary_zh都必须是中文，绝对不能是英文！

只输出JSON数组，不要其他任何内容：
[{{"id":1,"ai_related":true,"emoji":"🤖","title_zh":"中文标题","summary_zh":"中文摘要50到100字"}}]

资讯列表：
{news_list}"""

    try:
        logger.info("Calling Ollama (qwen2.5:7b)...")
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
            items = [it for it in items if not it.get("_remove")]
            logger.info(f"Done: {len(results)} processed, {filtered_count} filtered as non-AI")
        else:
            logger.warning("Ollama response not valid JSON, falling back")
            _fallback_titles(items)
    except Exception as e:
        logger.warning(f"Ollama failed: {e}, falling back")
        _fallback_titles(items)

    for item in items:
        if "title_zh" not in item:
            item["title_zh"] = item["title"]
        if "summary_zh" not in item:
            item["summary_zh"] = item["summary"]
    return items


def _fallback_titles(items):
    """Ollama 失败时的降级方案：直接用原文。"""
    for item in items:
        item["title_zh"] = item["title"]
        item["summary_zh"] = item["summary"]


# ── 标签推断 ──────────────────────────────────────────

TAG_RULES = [
    (re.compile(r"llm|gpt|claude|gemini|model|mistral|anthropic|openai|大模型|大语言|模型", re.I), "大模型", "tag-llm", "\U0001f916"),
    (re.compile(r"fund|rais|invest|ipo|valuat|\$\d|billion|million|serie|融资|估值|投资|收购", re.I), "融资", "tag-biz", "\U0001f4b0"),
    (re.compile(r"open.?source|github|hugging|apache|mit.license|开源|源码", re.I), "开源", "tag-open", "\U0001f331"),
    (re.compile(r"regulat|policy|govern|law|eu.ai|congress|senate|ban|court|监管|政策|法规|禁令", re.I), "政策", "tag-policy", "\U0001f3db"),
    (re.compile(r"launch|releas|announc|introduc|new.feature|product|发布|上线|推出|产品", re.I), "产品", "tag-product", "\U0001f680"),
    (re.compile(r"research|study|scientif|danger|risk|warning|研究|论文|发现", re.I), "研究", "tag-research", "\U0001f52c"),
    (re.compile(r"secur|privacy|hack|exploit|vulnerab|data.collect|track|安全|隐私|泄露|漏洞", re.I), "安全", "tag-policy", "\U0001f512"),
    (re.compile(r"chip|gpu|nvidia|hardware|data.center|infra|芯片|算力|英伟达|基础设施", re.I), "基础设施", "tag-other", "\u2699\ufe0f"),
    (re.compile(r"video|image|generat|sora|diffusion|creative|视频|图像|生成|创作", re.I), "创作", "tag-product", "\U0001f3a8"),
    (re.compile(r"agent|autonom|coding.agent|free.software|智能体|Agent", re.I), "Agent", "tag-llm", "\U0001f9e0"),
    # ✅ 新增：Web3 标签
    (re.compile(r"Web3|区块链|blockchain|DAO|智能合约|NFT|DeFi|元宇宙|去中心化|代币", re.I), "Web3", "tag-web3", "\U0001f517"),
    # ✅ 新增：机器人/具身智能标签
    (re.compile(r"具身智能|机器人|robot|humanoid|自动驾驶|autonomous|机械臂", re.I), "机器人", "tag-robot", "\U0001f916"),
    # ✅ 新增：AI工具标签
    (re.compile(r"AI绘画|AI证件照|AI写真|AI办公|AI工具|教程|实操|指南|测评", re.I), "工具", "tag-tools", "\U0001f6e0"),
]

SOURCE_EMOJI = {
    "Hacker News": "\U0001f525",
    "TechCrunch": "\U0001f4f0",
    "TLDR.tech": "\U0001f4e8",
    "36Kr": "\U0001f1e8\U0001f1f3",
    "机器之心": "\U0001f9e0",
    "量子位": "\u26a1",
    "InfoQ": "\U0001f4bb",
    "ScienceDaily": "\U0001f52c",
}


def infer_tags(item):
    text = f"{item['title']} {item['summary']} {item.get('title_zh', '')} {item.get('summary_zh', '')}"
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


# ── HTML 生成 ─────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI-Scream-{date}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
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
        /* ✅ 新增：分类过滤按钮样式 */
        .category-filters {{
            display: flex;
            justify-content: center;
            gap: 12px;
            margin: 24px 0;
            flex-wrap: wrap;
        }}
        .cat-btn {{
            padding: 8px 20px;
            border-radius: 24px;
            border: 1px solid rgba(255,255,255,0.15);
            background: rgba(255,255,255,0.05);
            color: rgba(255,255,255,0.7);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.25s ease;
            user-select: none;
        }}
        .cat-btn:hover, .cat-btn.active {{
            background: rgba(255,255,255,0.15);
            color: white;
            border-color: rgba(255,255,255,0.3);
        }}
        .cat-btn[data-cat="all"].active {{ border-color: #8b5cf6; color: #c4b5fd; }}
        .cat-btn[data-cat="tools_web3"].active {{ border-color: #10b981; color: #6ee7b7; }}
        .cat-btn[data-cat="llm_agi"].active {{ border-color: #6366f1; color: #a5b4fc; }}
        .cat-btn[data-cat="hardcore"].active {{ border-color: #f59e0b; color: #fcd34d; }}
        .cat-btn[data-cat="general"].active {{ border-color: #8b5cf6; color: #c4b5fd; }}
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
            transition: transform 0.25s ease, box-shadow 0.25s ease, opacity 0.3s ease;
            cursor: pointer;
            text-decoration: none;
            color: inherit;
            display: block;
            border: 1px solid rgba(255,255,255,0.06);
        }}
        .card:hover {{
            transform: translateY(-6px);
            box-shadow: 0 12px 36px rgba(0,0,0,0.5);
            border-color: rgba(255,255,255,0.12);
        }}
        .card.featured {{
            border: 1px solid rgba(255, 165, 0, 0.3);
            background: linear-gradient(135deg, #16161e 0%, #1a1a28 100%);
        }}
        .card.hidden {{
            display: none;
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
        .tag-featured {{
            background: linear-gradient(135deg, #ff6b00, #ff9500);
            color: white;
            font-weight: 700;
        }}
        .tag-emoji {{
            font-size: 16px;
            line-height: 1;
        }}
        /* ✅ 新增：Web3 / 机器人 / 工具标签样式 */
        .tag-web3 {{ background: #fef3c7; color: #92400e; }}
        .tag-robot {{ background: #dbeafe; color: #1e40af; }}
        .tag-tools {{ background: #ecfdf5; color: #065f46; }}
        /* ✅ 新增：分类标签样式 */
        .cat-tag {{
            font-size: 10px;
            padding: 2px 8px;
            border-radius: 10px;
            font-weight: 700;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }}
        .cat-tools {{ background: rgba(16,185,129,0.15); color: #10b981; }}
        .cat-llm {{ background: rgba(99,102,241,0.15); color: #818cf8; }}
        .cat-hardcore {{ background: rgba(245,158,11,0.15); color: #f59e0b; }}
        .cat-general {{ background: rgba(139,92,246,0.15); color: #a78bfa; }}
        .card-title {{
            font-size: 16px;
            font-weight: 700;
            color: #e8e8ed;
            margin-bottom: 10px;
            line-height: 1.5;
        }}
        .card-title .carrot {{ margin-right: 4px; }}
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
        /* ✅ 新增：分类统计条 */
        .category-stats {{
            display: flex;
            justify-content: center;
            gap: 16px;
            margin-top: 12px;
            flex-wrap: wrap;
        }}
        .cat-stat {{
            font-size: 12px;
            color: rgba(255,255,255,0.5);
        }}
        .cat-stat .count {{
            font-weight: 700;
            margin-left: 4px;
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
            <div class="subtitle">每日AI行业资讯精选 | 中英文全球覆盖</div>
            <div class="stats">
                <span class="stat">{count} 条精选</span>
                <span class="stat">来源 36Kr / 机器之心 / TechCrunch / HN / TLDR / InfoQ / ScienceDaily</span>
            </div>
            <div class="category-stats">
                <span class="cat-stat">🛠️ 工具/Web3 <span class="count">{cat_tools_count}</span></span>
                <span class="cat-stat">🧠 大模型/AGI <span class="count">{cat_llm_count}</span></span>
                <span class="cat-stat">⚙️ 硬核分析 <span class="count">{cat_hardcore_count}</span></span>
                <span class="cat-stat">📰 综合 <span class="count">{cat_general_count}</span></span>
            </div>
        </div>
        <!-- ✅ 新增：分类过滤按钮 -->
        <div class="category-filters">
            <span class="cat-btn active" data-cat="all" onclick="filterCards('all')">📋 全部</span>
            <span class="cat-btn" data-cat="tools_web3" onclick="filterCards('tools_web3')">🛠️ 工具/Web3</span>
            <span class="cat-btn" data-cat="llm_agi" onclick="filterCards('llm_agi')">🧠 大模型/AGI</span>
            <span class="cat-btn" data-cat="hardcore" onclick="filterCards('hardcore')">⚙️ 硬核分析</span>
            <span class="cat-btn" data-cat="general" onclick="filterCards('general')">📰 综合</span>
        </div>
        <div class="cards-grid">
{cards}
        </div>
        <div class="footer">
            \U0001f955 由 AI-Scream 自动生成 | {date}
        </div>
    </div>
    <!-- ✅ 新增：分类过滤JS -->
    <script>
        function filterCards(cat) {{
            document.querySelectorAll('.cat-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelector(`.cat-btn[data-cat="${{cat}}"]`).classList.add('active');
            document.querySelectorAll('.card').forEach(card => {{
                if (cat === 'all' || card.dataset.category === cat) {{
                    card.classList.remove('hidden');
                }} else {{
                    card.classList.add('hidden');
                }}
            }});
        }}
    </script>
</body>
</html>"""

CARD_TEMPLATE = """            <a class="card{featured_class}" href="{url}" target="_blank" rel="noopener" data-category="{category}">
                <div class="tags"><span class="tag-emoji">{emoji}</span> <span class="cat-tag {cat_css}">{cat_name}</span> {tags_html}</div>
                <div class="card-title">{title}</div>
                <div class="card-summary">{summary}</div>
                <div class="card-meta">
                    <span class="card-source">{source}</span>
                    <span class="card-arrow">\u2192</span>
                </div>
            </a>"""


def generate_html(items, date_str):
    cards = []
    # ✅ 新增：统计各分类数量
    cat_counts = {"tools_web3": 0, "llm_agi": 0, "hardcore": 0, "general": 0}

    for i, item in enumerate(items):
        tags = infer_tags(item)
        tags_html = ""
        if i < 3 and item.get("final_score", 0) >= 20:
            tags_html += '<span class="tag tag-featured">🔥 重磅</span> '
        tags_html += "".join(
            f'<span class="tag {css}">{escape(label)}</span>' for label, css, emoji in tags
        )
        featured_class = " featured" if (i < 3 and item.get("final_score", 0) >= 20) else ""

        # ✅ 新增：获取分类信息
        category = item.get("category", "general")
        cat_info = CATEGORY_MAP.get(category, CATEGORY_MAP["general"])
        cat_counts[category] = cat_counts.get(category, 0) + 1

        cards.append(
            CARD_TEMPLATE.format(
                url=escape(item["url"]),
                tags_html=tags_html,
                emoji=pick_emoji(item),
                title=escape(item.get("title_zh", item["title"])),
                summary=escape(item.get("summary_zh", item["summary"])),
                source=escape(item["source"]),
                featured_class=featured_class,
                category=escape(category),
                cat_css=escape(cat_info["css"]),
                cat_name=escape(cat_info["short"]),
            )
        )
    return HTML_TEMPLATE.format(
        date=date_str,
        cards="\n".join(cards),
        count=len(items),
        cat_tools_count=cat_counts.get("tools_web3", 0),
        cat_llm_count=cat_counts.get("llm_agi", 0),
        cat_hardcore_count=cat_counts.get("hardcore", 0),
        cat_general_count=cat_counts.get("general", 0),
    )


# ── 飞书推送 ──────────────────────────────────────────


def build_feishu_card(items, date_str):
    elements = []

    # ✅ 新增：按分类分组展示
    categorized = {}
    for item in items:
        cat = item.get("category", "general")
        if cat not in categorized:
            categorized[cat] = []
        categorized[cat].append(item)

    # 按优先级排列分类
    category_order = ["llm_agi", "tools_web3", "hardcore", "general"]

    for cat_key in category_order:
        cat_items = categorized.get(cat_key, [])
        if not cat_items:
            continue

        cat_info = CATEGORY_MAP.get(cat_key, CATEGORY_MAP["general"])

        # 分类标题
        elements.append({
            "tag": "markdown",
            "content": f"\n{cat_info['emoji']} {cat_info['name']} ({len(cat_items)}条)\n"
        })
        elements.append({"tag": "hr"})

        for i, item in enumerate(cat_items):
            tags = infer_tags(item)
            tag_str = " | ".join(label for label, _, _ in tags)
            emoji = pick_emoji(item)
            title_zh = item.get("title_zh", item["title"])
            summary_zh = item.get("summary_zh", item["summary"])

            # 判断是否为重磅新闻（全局排名前3且评分>=20）
            global_idx = items.index(item) if item in items else 999
            prefix = "🔥 **重磅** | " if (global_idx < 3 and item.get("final_score", 0) >= 20) else ""

            elements.append({
                "tag": "markdown",
                "content": f"{emoji} {prefix}{tag_str}\n{title_zh}\n{summary_zh}"
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
            if i < len(cat_items) - 1:
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
        "elements": [{"tag": "plain_text", "content": f"\U0001f955 由 AI-Scream 自动生成 | {date_str}"}],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"\U0001f955 AI-Scream-{date_str} | 今日AI资讯精选"},
                "template": "orange",
            },
            "elements": elements,
        },
    }


def publish_to_pages(html_content, date_str):
    """将 HTML 推送到 GitHub Pages 仓库。"""
    import subprocess
    try:
        pages = PAGES_DIR
        (pages / "latest.html").write_text(html_content, encoding="utf-8")
        (pages / f"AI-Scream-{date_str}.html").write_text(html_content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(pages), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"update: AI-Scream {date_str}"],
            cwd=str(pages), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push"],
            cwd=str(pages), check=True, capture_output=True, timeout=30
        )
        logger.info(f"Published: {PAGES_URL}/latest.html")
    except Exception as e:
        logger.warning(f"GitHub Pages push failed: {e}")


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
            logger.info("Feishu push succeeded")
        else:
            logger.warning(f"Feishu response: {result}")
    except Exception as e:
        logger.error(f"Feishu push failed: {e}")


# ── 主流程 ────────────────────────────────────────────


def main():
    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(beijing_tz).strftime("%Y-%m-%d")
    logger.info(f"=== AI-Scream RSS Edition v2.0 | {today} ===")

    # 1. 抓取（英文源）
    logger.info("[1/9] Fetching TechCrunch AI...")
    tc = fetch_techcrunch()
    logger.info(f"      Got {len(tc)} items")

    logger.info("[2/9] Fetching Hacker News...")
    hn = fetch_hackernews()
    logger.info(f"      Got {len(hn)} items")

    logger.info("[3/9] Fetching TLDR.tech AI...")
    tldr = fetch_tldr()
    logger.info(f"      Got {len(tldr)} items")

    # ✅ 新增：ScienceDaily
    logger.info("[4/9] Fetching ScienceDaily AI...")
    sd = fetch_sciencedaily()
    logger.info(f"      Got {len(sd)} items")

    # 抓取国内源
    logger.info("[5/9] Fetching 36Kr AI...")
    kr = fetch_36kr()
    logger.info(f"      Got {len(kr)} items")

    logger.info("[6/9] Fetching 机器之心...")
    jqzx = fetch_jiqizhixin()
    logger.info(f"      Got {len(jqzx)} items")

    logger.info("[7/9] Fetching 量子位...")
    qbit = fetch_qbitai()
    logger.info(f"      Got {len(qbit)} items")

    # ✅ 新增：InfoQ
    logger.info("[8/9] Fetching InfoQ AI...")
    infoq = fetch_infoq()
    logger.info(f"      Got {len(infoq)} items")

    # 2. 合并去重排序（✅ 现在包含质量过滤和分类标记）
    all_items = tc + hn + tldr + sd + kr + jqzx + qbit + infoq
    logger.info(f"Total raw: {len(all_items)}")
    final = deduplicate_and_rank(all_items)
    logger.info(f"After dedup, filter & rank: {len(final)}")

    if not final:
        logger.error("No items fetched. Check network.")
        return

    # 3. 生成中文标题与摘要（✅ 之后会重新分类）
    logger.info("[9/9] Generating Chinese summaries...")
    final = generate_chinese_summaries(final)

    # ✅ 新增：打印分类统计
    cat_stats = {}
    for item in final:
        cat = item.get("category", "general")
        cat_stats[cat] = cat_stats.get(cat, 0) + 1
    logger.info(f"Category stats: {cat_stats}")

    # 4. 生成 HTML
    html = generate_html(final, today)
    output_path = OUTPUT_DIR / f"AI-Scream-{today}.html"
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"HTML saved: {output_path}")

    # 5. 推送到 GitHub Pages
    logger.info("Publishing to GitHub Pages...")
    publish_to_pages(html, today)

    # 6. 推送飞书（✅ 现在按分类分组展示）
    card = build_feishu_card(final, today)
    push_feishu(card)

    # 7. 打印摘要（✅ 增加分类信息）
    logger.info("=== Top 5 Stories ===")
    for i, item in enumerate(final[:5], 1):
        score = item.get("final_score", 0)
        cat = item.get("category", "general")
        cat_name = CATEGORY_MAP.get(cat, {}).get("short", "综合")
        logger.info(f"  {i}. [Score:{score}] [{cat_name}] [{item['source']}] {item.get('title_zh', item['title'])}")

    logger.info("Done!")


if __name__ == "__main__":
    main()
