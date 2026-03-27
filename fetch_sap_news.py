#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import os
import re
import sys
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable, Optional

JST = timezone(timedelta(hours=9))
USER_AGENT = "Mozilla/5.0 (compatible; SAPNewsDigest/1.0; +https://github.com/)"
DEFAULT_OUTPUT = Path("output")

FEEDS = [
    {
        "name": "SAP News Center",
        "url": "https://news.sap.com/feed/",
        "category": "official",
    },
    {
        "name": "Google News - SAP",
        "url": "https://news.google.com/rss/search?q=SAP+when:2d&hl=en-US&gl=US&ceid=US:en",
        "category": "web",
    },
]

KEYWORDS = {
    "AI": ["ai", "joule", "generative", "genai", "agent"],
    "Cloud": ["cloud", "s/4hana", "rise with sap", "erp", "hana"],
    "HR": ["successfactors", "recruit", "hcm", "hr"],
    "Finance": ["finance", "revenue", "earnings", "forecast", "margin"],
    "Partnership": ["partner", "partnership", "alliance", "integration"],
    "Customer Story": ["customer", "transformation", "deployed", "secures", "optimizes"],
}

@dataclass
class Article:
    title: str
    url: str
    source: str
    published_jst: str
    published_date_jst: str
    category: str
    tags: list[str]
    summary: str


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_feed_date(text: str) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST)
    except Exception:
        return None


def unwrap_google_news(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if "news.google.com" not in parsed.netloc:
        return url
    qs = urllib.parse.parse_qs(parsed.query)
    if "url" in qs and qs["url"]:
        return qs["url"][0]
    return url


def extract_source(item: ET.Element, fallback: str) -> str:
    source = item.findtext("source")
    if source:
        return source.strip()
    ns_source = item.findtext("{http://search.yahoo.com/mrss/}source")
    if ns_source:
        return ns_source.strip()
    title = item.findtext("title") or fallback
    m = re.search(r"\s+-\s+([^\-]+)$", title)
    return m.group(1).strip() if m else fallback


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_summary(title: str, desc: str) -> str:
    base = clean_text(desc)
    if not base:
        return "要約なし"
    if title and base.lower().startswith(title.lower()):
        base = base[len(title):].strip(" -:–")
    return textwrap.shorten(base, width=180, placeholder="…")


def infer_tags(title: str, summary: str) -> list[str]:
    hay = f"{title} {summary}".lower()
    tags = [label for label, words in KEYWORDS.items() if any(w in hay for w in words)]
    return tags[:3] or ["General"]


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    q = [(k, v) for k, v in q if not k.startswith("utm_")]
    normalized = parsed._replace(query=urllib.parse.urlencode(q), fragment="")
    return urllib.parse.urlunparse(normalized)


def iter_items(feed_bytes: bytes) -> Iterable[ET.Element]:
    root = ET.fromstring(feed_bytes)
    channel = root.find("channel")
    if channel is not None:
        yield from channel.findall("item")
        return
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    yield from root.findall("atom:entry", ns)


def parse_article(item: ET.Element, feed_name: str, category: str) -> Optional[Article]:
    title = clean_text(item.findtext("title"))
    link = item.findtext("link") or ""
    link = unwrap_google_news(link.strip())
    link = normalize_url(link)
    published = (
        item.findtext("pubDate")
        or item.findtext("published")
        or item.findtext("updated")
        or ""
    )
    dt = parse_feed_date(published)
    if not title or not link or not dt:
        return None
    desc = item.findtext("description") or item.findtext("summary") or ""
    summary = make_summary(title, desc)
    source = extract_source(item, feed_name)
    return Article(
        title=title,
        url=link,
        source=source,
        published_jst=dt.strftime("%Y-%m-%d %H:%M JST"),
        published_date_jst=dt.strftime("%Y-%m-%d"),
        category=category,
        tags=infer_tags(title, summary),
        summary=summary,
    )


def collect_articles(target_date_jst: str) -> list[Article]:
    results: list[Article] = []
    seen: set[str] = set()
    seen_titles: set[str] = set()

    for feed in FEEDS:
        try:
            raw = fetch_bytes(feed["url"])
            for item in iter_items(raw):
                article = parse_article(item, feed["name"], feed["category"])
                if article is None:
                    continue
                if article.published_date_jst != target_date_jst:
                    continue
                key = article.url.lower()
                title_key = re.sub(r"\W+", "", article.title.lower())
                if key in seen or title_key in seen_titles:
                    continue
                seen.add(key)
                seen_titles.add(title_key)
                results.append(article)
        except Exception as e:
            print(f"[WARN] feed fetch failed: {feed['name']} - {e}", file=sys.stderr)

    results.sort(key=lambda x: (x.published_jst, x.source, x.title), reverse=True)
    return results


def write_json(path: Path, articles: list[Article], target_date_jst: str) -> None:
    payload = {
        "target_date_jst": target_date_jst,
        "generated_at_jst": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "count": len(articles),
        "articles": [asdict(a) for a in articles],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, articles: list[Article]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["published_jst", "source", "category", "title", "tags", "summary", "url"])
        for a in articles:
            writer.writerow([a.published_jst, a.source, a.category, a.title, ", ".join(a.tags), a.summary, a.url])


def write_markdown(path: Path, articles: list[Article], target_date_jst: str) -> None:
    lines = [
        f"# SAPニュース日次抽出レポート ({target_date_jst} JST)",
        "",
        f"- 生成日時: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S JST')}",
        f"- 件数: {len(articles)}件",
        "- ソース: SAP News Center RSS / Google News RSS (SAP検索)",
        "",
    ]
    if not articles:
        lines += ["対象日の記事は見つかりませんでした。", ""]
    else:
        for idx, a in enumerate(articles, start=1):
            tags = " / ".join(a.tags)
            lines += [
                f"## {idx}. {a.title}",
                f"- 公開: {a.published_jst}",
                f"- ソース: {a.source}",
                f"- 区分: {a.category}",
                f"- タグ: {tags}",
                f"- URL: {a.url}",
                f"- 要約: {a.summary}",
                "",
            ]
    path.write_text("\n".join(lines), encoding="utf-8")


def post_slack_if_configured(articles: list[Article], target_date_jst: str) -> None:
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    head = f"SAPニュース抽出 {target_date_jst} JST / {len(articles)}件"
    if articles:
        bullets = [f"• {a.title} ({a.source})" for a in articles[:10]]
        text = head + "\n" + "\n".join(bullets)
    else:
        text = head + "\n対象記事なし"
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20):
        pass


def main() -> int:
    out_dir = Path(os.getenv("OUTPUT_DIR", str(DEFAULT_OUTPUT)))
    out_dir.mkdir(parents=True, exist_ok=True)

    # default target = yesterday in JST
    target_date_jst = os.getenv("TARGET_DATE_JST", "").strip()
    if not target_date_jst:
        target_date_jst = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")

    articles = collect_articles(target_date_jst)
    write_json(out_dir / "sap_news.json", articles, target_date_jst)
    write_csv(out_dir / "sap_news.csv", articles)
    write_markdown(out_dir / "sap_news.md", articles, target_date_jst)
    post_slack_if_configured(articles, target_date_jst)

    print(f"done: {len(articles)} articles for {target_date_jst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
