"""
AI-Scream 纯 Python 版 —— 每日 AI 资讯抓取、HTML 生成与飞书推送脚本。
数据源: 覆盖国内外主流 AI 媒体、博客、KOL、投资资讯。
摘要生成: Ollama 本地模型 (Qwen 2.5 7B)，完全免费。
"""

import json
import re
import time
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path

import feedparser
import requests

# ── 配置 ──────────────────────────────────────────────
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/30bd0594-8318-4475-9f34-e0ed5a65de00"
OUTPUT_DIR = Path.home()
PAGES_DIR = Path.home() / "ai-scream-pages"
PAGES_URL = "https://twinkleshinya.github.io/ai-scream-pages"
MAX_ITEMS = 20
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b"

AI_KEYWORDS = re.compile(
    r"artificial.intelligence|machine.learning|deep.learning"
    r"|llm|large.language|gpt.?[3-5o]|openai|claude|anthropic|gemini|mistral"
    r"|chatgpt|diffusion|neural.net|transformer|generative.ai"
    r"|langchain|hugging.?face|stable.diffusion|midjourney"
    r"|ai.agent|ai.model|foundation.model|reasoning.model"
    r"|ai.chip|ai.video|ai.startup|ai.fund|ai.regul|ai.safety"
    r"|sora|dall.?e|copilot.ai|cursor.ai|ai.coding"
    r"|deepseek|qwen|llama|meta.ai|nvidia.ai|gpu.cluster"
    r"|rag|vector.database|embedding|fine.?tun|rlhf|dpo"
    r"|multimodal|vision.language|text.to.image|text.to.video"
    r"|ai.hardware|ai.infra|ai.search|ai.assistant"
    r"|人工智能|大模型|大语言模型|机器学习|深度学习|生成式|智能体",
    re.IGNORECASE,
)

# 过滤掉论文/纯学术内容
PAPER_FILTER = re.compile(
    r"arxiv\.org/abs|preprint|theorem|equation|proof|journal\.of"
    r"|hamilton.jacobi|mathematical.methods|\.pdf$",
    re.IGNORECASE,
)

# 过滤掉跟 AI 无关的误匹配
FALSE_POSITIVE_FILTER = re.compile(
    r"smart.eyeglasses|smart.glasses|philly.courts|apple.watch"
    r"|cryptocurrency|bitcoin|ethereum|blockchain"
    r"|sports.score|weather.forecast|recipe|cooking",
    re.IGNORECASE,
)

# ── RSS 数据源配置 ─────────────────────────────────────
# 每个源: (名称, URL, 分类, 最大条数, 是否需要AI关键词过滤)

RSS_SOURCES = [
    # ===== 媒体资讯（国外） =====
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", "媒体资讯", 15, False),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/", "媒体资讯", 15, False),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/", "媒体资讯", 10, True),
    ("AI News", "https://www.artificialintelligence-news.com/feed/", "媒体资讯", 10, False),
    ("ScienceDaily AI", "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml", "媒体资讯", 10, False),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "媒体资讯", 10, False),
    ("Ars Technica AI", "https://feeds.arstechnica.com/arstechnica/technology-lab", "媒体资讯", 10, True),
    ("WIRED AI", "https://www.wired.com/feed/tag/ai/latest/rss", "媒体资讯", 10, False),

    # ===== 媒体资讯（国内） =====
    ("机器之心", "https://www.jiqizhixin.com/rss", "国内媒体", 15, False),
    ("量子位", "https://www.qbitai.com/feed", "国内媒体", 15, False),

    # ===== AI 投资/商业 =====
    ("Crunchbase News", "https://news.crunchbase.com/feed/", "AI投资", 10, True),

    # ===== 官方博客 =====
    ("OpenAI Blog", "https://openai.com/blog/rss.xml", "官方博客", 10, False),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/", "官方博客", 10, False),
    ("DeepMind Blog", "https://deepmind.google/blog/rss.xml", "官方博客", 10, False),
    ("Meta AI Blog", "https://ai.meta.com/blog/rss/", "官方博客", 10, False),
    ("NVIDIA AI Blog", "https://blogs.nvidia.com/feed/", "官方博客", 10, True),
    ("Microsoft AI Blog", "https://blogs.microsoft.com/ai/feed/", "官方博客", 10, False),
    ("Anthropic Blog", "https://www.anthropic.com/rss.xml", "官方博客", 10, False),

    # ===== HuggingFace =====
    ("HuggingFace Blog", "https://huggingface.co/blog/feed.xml", "开源社区", 10, False),

    # ===== 播客/视频 =====
    ("Practical AI Podcast", "https://changelog.com/practicalai/feed", "播客", 5, False),
    ("Everyday AI Podcast", "https://www.youreverydayai.com/feed/", "播客", 5, False),
    ("Lenny's Podcast", "https://www.lennyspodcast.com/rss/", "播客", 5, True),

    # ===== 独立博客 =====
    ("Lil'Log (Lilian Weng)", "https://lilianweng.github.io/index.xml", "独立博客", 5, False),
]

