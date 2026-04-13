"""
AI-Scream 纯 Python 版 —— 每日 AI 资讯抓取、HTML 生成与飞书推送脚本。
数据源: TechCrunch AI RSS, Hacker News API, TLDR.tech AI 归档页,
        36Kr AI频道, 机器之心, 量子位, InfoQ AI, ScienceDaily AI。
摘要生成: 本地 Ollama + Qwen3 14B，零成本无限制。

v2.3 更新（Ollama Qwen3 版）：
  - 重构：将 Gemini API 替换为本地 Ollama + Qwen3 14B，零成本无限制
  - 新增：Qwen3 /no_think 指令，禁用思考模式加速推理
  - 新增：<think> 标签清理，双重保险
  - 新增：Ollama JSON 模式（format: json），输出更稳定
  - 修复：TLDR.tech 正则灾难性回溯问题
  - 保留：全部 prompt 人设、评分、分类、飞书卡片逻辑不变
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
FEISHU_WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/30bd0594-8318-4475-9f34-e0ed5a65de00",
)

# 🔄 CHANGED: Ollama + Qwen3 14B 配置（替换 Gemini）
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")

OUTPUT_DIR = Path.home()
PAGES_DIR = Path.home() / "ai-scream-pages"
PAGES_URL = "https://twinkleshinya.github.io/ai-scream-pages"
MAX_ITEMS = 20
HN_TOP_N = 30

AI_KEYWORDS = re.compile(
    r"artificial.intelligence|machine.learning|deep.learning"
    r"|llm|large.language|gpt.?[3-5]|openai|claude|anthropic|gemini|mistral"
    r"|chatgpt|diffusion|neural.net|transformer|generative.ai"
    r"|langchain|hugging.?face|stable.diffusion|midjourney"
    r"|ai.agent|ai.model|foundation.model|reasoning.model"
    r"|ai.chip|ai.video|ai.startup|ai.fund|ai.regul|ai.safety"
    r"|sora|dall.?e|copilot.ai|cursor.ai|ai.coding"
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
    r"|Web3|区块链|元宇宙|智能合约|去中心化|加密货币"
    r"|以太坊|零知识证明|数字藏品|链上|代币经济"
    r"|具身智能|人形机器人|机器人|机械臂|传感器融合"
    r"|AI绘画|AI证件照|AI写真|AI办公|AI工具|AI教程"
    r"|AI配音|AI翻译|AI助手|AI插件",
    re.IGNORECASE,
)

WEB3_KEYWORDS = re.compile(
    r"Web3|区块链|blockchain|元宇宙|metaverse|DAO(?!\w)|智能合约"
    r"|smart.contract|NFT|DeFi|去中心化|加密货币|以太坊|Ethereum"
    r"|Solana|Layer.?2|零知识证明|ZK.proof|代币|token(?:iz|omics)"
    r"|数字藏品|链上|dApp|跨链|侧链|共识机制|质押|挖矿",
    re.IGNORECASE,
)

CLICKBAIT_FILTER = re.compile(
    r"震惊|快看|这竟然|千万别买|后悔了|速看|不转不是|99%的人"
    r"|惊呆了|太疯狂|细思极恐|重大信号|暴涨|暴跌"
    r"|你绝对想不到|赶紧收藏|最后一天|限时免费|必看"
    r"|clickbait|you.won.?t.believe|shocking|insane",
    re.IGNORECASE,
)

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
    r"|发布|上线|收购|合并|破产|关停|禁令"
    r"|breaking|exclusive|launch|acquire|shutdown"
    r"|leaked|open.?source|release",
    re.IGNORECASE,
)

FINANCING_KEYWORDS = re.compile(
    r"融资|IPO|估值|投资轮|天使轮|A轮|B轮|C轮|战略融资|亿元融资|万美元"
    r"|fund.?rais|series.[a-z]|valuat|invest.*round|\$\d+.*million|\$\d+.*billion"
    r"|raised.*million|raised.*billion",
    re.IGNORECASE,
)

SOURCE_AUTHORITY_SCORE = {
    # 🟢 国内科技媒体（可信 + 有解读）
    "36Kr": 14,
    "虎嗅": 13,
    "钛媒体": 12,
    "机器之心": 13,
    "量子位": 11,
    "InfoQ": 10,

    # 🟡 国际科技媒体（产品导向）
    "TechCrunch": 13,
    "The Verge": 13,
    "The Information": 14,

    # 🟢 官方 / 一手信息（优先级最高）
    "OpenAI Blog": 18,
    "Google Blog": 17,
    "Microsoft Blog": 17,
    "Anthropic Blog": 17,
    "Apple Newsroom": 17,

    # 🔥 社交/热点信号（你这个项目的关键！）
    "微博热搜": 16,
    "知乎热榜": 13,
    "B站热门": 12,
    "X (Twitter)": 14,
    "Reddit": 10,

    # 🧪 技术社区（降权，不然会太"工程味"）
    "Hacker News": 6,
    "GitHub Trending": 8,

    # 🧾 聚合类（辅助）
    "TLDR.tech": 7,
    "ScienceDaily": 6,

    # 📱 产品生态（非常重要，贴近生活）
    "App Store 更新": 15,
    "Play Store 更新": 14,
}

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
    """TLDR.tech AI — 爬取最新一期归档页，提取标题+摘要段落。"""
    items = []
    try:
        resp = requests.get("https://tldr.tech/ai/archives", timeout=10)
        resp.raise_for_status()
        dates = re.findall(r"/ai/(\d{4}-\d{2}-\d{2})", resp.text)
        if not dates:
            logger.warning("TLDR: No archive dates found")
            return items
        latest = sorted(dates, reverse=True)[0]
        detail = requests.get(f"https://tldr.tech/ai/{latest}", timeout=10)
        detail.raise_for_status()
        html = detail.text

        # ✅ 修复：分两步匹配，避免嵌套量词导致灾难性回溯
        link_pattern = re.compile(
            r'<a[^>]+href="(https?://(?!tldr\.tech)[^"]+)"[^>]*>\s*([^<]{15,}?)\s*</a>',
            re.DOTALL,
        )
        p_pattern = re.compile(r'<p[^>]*>([^<]{30,})</p>', re.DOTALL)

        links = list(link_pattern.finditer(html))
        paragraphs = list(p_pattern.finditer(html))

        seen = set()
        for link_match in links:
            url = link_match.group(1).strip()
            title = link_match.group(2).strip()
            link_end = link_match.end()

            summary = ""
            for p_match in paragraphs:
                if p_match.start() > link_end and (p_match.start() - link_end) < 500:
                    summary = re.sub(r'\s+', ' ', p_match.group(1)).strip()
                    break

            if (
                url not in seen
                and len(title) > 10
                and "advertiser" not in url.lower()
                and "sponsor" not in title.lower()
            ):
                seen.add(url)
                items.append({
                    "title": title,
                    "url": url,
                    "summary": summary[:300],
                    "source": "TLDR.tech",
                    "date": latest,
                    "score": 0,
                })

        if not items:
            for link_match in links:
                url = link_match.group(1).strip()
                title = link_match.group(2).strip()
                if (
                    url not in seen
                    and len(title) > 15
                    and "advertiser" not in url.lower()
                    and "sponsor" not in title.lower()
                ):
                    seen.add(url)
                    items.append({
                        "title": title,
                        "url": url,
                        "summary": "",
                        "source": "TLDR.tech",
                        "date": latest,
                        "score": 0,
                    })

    except requests.exceptions.Timeout:
        logger.warning("TLDR fetch timed out (10s)")
    except requests.exceptions.RequestException as e:
        logger.warning(f"TLDR fetch network error: {e}")
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
            if not articles:
                articles = re.findall(
                    r'<a[^>]+href="(/p/(\d+))"[^>]*>\s*<[^>]*>([^<]{10,})',
                    resp2.text,
                )
                articles = [(a[0], a[2]) for a in articles]
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
                        "summary": "",
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
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            resp = requests.get("https://www.jiqizhixin.com/", headers=headers, timeout=15)
            if resp.status_code == 200:
                articles = re.findall(
                    r'<a[^>]+href="(https?://www\.jiqizhixin\.com/articles/[^"]+)"[^>]*>\s*([^<]{10,})',
                    resp.text,
                )
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
                        "summary": "",
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
            json={"type": 1, "size": 15, "id": 33},
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
                        "summary": summary or "",
                        "source": "InfoQ",
                        "date": article.get("publish_time", ""),
                        "score": 0,
                        "is_chinese": True,
                    })
    except Exception as e:
        logger.warning(f"InfoQ fetch failed: {e}")
    return items


# ── 标题质量过滤 ─────────────────────────────────────


def filter_low_quality(items):
    """过滤标题党、营销号等低质量内容。"""
    filtered = []
    removed_count = 0
    for item in items:
        title = item.get("title", "")
        summary = item.get("summary", "")
        text = f"{title} {summary}"

        if CLICKBAIT_FILTER.search(title):
            logger.debug(f"Filtered (clickbait): {title}")
            removed_count += 1
            continue

        if SPAM_FILTER.search(text):
            logger.debug(f"Filtered (spam): {title}")
            removed_count += 1
            continue

        if len(title.strip()) < 5:
            logger.debug(f"Filtered (too short): {title}")
            removed_count += 1
            continue

        filtered.append(item)

    if removed_count > 0:
        logger.info(f"Quality filter removed {removed_count} low-quality items")
    return filtered


# ── 去重与排序 ─────────────────────────────────────────


def _title_tokens(title: str) -> set:
    """将标题切分为词集合，用于语义相似度去重。"""
    words = re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fa5]+", title.lower())
    return set(words)


def _is_similar_title(title_a: str, title_b: str, threshold: float = 0.55) -> bool:
    """Jaccard 相似度判断两个标题是否语义重复。"""
    ta = _title_tokens(title_a)
    tb = _title_tokens(title_b)
    if not ta or not tb:
        return False
    intersection = len(ta & tb)
    union = len(ta | tb)
    return (intersection / union) >= threshold


def deduplicate_and_rank(all_items):
    """URL 去重 + 语义标题去重 + 质量过滤 + 融资限流 + 多维度综合评分排序。"""
    all_items = filter_low_quality(all_items)

    seen_urls = set()
    url_unique = []
    for item in all_items:
        url = item["url"].rstrip("/")
        if url not in seen_urls and item["title"]:
            seen_urls.add(url)
            item["final_score"] = calculate_importance_score(item)
            item["category"] = classify_content(item)
            url_unique.append(item)

    url_unique.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    unique = []
    for item in url_unique:
        is_dup = any(
            _is_similar_title(item["title"], kept["title"])
            for kept in unique
        )
        if not is_dup:
            unique.append(item)

    MAX_FINANCING = 2
    financing_count = 0
    final = []
    for item in unique:
        title = item.get("title", "") + " " + item.get("summary", "")
        is_financing = bool(FINANCING_KEYWORDS.search(title))
        if is_financing:
            if financing_count >= MAX_FINANCING:
                logger.debug(f"Financing cap hit, skipping: {item['title']}")
                continue
            financing_count += 1
        final.append(item)

    logger.info(f"After dedup: {len(url_unique)} URL-unique → {len(unique)} title-unique → {len(final)} after financing cap")
    return final[:MAX_ITEMS]


def calculate_importance_score(item):
    """多维度综合评分算法。"""
    score = 0
    title = item.get("title", "")
    summary = item.get("summary", "")
    text = f"{title} {summary}"
    source = item.get("source", "")

    score += SOURCE_AUTHORITY_SCORE.get(source, 0)

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

    breaking_matches = BREAKING_KEYWORDS.findall(text)
    score += min(len(breaking_matches) * 5, 15)

    if FINANCING_KEYWORDS.search(text):
        score -= 5

    major_entities = re.compile(
        r"OpenAI|Google|Microsoft|Meta|Apple|Nvidia|英伟达|谷歌|微软"
        r"|GPT-?[4-5]|Claude|Gemini|Llama|DeepSeek|Sora"
        r"|百度|字节|阿里|腾讯|华为",
        re.IGNORECASE,
    )
    entity_matches = major_entities.findall(text)
    score += min(len(set(entity_matches)) * 3, 12)

    title_len = len(title)
    if 15 <= title_len <= 60:
        score += 3

    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(beijing_tz).strftime("%Y-%m-%d")
    if today in item.get("date", ""):
        score += 5

    factual_pattern = re.compile(
        r"\d+[BMK]\s*参数|\d+%|v\d+\.\d+|\$\d+|billion|million"
        r"|\d+亿|\d+万|\d+\.?\d*[TB]|benchmark|SOTA|评测",
        re.IGNORECASE,
    )
    if factual_pattern.search(text):
        score += 4

    if WEB3_KEYWORDS.search(text):
        score += 3

    tools_pattern = re.compile(
        r"AI绘画|AI证件照|AI写真|AI办公|AI工具|AI教程|AI配音|ComfyUI|LoRA|ControlNet"
        r"|Midjourney|Stable.Diffusion|实操|教程|上手|指南|测评|插件"
        r"|Web3|区块链|blockchain|元宇宙|智能合约|DAO(?!\w)|DeFi|NFT|代币|链上",
        re.IGNORECASE,
    )
    if tools_pattern.search(text):
        score += 6

    llm_hot_pattern = re.compile(
        r"LLM|大语言模型|大模型|DeepSeek|OpenAI|Claude|Gemini|Kimi|通义|文心"
        r"|API降价|开源|参数|token|benchmark|SOTA|能力突破|视频生成|图像生成"
        r"|AI监管|AI政策|禁令|竞争|巨头|AGI|通用人工智能",
        re.IGNORECASE,
    )
    if llm_hot_pattern.search(text):
        score += 5

    hardcore_pattern = re.compile(
        r"AI芯片|GPU|英伟达|NVIDIA|AMD|昇腾|华为算力|TPU|算力"
        r"|具身智能|人形机器人|机械臂|传感器融合|自动驾驶"
        r"|Transformer|Mamba|注意力机制|扩散模型|MoE|混合专家|量化|蒸馏|微调"
        r"|原理|拆解|深度分析|科普|数据中心|inference|latency|throughput",
        re.IGNORECASE,
    )
    if hardcore_pattern.search(text):
        score += 5

    lifestyle_pattern = re.compile(
        r"宕机|崩了|崩溃|翻车|Bug|bug|故障|下线|限流|封号|被封"
        r"|上线|发布|更新|新功能|新版本|内测|公测|偷跑|灰度"
        r"|裁员|替代|失业|打工人|岗位|白领|工作流"
        r"|手机|App|应用|集成|接入|内置|插件"
        r"|热搜|出圈|全网|爆火|刷屏|讨论"
        r"|免费|收费|降价|涨价|付费|会员"
        r"|合作|收购|对抗|竞争|封杀|禁令",
        re.IGNORECASE,
    )
    if lifestyle_pattern.search(text):
        score += 6

    return score


# ══════════════════════════════════════════════════════
# 🔄 CHANGED: Ollama + Qwen3 14B 调用（替换 Gemini）
# ══════════════════════════════════════════════════════


# 🔄 CHANGED: 用于清理 Qwen3 思考标签的正则（模块级编译，避免重复编译）
_THINK_TAG_RE = re.compile(r'<think>.*?</think>', re.DOTALL)


def generate_chinese_summaries(items):
    """用 Ollama + Qwen3 为每条资讯生成编辑式中文标题和摘要。"""
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

    for item in all_items:
        item["category"] = classify_content(item)

    return all_items


# 🔄 CHANGED: 完整替换 _call_gemini_single → _call_ollama_single
def _call_ollama_single(prompt: str, timeout: int = 120) -> str:
    """调用本地 Ollama + Qwen3 14B，返回原始文本，失败时返回空字符串。"""
    # ✅ 自动追加 /no_think 指令，禁用 Qwen3 思考模式以加速推理
    if "/no_think" not in prompt:
        prompt = prompt + "\n/no_think"

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",       # ✅ Ollama JSON 模式，输出更稳定
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 1024,
                        "num_ctx": 4096,    # ✅ 明确上下文窗口
                    },
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()

            # ✅ 双重保险：清理 Qwen3 可能残留的思考标签
            raw = _THINK_TAG_RE.sub('', raw).strip()

            if raw:
                return raw

            logger.warning(f"Ollama returned empty response, attempt {attempt + 1}/{max_retries + 1}")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return ""

        except requests.exceptions.Timeout:
            logger.warning(f"Ollama call timed out ({timeout}s), attempt {attempt + 1}/{max_retries + 1}")
            if attempt < max_retries:
                time.sleep(3)
                continue
            return ""
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Ollama connection error (is Ollama running?): {e}")
            if attempt < max_retries:
                time.sleep(5)
                continue
            return ""
        except Exception as e:
            logger.warning(f"Ollama call failed: {e}")
            return ""

    return ""


def _parse_single_json(text: str) -> dict:
    """从模型输出中提取单条 JSON 对象，返回 dict 或 {}。"""
    # 先尝试直接解析整段文本
    try:
        return json.loads(text)
    except Exception:
        pass
    # 降级：提取 {...}
    m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {}


# ══════════════════════════════════════════════════════
# Prompt 系统：「AI行业生活化资讯编辑」风格（保持不变）
# ══════════════════════════════════════════════════════

_EDITOR_PERSONA = """\
你是「AI行业生活化资讯编辑」，专门把AI新闻翻译成普通人能感知的变化。

