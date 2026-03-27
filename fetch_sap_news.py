import os
import json
import csv
import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, parse_qs, unquote

import feedparser
import requests
from openai import OpenAI


# =========================
# 基本設定
# =========================
JST = timezone(timedelta(hours=9))
OUTPUT_DIR = "output"
RSS_URLS = [
    "https://news.sap.com/feed/",
    "https://news.google.com/rss/search?q=SAP&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=SAP%20software&hl=en-US&gl=US&ceid=US:en",
]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_TO_USER_ID = os.getenv("LINE_TO_USER_ID")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# =========================
# ユーティリティ
# =========================
def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def yesterday_range_jst():
    now_jst = datetime.now(JST)
    today_jst = now_jst.date()
    yesterday_jst = today_jst - timedelta(days=1)

    start = datetime(yesterday_jst.year, yesterday_jst.month, yesterday_jst.day, 0, 0, 0, tzinfo=JST)
    end = start + timedelta(days=1)
    return start, end


def parse_entry_datetime(entry):
    candidates = []

    if getattr(entry, "published", None):
        candidates.append(entry.published)
    if getattr(entry, "updated", None):
        candidates.append(entry.updated)
    if getattr(entry, "created", None):
        candidates.append(entry.created)

    for value in candidates:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(JST)
        except Exception:
            pass

    return None


def extract_real_url(url):
    """
    Google News RSSのリンクは中継URLのことがあるので、
    可能なら元URLっぽいものを抜く。
    """
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)

        for key in ("url", "u"):
            if key in qs and qs[key]:
                return unquote(qs[key][0])

        return url
    except Exception:
        return url


def strip_html(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_title(title):
    title = (title or "").strip().lower()
    title = re.sub(r"\s+", " ", title)
    return title


def summarize_to_japanese(title, summary, source, link):
    """
    記事タイトル・要約文をもとに、日本語で短く要約する。
    """
    base_text = f"""
以下のニュース情報を、日本語で簡潔に要約してください。

条件:
- 2〜3文
- ビジネス視点で重要点のみ
- 誇張しない
- 不明なことは書かない
- 日本語として自然に
- 1文目で何が起きたか
- 2文目で企業・市場への意味合い

タイトル: {title}
要約文: {summary}
媒体: {source}
URL: {link}
""".strip()

    if not client:
        return "要約未生成（OPENAI_API_KEY未設定）"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "あなたは企業ニュースを簡潔に整理する編集者です。事実ベースで日本語要約してください。"
                },
                {
                    "role": "user",
                    "content": base_text
                }
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"要約生成エラー: {str(e)}"


def fetch_articles():
    start_jst, end_jst = yesterday_range_jst()
    seen = set()
    results = []

    for rss_url in RSS_URLS:
        feed = feedparser.parse(rss_url)

        for entry in feed.entries:
            dt_jst = parse_entry_datetime(entry)
            if not dt_jst:
                continue

            if not (start_jst <= dt_jst < end_jst):
                continue

            title = getattr(entry, "title", "").strip()
            link = extract_real_url(getattr(entry, "link", "").strip())
            summary = strip_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            source = ""

            if getattr(entry, "source", None):
                if isinstance(entry.source, dict):
                    source = entry.source.get("title", "")
                else:
                    source = str(entry.source)

            if not source:
                source = urlparse(link).netloc or "unknown"

            dedupe_key = (normalize_title(title), link)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            ja_summary = summarize_to_japanese(title, summary, source, link)

            results.append({
                "published_jst": dt_jst.strftime("%Y-%m-%d %H:%M:%S JST"),
                "title": title,
                "source": source,
                "link": link,
                "summary_en_or_original": summary,
                "summary_ja": ja_summary,
            })

    results.sort(key=lambda x: x["published_jst"], reverse=True)
    return results


def write_json(items):
    path = os.path.join(OUTPUT_DIR, "sap_news.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def write_csv(items):
    path = os.path.join(OUTPUT_DIR, "sap_news.csv")
    fieldnames = [
        "published_jst",
        "title",
        "source",
        "link",
        "summary_en_or_original",
        "summary_ja",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(items)


def write_markdown(items):
    start_jst, end_jst = yesterday_range_jst()
    target_date = start_jst.date().isoformat()

    path = os.path.join(OUTPUT_DIR, "sap_news.md")
    lines = []
    lines.append(f"# SAPニュースまとめ（{target_date}分 / JST基準）")
    lines.append("")
    lines.append(f"- 抽出件数: {len(items)}件")
    lines.append("")

    if not items:
        lines.append("昨日公開分の記事は見つかりませんでした。")
    else:
        for i, item in enumerate(items, start=1):
            lines.append(f"## {i}. {item['title']}")
            lines.append("")
            lines.append(f"- 公開日時: {item['published_jst']}")
            lines.append(f"- 媒体: {item['source']}")
            lines.append(f"- URL: {item['link']}")
            lines.append(f"- 日本語要約: {item['summary_ja']}")
            lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_line_message(items):
    start_jst, _ = yesterday_range_jst()
    target_date = start_jst.date().isoformat()

    if not items:
        return f"【SAPニュースまとめ】\n{target_date}（JST基準）\n昨日公開分の記事は見つかりませんでした。"

    lines = [f"【SAPニュースまとめ】", f"{target_date}（JST基準）", ""]

    max_items = min(len(items), 5)
    for idx, item in enumerate(items[:max_items], start=1):
        lines.append(f"{idx}. {item['title']}")
        lines.append(f"要約: {item['summary_ja']}")
        lines.append(f"URL: {item['link']}")
        lines.append("")

    if len(items) > max_items:
        lines.append(f"ほか {len(items) - max_items} 件はGitHubの output/sap_news.md を確認してください。")

    message = "\n".join(lines)

    # LINEの可読性のため長すぎる場合は切る
    if len(message) > 4500:
        message = message[:4500] + "\n\n（長いため途中まで表示）"

    return message


def send_line_push_message(text):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("LINE送信スキップ: LINE_CHANNEL_ACCESS_TOKEN 未設定")
        return

    if not LINE_TO_USER_ID:
        print("LINE送信スキップ: LINE_TO_USER_ID 未設定")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "to": LINE_TO_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    print(f"LINE response status: {response.status_code}")
    print(response.text)
    response.raise_for_status()


def main():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY が未設定です。GitHub Secrets に登録してください。")

    ensure_output_dir()
    items = fetch_articles()
    write_json(items)
    write_csv(items)
    write_markdown(items)

    line_message = build_line_message(items)
    send_line_push_message(line_message)

    print(f"Done. Collected {len(items)} articles.")


if __name__ == "__main__":
    main()
