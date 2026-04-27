"""
Local review server for AI'm OK.

Supports:
1. selecting/deselecting items before push
2. tagging review feedback per item
3. splitting audio items into a dedicated review section
4. manual reordering before submission
"""

import json
import threading
import time
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer


FEEDBACK_OPTIONS = [
    "有用",
    "一般",
    "无关",
    "太偏技术",
    "太偏商业",
    "适合音频部",
]


REVIEW_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI'm OK 审核页</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            background: #0d1117;
            color: #e6edf3;
            min-height: 100vh;
            padding-bottom: 76px;
        }}
        .toolbar {{
            position: sticky;
            top: 0;
            z-index: 10;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
            padding: 14px 18px;
            background: rgba(13, 17, 23, 0.94);
            border-bottom: 1px solid rgba(240,246,252,0.08);
            backdrop-filter: blur(10px);
        }}
        .title {{ font-size: 20px; font-weight: 800; }}
        .meta {{ font-size: 13px; color: #8b949e; }}
        .toolbar-actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
        button {{
            border: none;
            border-radius: 999px;
            padding: 10px 18px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 700;
        }}
        .btn-lite {{ background: #21262d; color: #e6edf3; }}
        .btn-primary {{ background: linear-gradient(135deg, #3b82f6, #8b5cf6); color: #fff; }}
        .btn-danger {{ background: #3d1d1d; color: #ffb4b4; }}
        .container {{ max-width: 1180px; margin: 0 auto; padding: 18px; }}
        .filter-bar {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }}
        .filter-chip {{
            padding: 6px 14px;
            border-radius: 999px;
            background: #161b22;
            color: #9da7b3;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
            border: 1px solid rgba(240,246,252,0.08);
        }}
        .filter-chip.active {{ color: #fff; background: #22304a; border-color: #3b82f6; }}
        .section-title {{ margin: 20px 0 10px; font-size: 15px; color: #9da7b3; font-weight: 800; }}
        .cards-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
        @media (max-width: 760px) {{
            .cards-grid {{ grid-template-columns: 1fr; }}
        }}
        .card {{
            background: #161b22;
            border: 2px solid rgba(240,246,252,0.06);
            border-radius: 20px;
            padding: 18px;
            transition: 0.2s ease;
        }}
        .card.selected {{ border-color: #3b82f6; box-shadow: 0 0 0 1px rgba(59,130,246,0.25); }}
        .card.removed {{ opacity: 0.42; }}
        .card-top {{ display: flex; justify-content: space-between; gap: 12px; margin-bottom: 8px; }}
        .rank {{ font-size: 12px; color: #8b949e; font-weight: 700; }}
        .pool-badge {{ font-size: 11px; padding: 4px 10px; border-radius: 999px; font-weight: 800; }}
        .pool-A {{ background: rgba(34,197,94,0.16); color: #7ee787; }}
        .pool-B {{ background: rgba(250,204,21,0.16); color: #facc15; }}
        .pool-DROP {{ background: rgba(248,113,113,0.16); color: #fda4af; }}
        .tags {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }}
        .tag {{ font-size: 11px; padding: 4px 10px; border-radius: 999px; background: #21262d; color: #c9d1d9; }}
        .title-line {{ font-size: 16px; font-weight: 800; line-height: 1.55; margin-bottom: 8px; }}
        .summary {{ color: #9da7b3; font-size: 13px; line-height: 1.7; margin-bottom: 12px; }}
        .scores {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; color: #8b949e; font-size: 12px; }}
        .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }}
        .toggle-btn {{ background: #22304a; color: #dbeafe; }}
        .toggle-btn.off {{ background: #2a1a1a; color: #fecaca; }}
        .move-btn {{ background: #1f2937; color: #d1d5db; padding: 8px 12px; font-size: 13px; }}
        .link {{ display: inline-flex; align-items: center; color: #7cc0ff; text-decoration: none; font-size: 13px; font-weight: 700; }}
        .feedback-title {{ font-size: 12px; color: #8b949e; margin-bottom: 8px; font-weight: 800; }}
        .feedback-group {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .feedback-chip {{
            padding: 6px 12px;
            border-radius: 999px;
            background: #0f1720;
            color: #9da7b3;
            font-size: 12px;
            font-weight: 700;
            border: 1px solid rgba(240,246,252,0.08);
            cursor: pointer;
        }}
        .feedback-chip.active {{ background: #243247; color: #fff; border-color: #60a5fa; }}
        .status-bar {{
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: rgba(13, 17, 23, 0.96);
            border-top: 1px solid rgba(240,246,252,0.08);
            backdrop-filter: blur(10px);
            padding: 12px 18px;
            display: flex;
            justify-content: center;
            gap: 18px;
            flex-wrap: wrap;
            color: #9da7b3;
            font-size: 13px;
        }}
        .highlight {{ color: #fff; font-weight: 800; }}
        .overlay {{
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.66);
            align-items: center;
            justify-content: center;
            z-index: 50;
            color: #fff;
            font-size: 20px;
            font-weight: 800;
        }}
        .overlay.active {{ display: flex; }}
    </style>
</head>
<body>
    <div class="toolbar">
        <div>
            <div class="title">AI'm OK 审核页</div>
            <div class="meta">共 <span class="highlight" id="totalCount">{total}</span> 条，已选 <span class="highlight" id="selectedCount">{total}</span> 条</div>
        </div>
        <div class="toolbar-actions">
            <button class="btn-lite" type="button" onclick="selectAll()">全选</button>
            <button class="btn-lite" type="button" onclick="invertSelection()">反选</button>
            <button class="btn-danger" type="button" onclick="deselectAll()">全不选</button>
            <button class="btn-primary" type="button" id="submitBtn" onclick="submitReview()">确认推送</button>
        </div>
    </div>

    <div class="container">
        <div class="filter-bar">
            <span class="filter-chip active" onclick="filterBy('all', this)">全部</span>
            <span class="filter-chip" onclick="filterBy('intl', this)">国际</span>
            <span class="filter-chip" onclick="filterBy('domestic', this)">国内</span>
            <span class="filter-chip" onclick="filterBy('audio', this)">AI音频</span>
            <span class="filter-chip" onclick="filterBy('A', this)">A池</span>
            <span class="filter-chip" onclick="filterBy('B', this)">B池</span>
            {filter_chips}
        </div>

        <div class="section-title" data-section="intl">国际资讯（{intl_count}）</div>
        <div class="cards-grid" data-section="intl">
{intl_cards}
        </div>

        <div class="section-title" data-section="domestic">国内资讯（{domestic_count}）</div>
        <div class="cards-grid" data-section="domestic">
{domestic_cards}
        </div>

        <div class="section-title" data-section="audio">AI音频（{audio_count}）</div>
        <div class="cards-grid" data-section="audio">
{audio_cards}
        </div>
    </div>

    <div class="status-bar">
        <span>已选 <span class="highlight" id="bottomSelectedCount">{total}</span> / {total}</span>
        <span>A池 <span class="highlight" id="aCount">{a_count}</span></span>
        <span>B池 <span class="highlight" id="bCount">{b_count}</span></span>
        <span>已标注 <span class="highlight" id="feedbackCount">0</span></span>
    </div>

    <div class="overlay" id="overlay">正在提交审核结果...</div>

    <script>
        const FEEDBACK_OPTIONS = {feedback_options_json};

        function getCards() {{
            return Array.from(document.querySelectorAll('.card'));
        }}

        function getVisibleCards() {{
            return getCards().filter(card => card.style.display !== 'none');
        }}

        function refreshRanks() {{
            getCards().forEach((card, idx) => {{
                const rankEl = card.querySelector('.rank');
                if (rankEl) rankEl.textContent = `#${{idx + 1}}`;
            }});
        }}

        function setSelected(card, selected) {{
            card.dataset.selected = selected ? '1' : '0';
            card.classList.toggle('selected', selected);
            card.classList.toggle('removed', !selected);
            const btn = card.querySelector('.toggle-btn');
            if (btn) {{
                btn.textContent = selected ? '保留推送' : '已移除';
                btn.classList.toggle('off', !selected);
            }}
        }}

        function toggleCardSelection(card) {{
            setSelected(card, card.dataset.selected !== '1');
            updateCounts();
        }}

        function moveCard(card, direction) {{
            if (!card) return;
            const parent = card.parentElement;
            if (!parent) return;
            if (direction < 0) {{
                const prev = card.previousElementSibling;
                if (prev) parent.insertBefore(card, prev);
            }} else {{
                const next = card.nextElementSibling;
                if (next) parent.insertBefore(next, card);
            }}
            refreshRanks();
        }}

        function selectAll() {{
            getVisibleCards().forEach(card => setSelected(card, true));
            updateCounts();
        }}

        function deselectAll() {{
            getVisibleCards().forEach(card => setSelected(card, false));
            updateCounts();
        }}

        function invertSelection() {{
            getVisibleCards().forEach(card => setSelected(card, card.dataset.selected !== '1'));
            updateCounts();
        }}

        function toggleFeedback(cardId, label, chip) {{
            const card = document.querySelector(`.card[data-item-id="${{cardId}}"]`);
            if (!card) return;
            const current = (card.dataset.feedback || '').split('|').filter(Boolean);
            const idx = current.indexOf(label);
            if (idx >= 0) {{
                current.splice(idx, 1);
                chip.classList.remove('active');
            }} else {{
                current.push(label);
                chip.classList.add('active');
            }}
            card.dataset.feedback = current.join('|');
            updateCounts();
        }}

        function filterBy(type, chipEl) {{
            document.querySelectorAll('.filter-chip').forEach(chip => chip.classList.remove('active'));
            chipEl.classList.add('active');
            getCards().forEach(card => {{
                const sourceType = card.dataset.sourceType;
                const category = card.dataset.category;
                const pool = card.dataset.pool;
                const isAudio = card.dataset.audio === '1';
                let show = false;
                if (type === 'all') show = true;
                else if (type === 'intl' || type === 'domestic') show = sourceType === type;
                else if (type === 'audio') show = isAudio;
                else if (type === 'A' || type === 'B') show = pool === type;
                else show = category === type;
                card.style.display = show ? '' : 'none';
            }});
            document.querySelectorAll('.section-title, .cards-grid[data-section]').forEach(el => {{
                if (type === 'all') {{
                    el.style.display = '';
                    return;
                }}
                const section = el.dataset.section;
                if (!section) return;
                if (type === 'intl' || type === 'domestic' || type === 'audio') {{
                    el.style.display = section === type ? '' : 'none';
                }} else {{
                    el.style.display = '';
                }}
            }});
        }}

        function updateCounts() {{
            const cards = getCards();
            const selected = cards.filter(card => card.dataset.selected === '1');
            const withFeedback = cards.filter(card => (card.dataset.feedback || '').trim() !== '');
            document.getElementById('selectedCount').textContent = selected.length;
            document.getElementById('bottomSelectedCount').textContent = selected.length;
            document.getElementById('feedbackCount').textContent = withFeedback.length;

            const aCount = selected.filter(card => card.dataset.pool === 'A').length;
            const bCount = selected.filter(card => card.dataset.pool === 'B').length;
            document.getElementById('aCount').textContent = aCount;
            document.getElementById('bCount').textContent = bCount;

            const btn = document.getElementById('submitBtn');
            btn.textContent = selected.length ? `确认推送（${{selected.length}} 条）` : '请至少选择 1 条';
            btn.disabled = selected.length === 0;
        }}

        function collectPayload() {{
            const records = getCards().map(card => {{
                return {{
                    item_id: parseInt(card.dataset.itemId, 10),
                    selected: card.dataset.selected === '1',
                    labels: (card.dataset.feedback || '').split('|').filter(Boolean),
                }};
            }});
            return {{
                selected_ids: records.filter(r => r.selected).map(r => r.item_id),
                ordered_ids: getCards().map(card => parseInt(card.dataset.itemId, 10)),
                feedback: records,
            }};
        }}

        function submitReview() {{
            const payload = collectPayload();
            if (!payload.selected_ids.length) {{
                alert('请至少选择 1 条');
                return;
            }}
            if (!confirm(`确认推送 ${{payload.selected_ids.length}} 条资讯到飞书吗？`)) return;

            document.getElementById('overlay').classList.add('active');
            fetch('/submit', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload),
            }})
            .then(resp => resp.json())
            .then(data => {{
                document.getElementById('overlay').classList.remove('active');
                if (!data.success) {{
                    alert('提交失败：' + (data.error || '未知错误'));
                    return;
                }}
                alert(`已提交 ${{data.count}} 条，反馈标签 ${{data.feedback_count}} 条`);
                setTimeout(() => fetch('/shutdown'), 600);
            }})
            .catch(err => {{
                document.getElementById('overlay').classList.remove('active');
                alert('网络错误：' + err.message);
            }});
        }}

        document.addEventListener('keydown', function(e) {{
            if (e.ctrlKey && e.key === 'a') {{
                e.preventDefault();
                selectAll();
            }}
            if (e.ctrlKey && e.key === 'i') {{
                e.preventDefault();
                invertSelection();
            }}
            if (e.ctrlKey && e.key === 'Enter') {{
                e.preventDefault();
                submitReview();
            }}
        }});

        document.addEventListener('DOMContentLoaded', function() {{
            getCards().forEach(card => setSelected(card, true));
            document.querySelectorAll('.feedback-chip').forEach(chip => {{
                chip.addEventListener('click', function(e) {{
                    e.preventDefault();
                    const cardId = parseInt(chip.dataset.itemId, 10);
                    const label = chip.dataset.label || '';
                    toggleFeedback(cardId, label, chip);
                }});
            }});
            refreshRanks();
            updateCounts();
        }});
    </script>
</body>
</html>"""


REVIEW_CARD_TEMPLATE = """            <div class="card selected"
                 data-item-id="{item_id}"
                 data-source-type="{source_type}"
                 data-category="{category}"
                 data-pool="{pool}"
                 data-audio="{is_audio}"
                 data-selected="1"
                 data-feedback="">
                <div class="card-top">
                    <div class="rank">#{rank}</div>
                    <div class="pool-badge pool-{pool}">{pool_label}</div>
                </div>
                <div class="tags">
                    <span class="tag">{emoji}</span>
                    {tags_html}
                </div>
                <div class="title-line"><strong>{title}</strong></div>
                <div class="summary">{summary}</div>
                <div class="scores">
                    <span>来源: {source_icon} {source_display}</span>
                    <span>热度: {heat_score}</span>
                    <span>实用分: {practical_score}</span>
                    <span>音频分: {audio_score}</span>
                </div>
                <div class="actions">
                    <button type="button" class="toggle-btn" onclick="toggleCardSelection(this.closest('.card'))">保留推送</button>
                    <button type="button" class="move-btn" onclick="moveCard(this.closest('.card'), -1)">上移</button>
                    <button type="button" class="move-btn" onclick="moveCard(this.closest('.card'), 1)">下移</button>
                    <a class="link" href="{url}" target="_blank" rel="noopener">查看原文</a>
                </div>
                <div class="feedback-title">反馈标签</div>
                <div class="feedback-group">
                    {feedback_chips}
                </div>
            </div>"""


class ReviewResult:
    def __init__(self):
        self.selected_ids = None
        self.ordered_ids = []
        self.feedback = []
        self.completed = threading.Event()


def _build_review_card(item, index, infer_tags_func, pick_emoji_func, get_source_info_func):
    tags = infer_tags_func(item)
    tags_html = "".join(
        f'<span class="tag">{escape(label)}</span>' for label, _css, _emoji in tags
    )
    feedback_chips = "".join(
        f'<button type="button" class="feedback-chip" data-item-id="{index}" data-label="{escape(label, quote=True)}">{escape(label)}</button>'
        for label in FEEDBACK_OPTIONS
    )
    src_info = get_source_info_func(item["source"])
    pool = escape(str(item.get("_pool", "A")))
    if pool == "A":
        pool_label = "A池 · 高确定性"
    elif pool == "B":
        pool_label = "B池 · 补充候选"
    else:
        pool_label = "DROP"
    return REVIEW_CARD_TEMPLATE.format(
        item_id=index,
        rank=index + 1,
        source_type=src_info["type"],
        category=escape(item.get("category", "AI")),
        pool=pool,
        is_audio="1" if item.get("_is_audio_section") else "0",
        pool_label=pool_label,
        emoji=pick_emoji_func(item),
        tags_html=tags_html,
        title=escape(item.get("title_zh", item["title"])),
        summary=escape(item.get("summary_zh", item["summary"])),
        source_display=escape(src_info["display"]),
        source_icon=src_info["icon"],
        heat_score=item.get("heat_score", 0),
        practical_score=item.get("practical_score", 0),
        audio_score=item.get("audio_score", 0),
        url=escape(item["url"]),
        feedback_chips=feedback_chips,
    )


def _build_review_page(items, infer_tags_func, pick_emoji_func, get_source_info_func, audio_item_urls=None):
    audio_item_urls = {str(u).rstrip("/") for u in (audio_item_urls or set()) if u}
    prepared_items = []
    for item in items:
        cloned = dict(item)
        url = str(cloned.get("url", "") or "").rstrip("/")
        cloned["_is_audio_section"] = bool(url and url in audio_item_urls)
        prepared_items.append(cloned)

    intl_items = [
        (i, it) for i, it in enumerate(prepared_items)
        if it.get("source_type") != "domestic" and not it.get("_is_audio_section")
    ]
    domestic_items = [
        (i, it) for i, it in enumerate(prepared_items)
        if it.get("source_type") == "domestic" and not it.get("_is_audio_section")
    ]
    audio_items = [
        (i, it) for i, it in enumerate(prepared_items)
        if it.get("_is_audio_section")
    ]

    intl_cards = "\n".join(
        _build_review_card(it, i, infer_tags_func, pick_emoji_func, get_source_info_func)
        for i, it in intl_items
    )
    domestic_cards = "\n".join(
        _build_review_card(it, i, infer_tags_func, pick_emoji_func, get_source_info_func)
        for i, it in domestic_items
    )
    audio_cards = "\n".join(
        _build_review_card(it, i, infer_tags_func, pick_emoji_func, get_source_info_func)
        for i, it in audio_items
    )

    categories = sorted({str(it.get("category", "AI")) for it in prepared_items if it.get("category")})
    filter_chips = "".join(
        f'<span class="filter-chip" onclick="filterBy({json.dumps(cat, ensure_ascii=False)}, this)">{escape(cat)}</span>'
        for cat in categories
    )
    a_count = sum(1 for it in prepared_items if it.get("_pool") == "A")
    b_count = sum(1 for it in prepared_items if it.get("_pool") == "B")
    return REVIEW_PAGE_TEMPLATE.format(
        total=len(prepared_items),
        intl_count=len(intl_items),
        domestic_count=len(domestic_items),
        audio_count=len(audio_items),
        intl_cards=intl_cards,
        domestic_cards=domestic_cards,
        audio_cards=audio_cards,
        filter_chips=filter_chips,
        a_count=a_count,
        b_count=b_count,
        feedback_options_json=json.dumps(FEEDBACK_OPTIONS, ensure_ascii=False),
    )


def start_review_server(
    items,
    infer_tags_func,
    pick_emoji_func,
    get_source_info_func,
    port=18088,
    audio_item_urls=None,
    on_ready=None,
):
    review_result = ReviewResult()
    page_html = _build_review_page(
        items,
        infer_tags_func,
        pick_emoji_func,
        get_source_info_func,
        audio_item_urls=audio_item_urls,
    )

    class ReviewHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path in {"/", "/review"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page_html.encode("utf-8"))
                return
            if self.path == "/shutdown":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
                threading.Thread(target=self._shutdown_server, daemon=True).start()
                return
            if self.path == "/cancel":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
                review_result.selected_ids = []
                review_result.ordered_ids = []
                review_result.feedback = []
                review_result.completed.set()
                threading.Thread(target=self._shutdown_server, daemon=True).start()
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            if self.path != "/submit":
                self.send_response(404)
                self.end_headers()
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body or "{}")
                review_result.selected_ids = data.get("selected_ids", [])
                review_result.ordered_ids = data.get("ordered_ids", [])
                review_result.feedback = data.get("feedback", [])
                review_result.completed.set()
                response = {
                    "success": True,
                    "count": len(review_result.selected_ids),
                    "feedback_count": sum(1 for row in review_result.feedback if row.get("labels")),
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8"))

        def _shutdown_server(self):
            time.sleep(1)
            self.server.shutdown()

    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}/"
    print(f"\n  {'=' * 56}")
    print(f"  审核页已启动: {url}")
    print(f"  共 {len(items)} 条待审核")
    print("  现已支持反馈标签：有用 / 一般 / 无关 / 太偏技术 / 太偏商业 / 适合音频部")
    print("  现已支持排序：卡片可上移 / 下移，提交时会保留你的顺序")
    print("  快捷键：Ctrl+A 全选 | Ctrl+I 反选 | Ctrl+Enter 提交")
    print(f"  {'=' * 56}\n")

    if callable(on_ready):
        try:
            on_ready(url, items)
        except Exception as exc:
            print(f"  [WARN] 审核链接通知失败: {exc}")

    webbrowser.open(url)
    review_result.completed.wait()

    try:
        server.shutdown()
    except Exception:
        pass

    if review_result.selected_ids is None or len(review_result.selected_ids) == 0:
        print("  未选择任何条目，本次不推送。")
        return []

    feedback_map = {}
    for row in review_result.feedback or []:
        idx = row.get("item_id")
        if isinstance(idx, int):
            feedback_map[idx] = row

    items_by_id = {idx: item for idx, item in enumerate(items)}
    selected_id_set = {idx for idx in review_result.selected_ids if isinstance(idx, int)}
    ordered_selected_ids = []
    for idx in review_result.ordered_ids or []:
        if isinstance(idx, int) and idx in selected_id_set and idx not in ordered_selected_ids:
            ordered_selected_ids.append(idx)
    for idx in review_result.selected_ids or []:
        if isinstance(idx, int) and idx in selected_id_set and idx not in ordered_selected_ids:
            ordered_selected_ids.append(idx)

    selected_items = []
    for rank, idx in enumerate(ordered_selected_ids, 1):
        item = items_by_id.get(idx)
        if not item:
            continue
        row = feedback_map.get(idx, {})
        item["_review_feedback_labels"] = row.get("labels", [])
        item["_review_rank"] = rank
        selected_items.append(item)
    print(f"  审核完成！用户选择了 {len(selected_items)}/{len(items)} 条")
    return selected_items