【内容优先级（从高到低）】
✅ 优先写：
- AI产品上线/更新/崩溃/Bug/宕机（直接影响用户）
- AI进入手机/电脑/App（真实使用场景）
- AI对打工人/行业的影响（替代、提效、裁员）
- 爆火话题（热搜、出圈讨论）
- 大厂产品发布（可用的，不是纯demo）
- 公司之间的合作/对抗/翻车

❌ 少写或过滤掉：
- 纯融资金额/估值（除非规模大到影响行业格局，如百亿美元以上）
- 纯学术论文（除非直接推出了可用产品）
- 枯燥的技术参数（除非参数本身就是新闻，如"比上代快10倍"）

【写作风格】
- 标题要有情绪或冲突感：崩了 / 偷跑 / 反转 / 暴涨 / 直接炸了
- 不超过2-3行，信息短密，可读性强
- 像朋友圈科技博主发帖，不像新闻稿
- 口语化：打工人受影响、直接整合进去、用不了了
\
"""

_ITEM_PROMPT_ZH = """\
{persona}

【当前任务】处理这条中文AI资讯，判断是否值得推送，并生成飞书早报格式。

来源：{source}
原始标题：{title}
{summary_line}

【过滤标准】满足以下任一 → ai_related=false，直接过滤：
1. 纯融资/估值/投资轮，无产品/功能/影响内容
2. 纯学术论文，无可用产品
3. 标题党词汇（震惊/速看/颠覆世界等）
4. 与AI/机器人/Web3无直接关联