# ── 通用 RSS 抓取 ─────────────────────────────────────

def fetch_rss_source(name, url, category, max_items, need_ai_filter):
    """通用 RSS 抓取函数。"""
    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            summary = entry.get("summary", entry.get("description", ""))
            # 去掉 HTML 标签
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            if len(summary) > 250:
                summary = summary[:250] + "..."

            combined_text = f"{title} {summary} {link}"

            # 过滤论文/学术内容
            if PAPER_FILTER.search(combined_text):
                continue

            # 过滤误匹配
            if FALSE_POSITIVE_FILTER.search(combined_text):
                continue

            # 如果需要 AI 关键词过滤
            if need_ai_filter and not AI_KEYWORDS.search(combined_text):
                continue

            items.append({
                "title": title,
                "url": link,
                "summary": summary,
                "source": name,
                "category": category,
                "date": entry.get("published", entry.get("updated", "")),
                "score": 0,
            })
    except Exception as e:
        print(f"  [WARN] {name} fetch failed: {e}")
    return items


# ── TLDR.tech AI 专用抓取 ─────────────────────────────

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
                    "source": "TLDR.tech AI",
                    "category": "媒体资讯",
                    "date": latest,
                    "score": 0,
                })
    except Exception as e:
        print(f"  [WARN] TLDR fetch failed: {e}")
    return items


# ── HuggingFace Daily Papers 抓取 ─────────────────────

def fetch_hf_daily_papers():
    """HuggingFace Daily Papers API。"""
    items = []
    try:
        resp = requests.get("https://huggingface.co/api/daily_papers", timeout=10)
        papers = resp.json()
        for paper in papers[:10]:
            title = paper.get("title", "")
            paper_id = paper.get("paper", {}).get("id", "")
            summary = paper.get("paper", {}).get("summary", "")
            if len(summary) > 250:
                summary = summary[:250] + "..."
            upvotes = paper.get("paper", {}).get("upvotes", 0)
            items.append({
                "title": title,
                "url": f"https://huggingface.co/papers/{paper_id}",
                "summary": summary,
                "source": "HF Daily Papers",
                "category": "论文精选",
                "date": paper.get("publishedAt", ""),
                "score": upvotes,
            })
    except Exception as e:
        print(f"  [WARN] HF Daily Papers fetch failed: {e}")
    return items


# ── Twitter/X KOL 信息抓取（通过 Nitter 或 RSS Bridge）───

def fetch_twitter_kol_rss():
    """
    通过公开 RSS 桥接服务获取 AI KOL 推文。
    注意：Twitter 官方不提供免费 RSS，这里使用 nitter 实例或 RSS Bridge。
    如果这些服务不可用，此函数会静默返回空列表。
    """
    kols = [
        ("Sam Altman", "sama", "KOL"),
        ("Elon Musk", "elonmusk", "KOL"),
        ("Yann LeCun", "ylecun", "KOL"),
        ("Andrej Karpathy", "karpathy", "KOL"),
        ("Clem (HuggingFace)", "ClementDelangue", "KOL"),
        ("Mark Chen (OpenAI)", "markchen90", "KOL"),
    ]

    # 尝试多个 nitter 实例
    nitter_instances = [
        "https://nitter.net",
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
    ]

    items = []
    for display_name, handle, category in kols:
        fetched = False
        for instance in nitter_instances:
            if fetched:
                break
            try:
                rss_url = f"{instance}/{handle}/rss"
                feed = feedparser.parse(rss_url)
                for entry in feed.entries[:3]:
                    title = entry.get("title", "").strip()
                    if len(title) > 15 and AI_KEYWORDS.search(title):
                        items.append({
                            "title": f"[{display_name}] {title[:100]}",
                            "url": entry.get("link", ""),
                            "summary": title[:200],
                            "source": f"X/{display_name}",
                            "category": category,
                            "date": entry.get("published", ""),
                            "score": 0,
                        })
                fetched = True
            except Exception:
                continue
    return items


