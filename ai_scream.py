"""
AI-Scream 纯 Python 版 —— 每日 AI 资讯抓取、HTML 生成与飞书推送脚本。
数据源: TechCrunch AI RSS, Hacker News API, TLDR.tech AI 归档页。
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
MAX_ITEMS = 15
HN_TOP_N = 30
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b"
GEMINI_API_KEY = "AIzaSyAwesMzAFIU45qjxw0ISW92L-ufU4tFG78"

AI_KEYWORDS = re.compile(
    r"artificial.intelligence|machine.learning|deep.learning"
    r"|llm|large.language|gpt.?[3-5]|openai|claude|anthropic|gemini|mistral"
    r"|chatgpt|diffusion|neural.net|transformer|generative.ai"
    r"|langchain|hugging.?face|stable.diffusion|midjourney"
    r"|ai.agent|ai.model|foundation.model|reasoning.model"
    r"|ai.chip|ai.video|ai.startup|ai.fund|ai.regul|ai.safety"
    r"|sora|dall.?e|copilot.ai|cursor.ai|ai.coding",
    re.IGNORECASE,
)

# 过滤掉论文/学术内容
PAPER_FILTER = re.compile(
    r"arxiv\.org|preprint|theorem|equation|proof|journal\.of"
    r"|hamilton.jacobi|reinforcement.learning.and.diffusion"
    r"|mathematical.methods",
    re.IGNORECASE,
)

# 过滤掉跟 AI 无关的内容（误匹配修正）
FALSE_POSITIVE_FILTER = re.compile(
    r"copilot.edited.an.ad|smart.eyeglasses|smart.glasses"
    r"|philly.courts|apple.watch",
    re.IGNORECASE,
)

# ── 数据抓取 ──────────────────────────────────────────

def fetch_techcrunch():
    """TechCrunch AI RSS feed."""
    url = "https://techcrunch.com/category/artificial-intelligence/feed/"
    items = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:20]:
            summary = entry.get("summary", "")
            # 去掉 HTML 标签
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
        print(f"[WARN] TechCrunch fetch failed: {e}")
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
                # 过滤论文/学术内容
                if PAPER_FILTER.search(title) or PAPER_FILTER.search(url):
                    continue
                # 过滤误匹配（标题含 AI 关键词但实际无关）
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
        print(f"[WARN] HN fetch failed: {e}")
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
        print(f"[WARN] ScienceDaily fetch failed: {e}")
    return items


def fetch_tldr():
    """TLDR.tech AI — 爬取最新一期归档页。"""
    items = []
    try:
        # 归档页获取最新日期
        resp = requests.get("https://tldr.tech/ai/archives", timeout=10)
        # 找最新日期链接 /ai/YYYY-MM-DD
        dates = re.findall(r"/ai/(\d{4}-\d{2}-\d{2})", resp.text)
        if not dates:
            return items
        latest = sorted(dates, reverse=True)[0]
        # 抓取详情页
        detail = requests.get(f"https://tldr.tech/ai/{latest}", timeout=10)
        # 提取文章条目：标题在 <a> 标签中，摘要在段落中
        # TLDR 页面结构：每条新闻在一个 article 或 section 中
        # 简单提取所有外部链接 + 标题
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
        print(f"[WARN] TLDR fetch failed: {e}")
    return items


# ── 去重与排序 ─────────────────────────────────────────

def deduplicate_and_rank(all_items):
    """URL 去重，HN 高分优先，其余按来源顺序。"""
    seen_urls = set()
    unique = []
    # HN 高分帖子优先
    all_items.sort(key=lambda x: x.get("score", 0), reverse=True)
    for item in all_items:
        url = item["url"].rstrip("/")
        if url not in seen_urls and item["title"]:
            seen_urls.add(url)
            unique.append(item)
    return unique[:MAX_ITEMS]


# ── Ollama 生成中文标题与摘要 ─────────────────────────

def generate_chinese_summaries(items):
    """用 Ollama 本地模型为每条资讯生成编辑式中文标题和摘要。"""

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
    """Gemini 失败时的降级方案：直接用英文原文。"""
    for item in items:
        item["title_zh"] = item["title"]
        item["summary_zh"] = item["summary"]


# ── 标签推断 ──────────────────────────────────────────

TAG_RULES = [
    (re.compile(r"llm|gpt|claude|gemini|model|mistral|anthropic|openai", re.I), "大模型", "tag-llm", "\U0001f916"),
    (re.compile(r"fund|rais|invest|ipo|valuat|\$\d|billion|million|serie", re.I), "融资", "tag-biz", "\U0001f4b0"),
    (re.compile(r"open.?source|github|hugging|apache|mit.license", re.I), "开源", "tag-open", "\U0001f331"),
    (re.compile(r"regulat|policy|govern|law|eu.ai|congress|senate|ban|court", re.I), "政策", "tag-policy", "\U0001f3db"),
    (re.compile(r"launch|releas|announc|introduc|new.feature|product", re.I), "产品", "tag-product", "\U0001f680"),
    (re.compile(r"research|study|scientif|danger|risk|warning", re.I), "研究", "tag-research", "\U0001f52c"),
    (re.compile(r"secur|privacy|hack|exploit|vulnerab|data.collect|track", re.I), "安全", "tag-policy", "\U0001f512"),
    (re.compile(r"chip|gpu|nvidia|hardware|data.center|infra", re.I), "基础设施", "tag-other", "\u2699\ufe0f"),
    (re.compile(r"video|image|generat|sora|diffusion|creative", re.I), "创作", "tag-product", "\U0001f3a8"),
    (re.compile(r"agent|autonom|coding.agent|free.software", re.I), "Agent", "tag-llm", "\U0001f9e0"),
]

# 兜底 emoji 映射：根据来源给个默认 emoji
SOURCE_EMOJI = {
    "Hacker News": "\U0001f525",
    "TechCrunch": "\U0001f4f0",
    "TLDR.tech": "\U0001f4e8",
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
    """优先用 Gemini 返回的 emoji，否则按标签规则推断。"""
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
            <div class="subtitle">每日AI行业资讯精选 | 用最少的时间掌握最新动态</div>
            <div class="stats">
                <span class="stat">{count} 条精选</span>
                <span class="stat">来源 TechCrunch / HN / TLDR</span>
            </div>
        </div>
        <div class="cards-grid">
{cards}
        </div>
        <div class="footer">
            \U0001f955 由 AI-Scream 自动生成 | {date}
        </div>
    </div>
</body>
</html>"""