【输出】只输出一个JSON，不要任何其他内容：
{{"ai_related":true,"emoji":"🤖","hook":"标题（8-18字，有情绪/冲突感，忠于原文）","bullets":["子弹点1（10-20字，一个独立事实）","子弹点2","子弹点3"],"region":"CN"}}

region规则：国内公司/政策/事件→CN，否则→INTL

【风格示例】
好hook：「DeepSeek史诗级宕机」「苹果国行AI偷跑又被撤」「打工人注意：AI开始替代这个岗位」
差hook：「某公司完成A轮融资」「新论文提出新方法」「震惊！AI再次突破」

好bullets：["宕机长达13小时，一夜崩上热搜","用户吐槽：根本用不了","官方回应：正在紧急修复"]
差bullets：["公司完成融资","投资方包括xx","估值达到xx亿"]\
"""

_ITEM_PROMPT_EN = """\
{persona}

【当前任务】处理这条英文AI资讯，判断是否值得推送，并生成中文飞书早报格式。

来源：{source}
原始英文标题：{title}
{summary_line}

【过滤标准】满足以下任一 → ai_related=false：
1. 纯融资/估值，无产品/影响内容
2. 纯学术论文，无可用产品
3. 与AI/机器人/Web3无直接关联的普通科技新闻

【输出】只输出一个JSON，hook和bullets必须是中文，不要其他内容：
{{"ai_related":true,"emoji":"🤖","hook":"中文标题（8-18字，有情绪/冲突感，忠于原文事实）","bullets":["子弹点1（10-20字中文）","子弹点2","子弹点3"],"region":"INTL"}}