# ── AI 资讯聚合平台抓取 ────────────────────────────────

def fetch_ai_aggregators():
    """
    尝试抓取 AIbase、AI中国 等平台的 RSS（如果可用）。
    这些平台可能没有公开 RSS，如果失败则静默跳过。
    """
    aggregator_feeds = [
        ("AIbase", "https://www.aibase.com/rss", "AI聚合", 10, False),
        ("LMSYS", "https://lmsys.org/blog/feed.xml", "AI聚合", 5, False),
    ]
    items = []
    for name, url, category, max_n, need_filter in aggregator_feeds:
        items.extend(fetch_rss_source(name, url, category, max_n, need_filter))
    return items


# ── AlphaSignal 抓取 ──────────────────────────────────

def fetch_alphasignal():
    """AlphaSignal RSS feed（如果可用）。"""
    items = []
    try:
        feed = feedparser.parse("https://alphasignal.ai/feed")
        for entry in feed.entries[:10]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            summary = re.sub(r"<[^>]+>", "", summary).strip()
            if len(summary) > 250:
                summary = summary[:250] + "..."
            items.append({
                "title": title,
                "url": entry.get("link", ""),
                "summary": summary,
                "source": "AlphaSignal",
                "category": "AI聚合",
                "date": entry.get("published", ""),
                "score": 0,
            })
    except Exception as e:
        print(f"  [WARN] AlphaSignal fetch failed: {e}")
    return items


# ── 去重与排序 ─────────────────────────────────────────

def deduplicate_and_rank(all_items):
    """URL 去重，按分类多样性 + 分数排序。"""
    seen_urls = set()
    seen_titles = set()
    unique = []

    # 优先级排序：官方博客 > 媒体资讯 > 国内媒体 > AI投资 > KOL > 其他
    category_priority = {
        "官方博客": 100,
        "媒体资讯": 90,
        "国内媒体": 85,
        "AI投资": 80,
        "KOL": 75,
        "AI聚合": 70,
        "播客": 65,
        "论文精选": 60,
        "开源社区": 55,
        "独立博客": 50,
    }

    for item in all_items:
        item["_priority"] = category_priority.get(item.get("category", ""), 0) + item.get("score", 0) / 100

    all_items.sort(key=lambda x: x["_priority"], reverse=True)

    for item in all_items:
        url = item["url"].rstrip("/")
        title_key = item["title"][:30].lower()
        if url not in seen_urls and title_key not in seen_titles and item["title"]:
            seen_urls.add(url)
            seen_titles.add(title_key)
            unique.append(item)

    # 确保分类多样性：每个分类最多取 5 条
    category_counts = {}
    diverse_items = []
    for item in unique:
        cat = item.get("category", "其他")
        count = category_counts.get(cat, 0)
        if count < 5:
            diverse_items.append(item)
            category_counts[cat] = count + 1

    return diverse_items[:MAX_ITEMS]


# ── 二次 AI 相关性过滤 ─────────────────────────────────

def strict_ai_filter(items):
    """对所有抓取结果进行二次严格的 AI 相关性过滤。"""
    filtered = []
    for item in items:
        combined = f"{item['title']} {item['summary']} {item['url']}"
        # 必须包含 AI 关键词
        if not AI_KEYWORDS.search(combined):
            continue
        # 不能是论文
        if PAPER_FILTER.search(combined):
            continue
        # 不能是误匹配
        if FALSE_POSITIVE_FILTER.search(combined):
            continue
        filtered.append(item)
    return filtered


# ── Ollama 生成中文标题与摘要 ─────────────────────────