CARD_TEMPLATE = """            <a class="card" href="{url}" target="_blank" rel="noopener">
                <div class="tags"><span class="tag-emoji">{emoji}</span> {tags_html}</div>
                <div class="card-title">{title}</div>
                <div class="card-summary">{summary}</div>
                <div class="card-meta">
                    <span class="card-source">{source}</span>
                    <span class="card-arrow">\u2192</span>
                </div>
            </a>"""


def generate_html(items, date_str):
    cards = []
    for item in items:
        tags = infer_tags(item)
        tags_html = "".join(
            f'<span class="tag {css}">{escape(label)}</span>' for label, css, emoji in tags
        )
        cards.append(
            CARD_TEMPLATE.format(
                url=escape(item["url"]),
                tags_html=tags_html,
                emoji=pick_emoji(item),
                title=escape(item.get("title_zh", item["title"])),
                summary=escape(item.get("summary_zh", item["summary"])),
                source=escape(item["source"]),
            )
        )
    return HTML_TEMPLATE.format(date=date_str, cards="\n".join(cards), count=len(items))


# ── 飞书推送 ──────────────────────────────────────────

def build_feishu_card(items, date_str):
    elements = []
    for i, item in enumerate(items):
        tags = infer_tags(item)
        tag_str = " | ".join(label for label, _, _ in tags)
        emoji = pick_emoji(item)
        title_zh = item.get("title_zh", item["title"])
        summary_zh = item.get("summary_zh", item["summary"])
        elements.append({
            "tag": "markdown",
            "content": f"**{emoji} {tag_str}**\n**{title_zh}**\n{summary_zh}"
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
        # 写入 latest.html（始终覆盖）和日期归档
        (pages / "latest.html").write_text(html_content, encoding="utf-8")
        (pages / f"AI-Scream-{date_str}.html").write_text(html_content, encoding="utf-8")
        # git add + commit + push
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
    # 使用北京时间
    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(beijing_tz).strftime("%Y-%m-%d")
    print(f"=== AI-Scream RSS Edition | {today} ===\n")

    # 1. 抓取
    print("[1/4] Fetching TechCrunch AI...")
    tc = fetch_techcrunch()
    print(f"      Got {len(tc)} items")

    print("[2/4] Fetching Hacker News...")
    hn = fetch_hackernews()
    print(f"      Got {len(hn)} items")

    print("[3/4] Fetching TLDR.tech AI...")
    tldr = fetch_tldr()
    print(f"      Got {len(tldr)} items")

    # 2. 合并去重排序
    all_items = tc + hn + tldr
    print(f"\n      Total raw: {len(all_items)}")
    final = deduplicate_and_rank(all_items)
    print(f"      After dedup: {len(final)}")

    if not final:
        print("[ERROR] No items fetched. Check network.")
        return

    # 3. Gemini 生成中文标题与摘要
    print(f"\n[4/5] Generating Chinese summaries with Gemini...")
    final = generate_chinese_summaries(final)

    # 4. 生成 HTML
    html = generate_html(final, today)
    output_path = OUTPUT_DIR / f"AI-Scream-{today}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"\n[5/6] HTML saved: {output_path}")

    # 5. 推送到 GitHub Pages
    print("[6/6] Publishing to GitHub Pages...")
    publish_to_pages(html, today)

    # 6. 推送飞书
    card = build_feishu_card(final, today)
    push_feishu(card)

    # 5. 打印摘要
    print(f"\n=== Top 3 Stories ===")
    for i, item in enumerate(final[:3], 1):
        print(f"  {i}. [{item['source']}] {item['title']}")

    print(f"\nDone!")


if __name__ == "__main__":
    main()