region规则：涉及中国公司/政策/事件→CN，否则→INTL

【风格示例】
好hook：「Node.js集体请愿：禁止AI写代码」「Claude造15KB引擎，文字里能跑马里奥」「摩根士丹利裁员2500人，AI替代12人团队」
差hook：「OpenAI完成新一轮融资」「研究人员发现新方法」

好bullets：["1.9万行Claude Code引发百人联名封杀","Node.js核心成员主导请愿","开源社区讨论是否该全面禁用AI代码"]
差bullets：["融资金额达到x亿","投资方包括xx"]\
"""


def _process_item(item: dict, is_chinese: bool) -> dict | None:
    """用 Ollama + Qwen3 逐条处理单条资讯，返回处理后的 item 或 None（过滤掉）。"""
    title = item["title"]
    summary = item.get("summary", "")
    source = item["source"]
    has_real_summary = bool(summary) and len(summary) > 20

    summary_line = f"原始摘要：{summary}" if has_real_summary else "（无额外摘要，仅凭标题判断）"
    template = _ITEM_PROMPT_ZH if is_chinese else _ITEM_PROMPT_EN
    prompt = template.format(
        persona=_EDITOR_PERSONA,
        source=source,
        title=title,
        summary_line=summary_line,
    )

    # 🔄 CHANGED: 调用 Ollama 替代 Gemini
    text_out = _call_ollama_single(prompt, timeout=120)

    # 🔄 CHANGED: 本地模型无需速率控制，删除 GEMINI_RPM_DELAY 等待

    result = _parse_single_json(text_out)

    if not result:
        item.setdefault("hook", title)
        item.setdefault("bullets", [summary[:60]] if has_real_summary else [])
        item.setdefault("title_zh", title)
        item.setdefault("summary_zh", summary if has_real_summary else title)
        item.setdefault("region", "CN" if item.get("is_chinese") else "INTL")
        return item

    if not result.get("ai_related", True):
        logger.debug(f"Filtered (not AI/lifestyle): {title}")
        return None

    item["hook"] = result.get("hook") or title
    item["bullets"] = result.get("bullets") or []
    item["emoji_override"] = result.get("emoji", "")
    item["region"] = result.get("region", "CN" if is_chinese else "INTL")
    item["title_zh"] = item["hook"]
    item["summary_zh"] = "\n".join(f"• {b}" for b in item["bullets"]) if item["bullets"] else (summary if has_real_summary else title)
    return item


def _summarize_chinese_items(items):
    """逐条处理中文资讯。"""
    processed = []
    for item in items:
        result = _process_item(item, is_chinese=True)
        if result is not None:
            processed.append(result)
    logger.info(f"Chinese: {len(items)} → {len(processed)}")
    return processed


def _summarize_english_items(items):
    """逐条处理英文资讯（翻译+摘要）。"""
    processed = []
    for item in items:
        result = _process_item(item, is_chinese=False)
        if result is not None:
            processed.append(result)
    logger.info(f"English: {len(items)} → {len(processed)}")
    return processed


def _generate_daily_observation(items: list, date_str: str) -> str:
    """用 Ollama + Qwen3 生成「今日观察」3条总结，必须有具体观点。"""
    hooks = "\n".join(
        f"- [{item.get('region','?')}] {item.get('hook', item.get('title_zh', item['title']))}"
        for item in items[:12]
    )
    prompt = f"""\