def generate_chinese_summaries(items):
    """用 Ollama 本地模型为每条资讯生成编辑式中文标题和摘要。"""

    news_list = ""
    for i, item in enumerate(items):
        news_list += f"[{i+1}] {item['title']} | {item['summary'][:150]} | 来源:{item['source']} | 分类:{item.get('category', '')}\n"

    prompt = f"""你是资深AI行业记者。将以下{len(items)}条资讯转化为中文精华版。

重要：先判断每条是否真正与AI/人工智能直接相关。
- 直接相关：AI模型发布、AI公司融资/动态、AI产品、AI政策、AI芯片、AI应用、AI研究突破等
- 不相关：普通科技新闻、非AI的融资、一般软件工具、纯硬件（非AI芯片）、GitHub工具集合等

每条必须包含以下字段：
- ai_related: true或false
- emoji: 一个贴切的emoji
- title_zh: 中文标题（必须是中文！15-25字，像新闻编辑写的标题，不要直译）
- summary_zh: 中文摘要（必须是中文！50-100字，解释这件事为什么重要，有洞察力）

好标题：「OpenAI 关停 Sora：日烧百万美元，用户不到50万」
好摘要：「据华尔街日报调查，Sora 上线仅半年，全球用户从百万骤降至不足50万，每日运营成本高达100万美元。这揭示了AI视频生成领域叫好不叫座的残酷现实。」

注意：title_zh和summary_zh都必须是中文，绝对不能是英文！
如果原文已经是中文，则进行润色和精简即可。

只输出JSON数组，不要其他任何内容：
[{{"id":1,"ai_related":true,"emoji":"🤖","title_zh":"中文标题","summary_zh":"中文摘要50到100字"}}]

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
    return items


def _fallback_titles(items):
    """Ollama 失败时的降级方案：直接用原文。"""
    for item in items:
        item["title_zh"] = item["title"]
        item["summary_zh"] = item["summary"]


# ── 标签推断 ──────────────────────────────────────────

TAG_RULES = [
    (re.compile(r"llm|gpt|claude|gemini|model|mistral|anthropic|openai|大模型|语言模型", re.I), "大模型", "tag-llm", "\U0001f916"),
    (re.compile(r"fund|rais|invest|ipo|valuat|\$\d|billion|million|serie|融资|投资", re.I), "融资", "tag-biz", "\U0001f4b0"),
    (re.compile(r"open.?source|github|hugging|apache|mit.license|开源", re.I), "开源", "tag-open", "\U0001f331"),
    (re.compile(r"regulat|policy|govern|law|eu.ai|congress|senate|ban|court|监管|政策", re.I), "政策", "tag-policy", "\U0001f3db"),
    (re.compile(r"launch|releas|announc|introduc|new.feature|product|发布|产品", re.I), "产品", "tag-product", "\U0001f680"),
    (re.compile(r"research|study|scientif|danger|risk|warning|研究|突破", re.I), "研究", "tag-research", "\U0001f52c"),
    (re.compile(r"secur|privacy|hack|exploit|vulnerab|data.collect|track|安全", re.I), "安全", "tag-policy", "\U0001f512"),
    (re.compile(r"chip|gpu|nvidia|hardware|data.center|infra|芯片|算力", re.I), "基础设施", "tag-other", "\u2699\ufe0f"),
    (re.compile(r"video|image|generat|sora|diffusion|creative|视频|图像|生成", re.I), "创作", "tag-product", "\U0001f3a8"),
    (re.compile(r"agent|autonom|coding.agent|智能体", re.I), "Agent", "tag-llm", "\U0001f9e0"),
    (re.compile(r"podcast|播客|节目", re.I), "播客", "tag-other", "\U0001f399"),
]

SOURCE_EMOJI = {
    "TechCrunch AI": "\U0001f4f0",
    "TLDR.tech AI": "\U0001f4e8",
    "VentureBeat AI": "\U0001f4ca",
    "MIT Technology Review": "\U0001f393",
    "机器之心": "\U0001f1e8\U0001f1f3",
    "量子位": "\U0001f1e8\U0001f1f3",
    "OpenAI Blog": "\U0001f7e2",
    "Google AI Blog": "\U0001f535",
    "DeepMind Blog": "\U0001f9e0",
    "Meta AI Blog": "\U0001f7e6",
    "NVIDIA AI Blog": "\U0001f7e9",
    "Microsoft AI Blog": "\U0001f7e6",
    "Anthropic Blog": "\U0001f7e0",
    "HF Daily Papers": "\U0001f917",
    "HuggingFace Blog": "\U0001f917",
    "AlphaSignal": "\U0001f4e1",
    "ScienceDaily AI": "\U0001f52c",
}


def infer_tags(item):
    text = f"{item['title']} {item['summary']}"
    tags = []
    for pattern, label, css, emoji in TAG_RULES:
        if pattern.search(text):
            tags.append((label, css, emoji))
    if not tags:
        tags.append(("AI", "tag-other", "\u2728"))
    return tags[:3]


def pick_emoji(item):
    """优先用 LLM 返回的 emoji，否则按来源 > 标签规则推断。"""
    if item.get("emoji_override"):
        return item["emoji_override"]
    source_em = SOURCE_EMOJI.get(item.get("source", ""))
    if source_em:
        return source_em
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
        .category-header {{
            color: #e8e8ed;
            font-size: 18px;
            font-weight: 700;
            margin: 32px 0 16px;
            padding-left: 8px;
            border-left: 3px solid #7c3aed;
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
        }}
        .card:hover {{
            transform: translateY(-6px);
            box-shadow: 0 12px 36px rgba(0,0,0,0.5);
            border-color: rgba(255,255,255,0.12);
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
        .tag-emoji {{
            font-size: 16px;
            line-height: 1;
        }}
        .tag-category {{
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 10px;
            background: rgba(124,58,237,0.2);
            color: #b794f4;
        }}
        .card-title {{
            font-size: 16px;
            font-weight: 700;
            color: #e8e8ed;
            margin-bottom: 10px;
            line-height: 1.5;
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
        .source-list {{
            text-align: center;
            color: rgba(255,255,255,0.3);
            font-size: 11px;
            margin-top: 8px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">\U0001f955</div>
            <h1>AI-Scream-{date}</h1>
            <div class="subtitle">每日AI行业资讯精选 | 用最少的时间掌握最新动态</div>
            <div class="stats">
                <span class="stat">{count} 条精选</span>
                <span class="stat">{source_count} 个数据源</span>
                <span class="stat">{category_count} 个分类</span>
            </div>
        </div>
        <div class="cards-grid">
{cards}
        </div>
        <div class="footer">
            \U0001f955 由 AI-Scream 自动生成 | {date}
            <div class="source-list">数据源: {sources_text}</div>
        </div>
    </div>
</body>
</html>"""

