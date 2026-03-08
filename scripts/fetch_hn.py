#!/usr/bin/env python3
"""Fetch Hacker News Top 10 stories and generate a static HTML page."""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
TOP_N = 10
BJT = timezone(timedelta(hours=8))
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "hn", "data")
OUTPUT_HTML = os.path.join(os.path.dirname(__file__), "..", "hn", "index.html")
KEEP_DAYS = 7


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hn-daily-bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_top_stories(n=TOP_N):
    ids = fetch_json(HN_TOP_URL)[:n]
    stories = []
    for sid in ids:
        item = fetch_json(HN_ITEM_URL.format(sid))
        if item and item.get("type") == "story":
            stories.append({
                "id": item["id"],
                "title": item.get("title", ""),
                "url": item.get("url", f"https://news.ycombinator.com/item?id={item['id']}"),
                "score": item.get("score", 0),
                "descendants": item.get("descendants", 0),
                "by": item.get("by", ""),
            })
    return stories


def get_period_label(now):
    hour = now.hour
    if hour < 11:
        return "morning", "🌅 早间"
    elif hour < 17:
        return "noon", "☀️ 午间"
    else:
        return "evening", "🌙 晚间"


def save_data(stories, now):
    os.makedirs(DATA_DIR, exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    period_key, _ = get_period_label(now)
    filepath = os.path.join(DATA_DIR, f"{date_str}.json")

    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            day_data = json.load(f)
    else:
        day_data = {}

    day_data[period_key] = {
        "time": now.strftime("%Y-%m-%d %H:%M"),
        "stories": stories,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(day_data, f, ensure_ascii=False, indent=2)

    return date_str, period_key


def load_all_data():
    """Load all JSON data files, sorted by date descending."""
    os.makedirs(DATA_DIR, exist_ok=True)
    files = sorted(
        [f for f in os.listdir(DATA_DIR) if f.endswith(".json")],
        reverse=True,
    )[:KEEP_DAYS]
    all_data = []
    for fname in files:
        date_str = fname.replace(".json", "")
        with open(os.path.join(DATA_DIR, fname), "r", encoding="utf-8") as f:
            day_data = json.load(f)
        all_data.append((date_str, day_data))
    return all_data


def cleanup_old_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    files = sorted([f for f in os.listdir(DATA_DIR) if f.endswith(".json")])
    while len(files) > KEEP_DAYS:
        os.remove(os.path.join(DATA_DIR, files.pop(0)))


def render_stories_html(stories):
    rows = []
    for i, s in enumerate(stories, 1):
        hn_link = f"https://news.ycombinator.com/item?id={s['id']}"
        rows.append(f"""
        <div class="story">
          <span class="rank">{i}</span>
          <div class="story-content">
            <a class="story-title" href="{s['url']}" target="_blank">{s['title']}</a>
            <div class="story-meta">
              <span class="score">▲ {s['score']}</span>
              <a class="comments" href="{hn_link}" target="_blank">💬 {s['descendants']}</a>
              <span class="author">by {s['by']}</span>
            </div>
          </div>
        </div>""")
    return "\n".join(rows)


PERIOD_ORDER = ["morning", "noon", "evening"]
PERIOD_LABELS = {"morning": "🌅 早间", "noon": "☀️ 午间", "evening": "🌙 晚间"}


def render_html(all_data):
    sections = []
    for date_str, day_data in all_data:
        period_blocks = []
        for period in PERIOD_ORDER:
            if period not in day_data:
                continue
            info = day_data[period]
            label = PERIOD_LABELS[period]
            time_str = info["time"]
            stories_html = render_stories_html(info["stories"])
            period_blocks.append(f"""
      <div class="period">
        <h3>{label} <span class="update-time">{time_str}</span></h3>
        <div class="stories">{stories_html}
        </div>
      </div>""")
        if period_blocks:
            sections.append(f"""
    <div class="day-section">
      <h2>{date_str}</h2>
      {"".join(period_blocks)}
    </div>""")

    return HTML_TEMPLATE.replace("{{CONTENT}}", "\n".join(sections))


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HN Daily Top 10</title>
  <style>
    :root {
      --bg: #fafafa;
      --card-bg: #fff;
      --text: #1a1a1a;
      --text-secondary: #666;
      --accent: #ff6600;
      --border: #eee;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #1a1a1a;
        --card-bg: #2a2a2a;
        --text: #e0e0e0;
        --text-secondary: #999;
        --border: #333;
      }
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      max-width: 720px;
      margin: 0 auto;
      padding: 20px 16px;
    }
    header {
      text-align: center;
      padding: 24px 0;
      border-bottom: 2px solid var(--accent);
      margin-bottom: 24px;
    }
    header h1 {
      font-size: 1.8em;
      color: var(--accent);
    }
    header p {
      color: var(--text-secondary);
      font-size: 0.9em;
      margin-top: 4px;
    }
    .day-section {
      margin-bottom: 32px;
    }
    .day-section > h2 {
      font-size: 1.2em;
      padding: 8px 0;
      border-bottom: 1px solid var(--border);
      margin-bottom: 12px;
    }
    .period {
      margin-bottom: 20px;
    }
    .period h3 {
      font-size: 1em;
      margin-bottom: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .update-time {
      font-size: 0.8em;
      color: var(--text-secondary);
      font-weight: normal;
    }
    .story {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 10px 12px;
      background: var(--card-bg);
      border-radius: 8px;
      margin-bottom: 6px;
      transition: transform 0.1s;
    }
    .story:hover {
      transform: translateX(4px);
    }
    .rank {
      font-size: 1.1em;
      font-weight: bold;
      color: var(--accent);
      min-width: 24px;
      text-align: center;
      padding-top: 2px;
    }
    .story-content {
      flex: 1;
      min-width: 0;
    }
    .story-title {
      color: var(--text);
      text-decoration: none;
      font-weight: 500;
      font-size: 0.95em;
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .story-title:hover {
      color: var(--accent);
    }
    .story-meta {
      display: flex;
      gap: 12px;
      font-size: 0.8em;
      color: var(--text-secondary);
      margin-top: 4px;
    }
    .story-meta a {
      color: var(--text-secondary);
      text-decoration: none;
    }
    .story-meta a:hover {
      color: var(--accent);
    }
    .score { color: var(--accent); font-weight: 500; }
    footer {
      text-align: center;
      padding: 24px 0;
      color: var(--text-secondary);
      font-size: 0.8em;
      border-top: 1px solid var(--border);
    }
    footer a { color: var(--accent); text-decoration: none; }
  </style>
</head>
<body>
  <header>
    <h1>📰 HN Daily Top 10</h1>
    <p>每日早中晚自动汇总 Hacker News 热门</p>
  </header>
  <main>
{{CONTENT}}
  </main>
  <footer>
    Auto-generated by <a href="https://github.com/ZiNai/ZiNai.github.io">GitHub Actions</a> ·
    Data from <a href="https://news.ycombinator.com">Hacker News</a>
  </footer>
</body>
</html>
"""


def main():
    now = datetime.now(BJT)
    print(f"[{now.strftime('%Y-%m-%d %H:%M')} BJT] Fetching HN Top {TOP_N}...")

    stories = fetch_top_stories(TOP_N)
    print(f"Fetched {len(stories)} stories")

    date_str, period = save_data(stories, now)
    print(f"Saved data: {date_str}/{period}")

    cleanup_old_data()

    all_data = load_all_data()
    html = render_html(all_data)

    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
