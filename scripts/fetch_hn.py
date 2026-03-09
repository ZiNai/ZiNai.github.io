#!/usr/bin/env python3
"""Fetch Hacker News Top 10 stories and generate static HTML pages.

Features:
- Insights per period (top domains, score highlights, topic tags)
- 7-day rolling window on main page, weekly archive to history/
- Trending tracker: recurring stories, authors, and related topics
"""

import json
import os
import re
import shutil
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
TOP_N = 10
BJT = timezone(timedelta(hours=8))

BASE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
HN_DIR = os.path.join(BASE_DIR, "hn")
DATA_DIR = os.path.join(HN_DIR, "data")
HISTORY_DIR = os.path.join(HN_DIR, "history")
OUTPUT_HTML = os.path.join(HN_DIR, "index.html")
HISTORY_INDEX_HTML = os.path.join(HISTORY_DIR, "index.html")
KEEP_DAYS = 7

PERIOD_ORDER = ["morning", "noon", "evening"]
PERIOD_LABELS = {"morning": "🌅 早间", "noon": "☀️ 午间", "evening": "🌙 晚间"}

# Topic keywords for tagging
TOPIC_KEYWORDS = {
    "AI": ["ai", "gpt", "llm", "machine learning", "deep learning", "neural",
           "openai", "anthropic", "gemini", "claude", "chatgpt", "diffusion",
           "transformer", "model", "inference", "token"],
    "Rust": ["rust", "cargo", "rustc"],
    "Go": ["golang"],
    "Python": ["python", "pip", "django", "flask", "fastapi"],
    "JavaScript": ["javascript", "typescript", "node.js", "deno", "bun ", "react",
                    "vue", "svelte", "next.js"],
    "Web": ["web", "css", "html", "browser", "firefox", "chrome", "safari",
            "wasm", "webassembly", "web component"],
    "Systems": ["linux", "kernel", "os ", "memory", "cpu", "compiler", "c++",
                "gcc", "clang", "llvm", "assembly", "zig"],
    "Database": ["database", "sql", "postgres", "sqlite", "redis", "mongo",
                 "mysql", "nosql"],
    "Security": ["security", "vulnerability", "cve", "hack", "encrypt",
                 "privacy", "zero-day", "backdoor"],
    "Startup": ["yc ", "y combinator", "startup", "funding", "series a",
                "series b", "ipo", "acquisition"],
    "Show HN": ["show hn"],
    "Ask HN": ["ask hn"],
    "Hardware": ["hardware", "chip", "gpu", "nvidia", "amd", "intel", "arm",
                 "risc-v", "fpga", "raspberry pi"],
    "Science": ["science", "physics", "biology", "chemistry", "research",
                "paper", "study", "nasa", "space", "quantum"],
    "Open Source": ["open source", "open-source", "github", "gitlab", "foss"],
}


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def fetch_json(url, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": "hn-daily-bot/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt < retries - 1:
                import time
                wait = 2 ** attempt
                print(f"Fetch failed ({e}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def fetch_top_stories(n=TOP_N):
    ids = fetch_json(HN_TOP_URL)[:n]
    stories = []
    for sid in ids:
        item = fetch_json(HN_ITEM_URL.format(sid))
        if not item or item.get("type") != "story":
            continue
        url = item.get("url", f"https://news.ycombinator.com/item?id={item['id']}")
        stories.append({
            "id": item["id"],
            "title": item.get("title", ""),
            "url": url,
            "domain": extract_domain(url),
            "score": item.get("score", 0),
            "descendants": item.get("descendants", 0),
            "by": item.get("by", ""),
            "tags": extract_tags(item.get("title", "")),
        })
    return stories


def extract_domain(url):
    try:
        host = urlparse(url).netloc
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def extract_tags(title):
    title_lower = f" {title.lower()} "
    tags = []
    for tag, keywords in TOPIC_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                tags.append(tag)
                break
    return tags


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------
def get_period_key(now):
    hour = now.hour
    if hour < 11:
        return "morning"
    elif hour < 17:
        return "noon"
    else:
        return "evening"


# ---------------------------------------------------------------------------
# AI Summary (Gemini API)
# ---------------------------------------------------------------------------
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"


def call_gemini(prompt, max_tokens=800):
    """Call Gemini API. Returns raw text or empty string on failure."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return ""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    })

    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
            return result["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"Gemini API failed: {e}")
        return ""


def generate_ai_summaries(stories):
    """Generate overall summary + per-story summaries in one API call.

    Returns (overall_summary: str, story_summaries: dict[int, str]).
    story_summaries maps story ID to its one-line Chinese summary.
    """
    if not stories or not os.environ.get("GEMINI_API_KEY"):
        return "", {}

    titles_block = "\n".join(
        f'{i+1}. [id={s["id"]}] {s["title"]} (▲{s["score"]}, 💬{s["descendants"]}, {s.get("domain", "")})'
        for i, s in enumerate(stories)
    )

    prompt = (
        f"以下是当前 Hacker News Top 10 热门帖子：\n\n{titles_block}\n\n"
        "请返回 JSON，格式如下：\n"
        '{"overall": "整体摘要（2~3句中文，提炼主题趋势，像新闻简报）", '
        '"stories": {"<story_id>": "该文章的一句话中文简介", ...}}\n\n'
        "要求：\n"
        "1. overall: 不要逐条翻译，而是提炼整体主题和趋势\n"
        "2. stories: 每篇一句话，说清楚这篇文章讲什么，让读者决定是否要点进去看\n"
        "3. 语气简洁、信息密度高\n"
        "4. 只输出 JSON，不要其他内容"
    )

    raw = call_gemini(prompt, max_tokens=800)
    if not raw:
        return "", {}

    try:
        data = json.loads(raw)
        overall = data.get("overall", "")
        story_map = {}
        for k, v in data.get("stories", {}).items():
            try:
                story_map[int(k)] = v
            except (ValueError, TypeError):
                pass
        return overall, story_map
    except json.JSONDecodeError:
        # Fallback: treat entire response as overall summary
        print(f"JSON parse failed, using raw text as overall summary")
        return raw, {}


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------
def generate_insights(stories):
    if not stories:
        return {}

    domains = Counter(s["domain"] for s in stories if s["domain"])
    top_domains = domains.most_common(3)

    scores = [s["score"] for s in stories]
    max_score_story = max(stories, key=lambda s: s["score"])
    most_discussed = max(stories, key=lambda s: s["descendants"])

    all_tags = Counter()
    for s in stories:
        all_tags.update(s.get("tags", []))
    hot_tags = [t for t, _ in all_tags.most_common(5)] if all_tags else []

    return {
        "top_domains": [{"domain": d, "count": c} for d, c in top_domains],
        "avg_score": round(sum(scores) / len(scores)),
        "max_score": {"title": max_score_story["title"], "score": max_score_story["score"]},
        "most_discussed": {
            "title": most_discussed["title"],
            "comments": most_discussed["descendants"],
        },
        "hot_tags": hot_tags,
    }


# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------
def save_data(stories, now):
    os.makedirs(DATA_DIR, exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    period_key = get_period_key(now)
    filepath = os.path.join(DATA_DIR, f"{date_str}.json")

    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            day_data = json.load(f)
    else:
        day_data = {}

    # Generate AI summaries (overall + per-story)
    overall_summary, story_summaries = generate_ai_summaries(stories)
    if overall_summary:
        print(f"AI summary: {overall_summary[:60]}...")
    if story_summaries:
        print(f"Per-story summaries: {len(story_summaries)} generated")
        for s in stories:
            if s["id"] in story_summaries:
                s["summary"] = story_summaries[s["id"]]

    day_data[period_key] = {
        "time": now.strftime("%Y-%m-%d %H:%M"),
        "stories": stories,
        "insights": generate_insights(stories),
        "summary": overall_summary,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(day_data, f, ensure_ascii=False, indent=2)

    return date_str, period_key


def load_recent_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    files = sorted(
        [f for f in os.listdir(DATA_DIR) if f.endswith(".json")],
        reverse=True,
    )[:KEEP_DAYS]
    result = []
    for fname in files:
        date_str = fname.replace(".json", "")
        with open(os.path.join(DATA_DIR, fname), "r", encoding="utf-8") as f:
            result.append((date_str, json.load(f)))
    return result


def load_all_data_flat():
    """Load ALL data (recent + history) for trending analysis."""
    entries = []

    os.makedirs(DATA_DIR, exist_ok=True)
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(DATA_DIR, fname), "r", encoding="utf-8") as f:
            entries.append((fname.replace(".json", ""), json.load(f)))

    if os.path.isdir(HISTORY_DIR):
        for year_dir in sorted(os.listdir(HISTORY_DIR)):
            year_path = os.path.join(HISTORY_DIR, year_dir)
            if not os.path.isdir(year_path) or not year_dir.isdigit():
                continue
            for wk in sorted(os.listdir(year_path)):
                wk_path = os.path.join(year_path, wk)
                if not os.path.isdir(wk_path):
                    continue
                for fname in sorted(os.listdir(wk_path)):
                    if not fname.endswith(".json"):
                        continue
                    with open(os.path.join(wk_path, fname), "r", encoding="utf-8") as f:
                        entries.append((fname.replace(".json", ""), json.load(f)))

    return entries


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------
def archive_old_data(now):
    os.makedirs(DATA_DIR, exist_ok=True)
    cutoff = now - timedelta(days=KEEP_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    archived = []
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".json"):
            continue
        date_str = fname.replace(".json", "")
        if date_str >= cutoff_str:
            continue
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        iso_year, iso_week, _ = dt.isocalendar()
        week_dir = os.path.join(HISTORY_DIR, str(iso_year), f"W{iso_week:02d}")
        os.makedirs(week_dir, exist_ok=True)
        shutil.move(os.path.join(DATA_DIR, fname), os.path.join(week_dir, fname))
        archived.append(date_str)

    return archived


# ---------------------------------------------------------------------------
# Trending tracker
# ---------------------------------------------------------------------------
def build_trending(all_data_flat):
    story_appearances = defaultdict(list)
    author_appearances = defaultdict(list)
    tag_stories = defaultdict(list)

    for date_str, day_data in all_data_flat:
        for period in PERIOD_ORDER:
            if period not in day_data:
                continue
            for s in day_data[period].get("stories", []):
                story_appearances[s["id"]].append({
                    "date": date_str, "period": period,
                    "title": s["title"], "url": s["url"],
                    "score": s["score"], "descendants": s["descendants"],
                })
                author_appearances[s["by"]].append({
                    "date": date_str, "period": period,
                    "title": s["title"], "id": s["id"],
                })
                for tag in s.get("tags", []):
                    tag_stories[tag].append({
                        "date": date_str, "period": period,
                        "title": s["title"], "url": s["url"],
                        "id": s["id"], "score": s["score"],
                    })

    # 持续热门: stories in 2+ periods
    hot_stories = []
    for sid, apps in story_appearances.items():
        if len(apps) >= 2:
            apps.sort(key=lambda a: (a["date"], PERIOD_ORDER.index(a["period"])))
            hot_stories.append({
                "id": sid, "title": apps[-1]["title"], "url": apps[-1]["url"],
                "appearances": apps,
                "peak_score": max(a["score"] for a in apps),
                "count": len(apps),
            })
    hot_stories.sort(key=lambda x: x["count"], reverse=True)

    # 活跃作者: 2+ unique stories
    active_authors = []
    for author, apps in author_appearances.items():
        if not author:
            continue
        seen = set()
        unique = []
        for a in apps:
            if a["id"] not in seen:
                seen.add(a["id"])
                unique.append(a)
        if len(unique) >= 2:
            active_authors.append({"author": author, "stories": unique})
    active_authors.sort(key=lambda x: len(x["stories"]), reverse=True)

    # 话题追踪: tags with 3+ unique stories
    topic_threads = []
    for tag, stories in tag_stories.items():
        seen = set()
        unique = []
        for s in stories:
            if s["id"] not in seen:
                seen.add(s["id"])
                unique.append(s)
        if len(unique) >= 3:
            unique.sort(key=lambda s: s["date"])
            topic_threads.append({"tag": tag, "stories": unique, "count": len(unique)})
    topic_threads.sort(key=lambda x: x["count"], reverse=True)

    return {
        "hot_stories": hot_stories[:10],
        "active_authors": active_authors[:10],
        "topic_threads": topic_threads[:8],
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def esc(text):
    """Minimal HTML escaping."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_summary_html(summary):
    if not summary:
        return ""
    return f'<div class="ai-summary">💡 {esc(summary)}</div>'


def render_insights_html(insights):
    if not insights:
        return ""
    parts = []

    if insights.get("hot_tags"):
        tags_html = " ".join(f'<span class="tag">{esc(t)}</span>' for t in insights["hot_tags"])
        parts.append(f'<div class="insight-tags">{tags_html}</div>')

    stats = []
    stats.append(f'均分 <b>{insights["avg_score"]}</b>')
    title_short = esc(insights["max_score"]["title"][:30])
    stats.append(f'最高 <b>{insights["max_score"]["score"]}</b>（{title_short}…）')
    stats.append(f'最热议 <b>{insights["most_discussed"]["comments"]}</b> 评论')
    parts.append(f'<div class="insight-stats">{"  ·  ".join(stats)}</div>')

    if insights.get("top_domains"):
        doms = " ".join(f'<code>{esc(d["domain"])}</code>×{d["count"]}' for d in insights["top_domains"])
        parts.append(f'<div class="insight-domains">来源: {doms}</div>')

    return f'<div class="insights">{"".join(parts)}</div>'


def render_stories_html(stories):
    rows = []
    for i, s in enumerate(stories, 1):
        hn_link = f"https://news.ycombinator.com/item?id={s['id']}"
        tags_html = ""
        if s.get("tags"):
            tags_html = " ".join(f'<span class="tag-sm">{esc(t)}</span>' for t in s["tags"][:3])
            tags_html = f' <span class="story-tags">{tags_html}</span>'
        summary_line = ""
        if s.get("summary"):
            summary_line = f'\n            <div class="story-summary">{esc(s["summary"])}</div>'
        rows.append(f"""
        <div class="story">
          <span class="rank">{i}</span>
          <div class="story-content">
            <a class="story-title" href="{esc(s['url'])}" target="_blank">{esc(s['title'])}</a>{tags_html}{summary_line}
            <div class="story-meta">
              <span class="score">▲ {s['score']}</span>
              <a class="comments" href="{hn_link}" target="_blank">💬 {s['descendants']}</a>
              <span class="author">by {esc(s['by'])}</span>
              <span class="domain">{esc(s.get('domain', ''))}</span>
            </div>
          </div>
        </div>""")
    return "\n".join(rows)


def render_trending_html(trending):
    if not trending:
        return ""
    parts = []

    if trending["hot_stories"]:
        items = []
        for s in trending["hot_stories"][:5]:
            timeline = " → ".join(
                f'{a["date"]} {PERIOD_LABELS.get(a["period"], a["period"])}(▲{a["score"]})'
                for a in s["appearances"]
            )
            items.append(f"""
          <div class="trending-item">
            <a href="{esc(s['url'])}" target="_blank">{esc(s['title'])}</a>
            <div class="trending-meta">登榜 {s['count']} 次 · 最高分 {s['peak_score']}</div>
            <div class="trending-timeline">{timeline}</div>
          </div>""")
        parts.append(f"""
      <div class="trending-block">
        <h3>🔥 持续热门</h3>{"".join(items)}
      </div>""")

    if trending["topic_threads"]:
        items = []
        for t in trending["topic_threads"][:6]:
            story_links = []
            for s in t["stories"][-5:]:
                short = esc(s["title"][:40]) + ("…" if len(s["title"]) > 40 else "")
                story_links.append(
                    f'<div class="topic-story"><a href="{esc(s["url"])}" target="_blank" '
                    f'title="{esc(s["date"])}">{short}</a> <span class="trending-meta">▲{s["score"]}</span></div>'
                )
            items.append(f"""
          <div class="trending-item">
            <span class="tag">{esc(t['tag'])}</span> <span class="trending-count">{t['count']} 篇相关</span>
            <div class="topic-stories">{"".join(story_links)}</div>
          </div>""")
        parts.append(f"""
      <div class="trending-block">
        <h3>📡 话题追踪</h3>{"".join(items)}
      </div>""")

    if trending["active_authors"]:
        items = []
        for a in trending["active_authors"][:5]:
            titles = ", ".join(esc(s["title"][:30]) for s in a["stories"][:3])
            items.append(f"""
          <div class="trending-item">
            <b>{esc(a['author'])}</b> — {len(a['stories'])} 篇上榜
            <div class="trending-meta">{titles}</div>
          </div>""")
        parts.append(f"""
      <div class="trending-block">
        <h3>✍️ 活跃作者</h3>{"".join(items)}
      </div>""")

    if not parts:
        return ""
    return f"""
    <section class="trending-section">
      <h2>📊 趋势追踪</h2>{"".join(parts)}
    </section>"""


def render_date_nav(all_data):
    """Render a sticky date navigation bar."""
    if len(all_data) <= 1:
        return ""
    links = []
    for date_str, _ in all_data:
        short = date_str[5:]  # MM-DD
        links.append(f'<a href="#day-{date_str}" class="date-nav-item">{short}</a>')
    return f'<nav class="date-nav">{" ".join(links)}</nav>'


def render_main_html(all_data, trending):
    sections = []

    # Date navigation
    date_nav = render_date_nav(all_data)

    trending_html = render_trending_html(trending)
    if trending_html:
        sections.append(trending_html)

    for date_str, day_data in all_data:
        period_blocks = []
        for period in PERIOD_ORDER:
            if period not in day_data:
                continue
            info = day_data[period]
            label = PERIOD_LABELS[period]
            summary_html = render_summary_html(info.get("summary", ""))
            insights_html = render_insights_html(info.get("insights", {}))
            stories_html = render_stories_html(info["stories"])
            period_blocks.append(f"""
      <div class="period">
        <h3>{label} <span class="update-time">{info['time']}</span></h3>
        {summary_html}
        {insights_html}
        <div class="stories">{stories_html}
        </div>
      </div>""")
        if period_blocks:
            sections.append(f"""
    <div class="day-section" id="day-{date_str}">
      <h2>{date_str}</h2>{"".join(period_blocks)}
    </div>""")

    history_link = ""
    if os.path.isdir(HISTORY_DIR) and any(
        d for d in os.listdir(HISTORY_DIR)
        if os.path.isdir(os.path.join(HISTORY_DIR, d)) and d.isdigit()
    ):
        history_link = '<div class="history-link"><a href="history/">📚 查看历史归档</a></div>'

    return MAIN_TEMPLATE.replace("{{CONTENT}}", "\n".join(sections)).replace(
        "{{HISTORY_LINK}}", history_link
    ).replace("{{DATE_NAV}}", date_nav)


# ---------------------------------------------------------------------------
# History index
# ---------------------------------------------------------------------------
def generate_history_index():
    os.makedirs(HISTORY_DIR, exist_ok=True)
    weeks = []
    for year_dir in sorted(os.listdir(HISTORY_DIR), reverse=True):
        year_path = os.path.join(HISTORY_DIR, year_dir)
        if not os.path.isdir(year_path) or not year_dir.isdigit():
            continue
        for wk in sorted(os.listdir(year_path), reverse=True):
            wk_path = os.path.join(year_path, wk)
            if not os.path.isdir(wk_path):
                continue
            files = sorted(f for f in os.listdir(wk_path) if f.endswith(".json"))
            if not files:
                continue
            date_range = f"{files[0].replace('.json', '')} ~ {files[-1].replace('.json', '')}"
            total = 0
            for fname in files:
                with open(os.path.join(wk_path, fname), "r", encoding="utf-8") as f:
                    d = json.load(f)
                for p in PERIOD_ORDER:
                    if p in d:
                        total += len(d[p].get("stories", []))
            weeks.append({
                "year": year_dir, "week": wk,
                "date_range": date_range, "days": len(files),
                "total_stories": total,
            })

    rows = []
    for w in weeks:
        rows.append(f"""
      <div class="history-week">
        <h3>{w['year']} {w['week']}</h3>
        <div class="history-meta">{w['date_range']} · {w['days']} 天 · {w['total_stories']} 条</div>
      </div>""")

    html = HISTORY_TEMPLATE.replace("{{WEEKS}}", "\n".join(rows) if rows else "<p>暂无归档数据</p>")

    with open(HISTORY_INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
CSS = """
    :root {
      --bg: #fafafa; --card-bg: #fff; --text: #1a1a1a;
      --text-secondary: #666; --accent: #ff6600; --border: #eee;
      --tag-bg: #fff3e6; --tag-text: #cc5200;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #1a1a1a; --card-bg: #2a2a2a; --text: #e0e0e0;
        --text-secondary: #999; --border: #333;
        --tag-bg: #3a2a1a; --tag-text: #ff9944;
      }
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg); color: var(--text);
      line-height: 1.6; max-width: 760px; margin: 0 auto; padding: 20px 16px;
    }
    header { text-align: center; padding: 24px 0; border-bottom: 2px solid var(--accent); margin-bottom: 24px; }
    header h1 { font-size: 1.8em; color: var(--accent); }
    header p { color: var(--text-secondary); font-size: 0.9em; margin-top: 4px; }
    .date-nav { position: sticky; top: 0; z-index: 100; background: var(--bg); padding: 10px 0; margin-bottom: 16px; display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; border-bottom: 1px solid var(--border); }
    .date-nav-item { display: inline-block; padding: 4px 14px; border-radius: 16px; background: var(--card-bg); color: var(--text); text-decoration: none; font-size: 0.85em; font-weight: 500; border: 1px solid var(--border); transition: all 0.15s; }
    .date-nav-item:hover, .date-nav-item:target { background: var(--accent); color: #fff; border-color: var(--accent); }
    .day-section { margin-bottom: 32px; scroll-margin-top: 60px; }
    .day-section > h2 { font-size: 1.2em; padding: 8px 0; border-bottom: 1px solid var(--border); margin-bottom: 12px; }
    .period { margin-bottom: 20px; }
    .period h3 { font-size: 1em; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
    .update-time { font-size: 0.8em; color: var(--text-secondary); font-weight: normal; }
    .ai-summary { background: linear-gradient(135deg, var(--tag-bg), var(--card-bg)); border-radius: 10px; padding: 12px 16px; margin-bottom: 12px; font-size: 0.92em; line-height: 1.7; border: 1px solid var(--border); }
    .insights { background: var(--card-bg); border-left: 3px solid var(--accent); padding: 10px 14px; margin-bottom: 12px; border-radius: 0 8px 8px 0; font-size: 0.85em; }
    .insight-tags { margin-bottom: 4px; }
    .insight-stats { color: var(--text-secondary); }
    .insight-stats b { color: var(--accent); }
    .insight-domains { color: var(--text-secondary); margin-top: 2px; }
    .insight-domains code { background: var(--tag-bg); padding: 1px 5px; border-radius: 3px; font-size: 0.9em; }
    .tag { display: inline-block; background: var(--tag-bg); color: var(--tag-text); padding: 2px 8px; border-radius: 10px; font-size: 0.75em; font-weight: 500; }
    .tag-sm { display: inline-block; background: var(--tag-bg); color: var(--tag-text); padding: 1px 6px; border-radius: 8px; font-size: 0.7em; }
    .story-tags { margin-left: 4px; }
    .story { display: flex; align-items: flex-start; gap: 12px; padding: 10px 12px; background: var(--card-bg); border-radius: 8px; margin-bottom: 6px; transition: transform 0.1s; }
    .story:hover { transform: translateX(4px); }
    .rank { font-size: 1.1em; font-weight: bold; color: var(--accent); min-width: 24px; text-align: center; padding-top: 2px; }
    .story-content { flex: 1; min-width: 0; }
    .story-title { color: var(--text); text-decoration: none; font-weight: 500; font-size: 0.95em; display: inline; }
    .story-title:hover { color: var(--accent); }
    .story-summary { color: var(--text-secondary); font-size: 0.83em; margin-top: 3px; line-height: 1.5; }
    .story-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 0.8em; color: var(--text-secondary); margin-top: 4px; }
    .story-meta a { color: var(--text-secondary); text-decoration: none; }
    .story-meta a:hover { color: var(--accent); }
    .score { color: var(--accent); font-weight: 500; }
    .domain { font-size: 0.9em; }
    .trending-section { background: var(--card-bg); border-radius: 12px; padding: 20px; margin-bottom: 32px; border: 1px solid var(--border); }
    .trending-section > h2 { font-size: 1.2em; margin-bottom: 16px; }
    .trending-block { margin-bottom: 18px; }
    .trending-block h3 { font-size: 1em; margin-bottom: 8px; }
    .trending-item { padding: 8px 0; border-bottom: 1px solid var(--border); }
    .trending-item:last-child { border-bottom: none; }
    .trending-item a { color: var(--text); text-decoration: none; font-weight: 500; }
    .trending-item a:hover { color: var(--accent); }
    .trending-meta { font-size: 0.8em; color: var(--text-secondary); margin-top: 2px; }
    .trending-timeline { font-size: 0.75em; color: var(--text-secondary); margin-top: 4px; word-break: break-all; }
    .trending-count { font-size: 0.8em; color: var(--text-secondary); }
    .topic-stories { margin-top: 4px; }
    .topic-story { font-size: 0.85em; padding: 2px 0; }
    .topic-story a { font-weight: normal; }
    .history-link { text-align: center; margin: 24px 0; }
    .history-link a { color: var(--accent); text-decoration: none; font-weight: 500; padding: 8px 20px; border: 1px solid var(--accent); border-radius: 20px; }
    .history-link a:hover { background: var(--accent); color: #fff; }
    footer { text-align: center; padding: 24px 0; color: var(--text-secondary); font-size: 0.8em; border-top: 1px solid var(--border); }
    footer a { color: var(--accent); text-decoration: none; }
    .history-week { padding: 12px 0; border-bottom: 1px solid var(--border); }
    .history-week h3 { font-size: 1em; color: var(--accent); }
    .history-meta { font-size: 0.85em; color: var(--text-secondary); }
    .back-link { margin-bottom: 20px; }
    .back-link a { color: var(--accent); text-decoration: none; }
"""

MAIN_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HN Daily Top 10</title>
  <style>""" + CSS + """</style>
</head>
<body>
  <header>
    <h1>📰 HN Daily Top 10</h1>
    <p>每日早中晚自动汇总 Hacker News 热门 · 附趋势洞察</p>
  </header>
  {{DATE_NAV}}
  <main>
{{CONTENT}}
  </main>
  {{HISTORY_LINK}}
  <footer>
    Auto-generated by <a href="https://github.com/ZiNai/ZiNai.github.io">GitHub Actions</a> ·
    Data from <a href="https://news.ycombinator.com">Hacker News</a>
  </footer>
</body>
</html>
"""

HISTORY_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HN Daily - 历史归档</title>
  <style>""" + CSS + """</style>
</head>
<body>
  <header>
    <h1>📚 历史归档</h1>
    <p>按周归档的 Hacker News Top 10 历史记录</p>
  </header>
  <div class="back-link"><a href="../">← 返回最新</a></div>
  <main>
{{WEEKS}}
  </main>
  <footer>
    <a href="../">← 返回最新</a>
  </footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    now = datetime.now(BJT)
    print(f"[{now.strftime('%Y-%m-%d %H:%M')} BJT] Fetching HN Top {TOP_N}...")

    stories = fetch_top_stories(TOP_N)
    print(f"Fetched {len(stories)} stories")

    date_str, period = save_data(stories, now)
    print(f"Saved data: {date_str}/{period}")

    archived = archive_old_data(now)
    if archived:
        print(f"Archived {len(archived)} day(s) to history/")

    all_flat = load_all_data_flat()
    trending = build_trending(all_flat)
    print(f"Trending: {len(trending['hot_stories'])} hot stories, "
          f"{len(trending['topic_threads'])} topic threads, "
          f"{len(trending['active_authors'])} active authors")

    recent_data = load_recent_data()
    html = render_main_html(recent_data, trending)
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated: {OUTPUT_HTML}")

    generate_history_index()
    print(f"Generated: {HISTORY_INDEX_HTML}")


if __name__ == "__main__":
    main()