CARD_TEMPLATE = """            <a class="card" href="{url}" target="_blank" rel="noopener">
                <div class="tags"><span class="tag-emoji">{emoji}</span> {tags_html} <span class="tag-category">{category}</span></div>
                <div class="card-title">{title}</div>
                <div class="card-summary">{summary}</div>
                <div class="card-meta">
                    <span class="card-source">{source}</span>
                    <span class="card-arrow">\u2192</span>
                </div>
            </a>"""


def generate_html(items, date_str):
    cards = []
    sources_set = set()
    categories_set = set()

    for item in items:
        tags = infer_tags(item)
        tags_html = "".join(
            f'<span class="tag {css}">{escape(label)}</span>' for label, css, emoji in tags
        )
        sources_set.add(item["source"])
        categories_set.add(item.get("category", ""))
        cards.append(
            CARD_TEMPLATE.format(
                url=escape(item["url"]),
                tags_html=tags_html,
                emoji=pick_emoji(item),
                title=escape(item.get("title_zh", item["title"])),
                summary=escape(item.get("summary_zh", item["summary"])),
                source=escape(item["source"]),
                category=escape(item.get("category", "AI")),
            )
        )

    sources_text = " · ".join(sorted(sources_set))

    return HTML_TEMPLATE.format(
        date=date_str,
        cards="\n".join(cards),
        count=len(items),
        source_count=len(sources_set),
        category_count=len(categories_set),
        sources_text=sources_text,
    )


# ── 飞书推送 ──────────────────────────────────────────