你是AI行业观察者，风格犀利，有观点，不说废话。

今日({date_str})AI热点：
{hooks}

生成「今日观察」3条总结。要求：
- 每条必须有具体判断，不能是"AI正在发展"这种废话
- 可以用口语，可以有情绪
- 国内/国际各一条趋势，加一条你自己的一句话判断
- 国内趋势从CN条目归纳，国际从INTL条目归纳
- "一句话判断"要像科技博主发表看法，有观点有态度

只输出JSON，不要其他内容：
{{"cn":"国内趋势（15-25字，有具体判断）","intl":"国际趋势（15-25字，有具体判断）","verdict":"一句话判断（15-25字，必须有观点/态度）"}}

示例输出：
{{"cn":"国内大模型开始进入稳定性比拼，宕机成新用户流失导火索","intl":"AI Agent从概念走向落地，白领裁员潮真的来了","verdict":"AI正在替代的不是程序员，是那些只会执行却不会思考的人"}}\
"""
    # 🔄 CHANGED: 调用 Ollama 替代 Gemini
    text_out = _call_ollama_single(prompt, timeout=120)
    result = _parse_single_json(text_out)
    if result:
        cn = result.get("cn", "")
        intl = result.get("intl", "")
        verdict = result.get("verdict", "")
        parts = []
        if cn:
            parts.append(f"• 国内：{cn}")
        if intl:
            parts.append(f"• 国际：{intl}")
        if verdict:
            parts.append(f"• 趋势：{verdict}")
        return "\n".join(parts)
    return ""


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
    (re.compile(r"Web3|区块链|blockchain|DAO|智能合约|NFT|DeFi|元宇宙|去中心化|代币", re.I), "Web3", "tag-web3", "\U0001f517"),
    (re.compile(r"具身智能|机器人|robot|humanoid|自动驾驶|autonomous|机械臂", re.I), "机器人", "tag-robot", "\U0001f916"),
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
        .tag-web3 {{ background: #fef3c7; color: #92400e; }}
        .tag-robot {{ background: #dbeafe; color: #1e40af; }}
        .tag-tools {{ background: #ecfdf5; color: #065f46; }}
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
    """飞书卡片结构。"""
    NUMBER_EMOJIS = ["1","2","3","4","5","6","7","8","9","10"]

    cn_items   = [it for it in items if it.get("region") == "CN"][:6]
    intl_items = [it for it in items if it.get("region") != "CN"][:4]

    elements = []

    def _add_section(label: str, section_items: list):
        if not section_items:
            return
        elements.append({"tag": "markdown", "content": f"{label}"})
        elements.append({"tag": "hr"})

        for idx, item in enumerate(section_items):
            num = NUMBER_EMOJIS[idx] if idx < len(NUMBER_EMOJIS) else f"{idx+1}."
            emoji = item.get("emoji_override") or pick_emoji(item)
            hook  = item.get("hook") or item.get("title_zh") or item["title"]
            bullets = item.get("bullets") or []

            lines = [f"{num} {emoji} {hook}"]
            for b in bullets[:3]:
                lines.append(f"• {b}")

            elements.append({"tag": "markdown", "content": "\n".join(lines)})
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看原文 →"},
                    "type": "primary",
                    "url": item["url"],
                }],
            })
            if idx < len(section_items) - 1:
                elements.append({"tag": "hr"})

    _add_section("🇨🇳 国内热点", cn_items)

    if cn_items and intl_items:
        elements.append({"tag": "hr"})

    _add_section("🌍 国际动态", intl_items)

    observation = _generate_daily_observation(items, date_str)
    if observation:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": f"**💡 今日观察**\n{observation}"
        })

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🥕 查看完整网页版"},
            "type": "default",
            "url": f"{PAGES_URL}/latest.html",
        }],
    })
    # 🔄 CHANGED: 更新 footer 文字
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"🥕 AI-Scream 自动生成 | {date_str} | Powered by Ollama + Qwen3"}],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🥕 AI早报 {date_str} | 今日热点"},
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
            logger.info("Feishu push succeeded ✅")
        else:
            logger.warning(f"Feishu response: {result}")
    except Exception as e:
        logger.error(f"Feishu push failed: {e}")


# ── 主流程 ────────────────────────────────────────────


def main():
    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(beijing_tz).strftime("%Y-%m-%d")
    # 🔄 CHANGED: 更新版本号
    logger.info(f"=== AI-Scream Ollama Qwen3 Edition v2.3 | {today} ===")

    # 🔄 CHANGED: 启动时检查 Ollama 连接
    try:
        ollama_check = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        ollama_check.raise_for_status()
        models = [m["name"] for m in ollama_check.json().get("models", [])]
        logger.info(f"Ollama connected. Available models: {models}")
        if not any(OLLAMA_MODEL in m for m in models):
            logger.warning(f"⚠️ Model '{OLLAMA_MODEL}' not found! Available: {models}")
            logger.warning(f"   Run: ollama pull {OLLAMA_MODEL}")
            return
    except requests.exceptions.ConnectionError:
        logger.error(f"❌ Cannot connect to Ollama at {OLLAMA_HOST}!")
        logger.error("   Make sure Ollama is running: ollama serve")
        return
    except Exception as e:
        logger.warning(f"Ollama check warning: {e} (continuing anyway)")

    logger.info(f"Using model: {OLLAMA_MODEL}")

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

    logger.info("[8/9] Fetching InfoQ AI...")
    infoq = fetch_infoq()
    logger.info(f"      Got {len(infoq)} items")

    # 2. 合并去重排序
    all_items = tc + hn + tldr + sd + kr + jqzx + qbit + infoq
    logger.info(f"Total raw: {len(all_items)}")
    final = deduplicate_and_rank(all_items)
    logger.info(f"After dedup, filter & rank: {len(final)}")

    if not final:
        logger.error("No items fetched. Check network.")
        return

    # 3. 生成中文标题与摘要（🔄 CHANGED: 使用 Ollama + Qwen3）
    logger.info("[9/9] Generating Chinese summaries via Ollama + Qwen3...")
    final = generate_chinese_summaries(final)

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

    # 6. 推送飞书
    card = build_feishu_card(final, today)
    push_feishu(card)

    # 7. 打印摘要
    logger.info("=== Top 5 Stories ===")
    for i, item in enumerate(final[:5], 1):
        score = item.get("final_score", 0)
        cat = item.get("category", "general")
        cat_name = CATEGORY_MAP.get(cat, {}).get("short", "综合")
        logger.info(f"  {i}. [Score:{score}] [{cat_name}] [{item['source']}] {item.get('title_zh', item['title'])}")

    logger.info("Done! ✅")


if __name__ == "__main__":
    main()