def build_feishu_card(items, date_str):
    elements = []

    # 按分类分组展示
    category_groups = {}
    for item in items:
        cat = item.get("category", "其他")
        category_groups.setdefault(cat, []).append(item)

    for cat, cat_items in category_groups.items():
        elements.append({
            "tag": "markdown",
            "content": f"📂 {cat}"
        })
        for item in cat_items:
            tags = infer_tags(item)
            tag_str = " | ".join(label for label, _, _ in tags)
            emoji = pick_emoji(item)
            title_zh = item.get("title_zh", item["title"])
            summary_zh = item.get("summary_zh", item["summary"])
            elements.append({
                "tag": "markdown",
                "content": f"{emoji} {tag_str}\n{title_zh}\n{summary_zh}"
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
        "elements": [{"tag": "plain_text", "content": f"\U0001f955 由 AI-Scream 自动生成 | {date_str} | {len(items)} 条精选"}],
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
        print(f"      Published: {PAGES_URL}/latest.html")
    except Exception as e:
        print(f"[WARN] GitHub Pages push failed: {e}")


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
            print("[OK] Feishu push succeeded")
        else:
            print(f"[WARN] Feishu response: {result}")
    except Exception as e:
        print(f"[ERROR] Feishu push failed: {e}")


# ── 主流程 ────────────────────────────────────────────

def main():
    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(beijing_tz).strftime("%Y-%m-%d")
    print(f"=== AI-Scream Multi-Source Edition | {today} ===\n")

    all_items = []

    # 1. 抓取所有 RSS 源
    print("[1/6] Fetching RSS sources...")
    for i, (name, url, category, max_n, need_filter) in enumerate(RSS_SOURCES):
        print(f"  [{i+1}/{len(RSS_SOURCES)}] {name}...")
        items = fetch_rss_source(name, url, category, max_n, need_filter)
        print(f"    Got {len(items)} items")
        all_items.extend(items)

    # 2. TLDR.tech AI
    print("\n[2/6] Fetching TLDR.tech AI...")
    tldr = fetch_tldr()
    print(f"  Got {len(tldr)} items")
    all_items.extend(tldr)

    # 3. HuggingFace Daily Papers
    print("\n[3/6] Fetching HuggingFace Daily Papers...")
    hf = fetch_hf_daily_papers()
    print(f"  Got {len(hf)} items")
    all_items.extend(hf)

    # 4. AI 聚合平台
    print("\n[4/6] Fetching AI aggregators...")
    agg = fetch_ai_aggregators()
    print(f"  Got {len(agg)} items")
    all_items.extend(agg)

    # 5. AlphaSignal
    print("\n[4.5/6] Fetching AlphaSignal...")
    alpha = fetch_alphasignal()
    print(f"  Got {len(alpha)} items")
    all_items.extend(alpha)

    # 6. Twitter KOL (可能失败，静默处理)
    print("\n[4.6/6] Fetching Twitter KOL feeds...")
    kol = fetch_twitter_kol_rss()
    print(f"  Got {len(kol)} items")
    all_items.extend(kol)

    print(f"\n  Total raw: {len(all_items)}")

    # 二次严格 AI 过滤
    print("\n[5/6] Strict AI relevance filtering...")
    # 对于已标记为不需要过滤的源（本身就是 AI 专用源），跳过过滤
    ai_native_sources = {
        "TechCrunch AI", "VentureBeat AI", "AI News", "ScienceDaily AI",
        "OpenAI Blog", "Google AI Blog", "DeepMind Blog", "Meta AI Blog",
        "Anthropic Blog", "HuggingFace Blog", "HF Daily Papers",
        "TLDR.tech AI", "机器之心", "量子位", "Lil'Log (Lilian Weng)",
        "AlphaSignal", "LMSYS", "AIbase", "The Verge AI", "WIRED AI",
        "Microsoft AI Blog", "Practical AI Podcast",
    }

    filtered_items = []
    for item in all_items:
        if item["source"] in ai_native_sources:
            # AI 原生源，直接保留（但仍过滤论文和误匹配）
            combined = f"{item['title']} {item['summary']}"
            if PAPER_FILTER.search(combined) or FALSE_POSITIVE_FILTER.search(combined):
                continue
            filtered_items.append(item)
        else:
            # 非 AI 原生源，需要 AI 关键词匹配
            combined = f"{item['title']} {item['summary']} {item['url']}"
            if AI_KEYWORDS.search(combined) and not PAPER_FILTER.search(combined) and not FALSE_POSITIVE_FILTER.search(combined):
                filtered_items.append(item)

    print(f"  After AI filter: {len(filtered_items)} (filtered out {len(all_items) - len(filtered_items)})")

    # 去重排序
    final = deduplicate_and_rank(filtered_items)
    print(f"  After dedup & rank: {len(final)}")

    if not final:
        print("[ERROR] No items fetched. Check network.")
        return

    # 分类统计
    cat_stats = {}
    for item in final:
        cat = item.get("category", "其他")
        cat_stats[cat] = cat_stats.get(cat, 0) + 1
    print(f"\n  Category breakdown:")
    for cat, count in sorted(cat_stats.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")

    # Ollama 生成中文标题与摘要
    print(f"\n[6/7] Generating Chinese summaries with Ollama...")
    final = generate_chinese_summaries(final)

    # 生成 HTML
    html = generate_html(final, today)
    output_path = OUTPUT_DIR / f"AI-Scream-{today}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"\n[7/8] HTML saved: {output_path}")

    # 推送到 GitHub Pages
    print("[8/9] Publishing to GitHub Pages...")
    publish_to_pages(html, today)

    # 推送飞书
    card = build_feishu_card(final, today)
    push_feishu(card)

    # 打印摘要
    print(f"\n=== Top 5 Stories ===")
    for i, item in enumerate(final[:5], 1):
        print(f"  {i}. [{item['source']}][{item.get('category', '')}] {item.get('title_zh', item['title'])}")

    print(f"\nDone! {len(final)} items from {len(cat_stats)} categories.")


if __name__ == "__main__":
    main()
