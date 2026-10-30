#!/usr/bin/env python3
"""RSS/Atom helpers + TLS-blocked feed cache for ACR map enrichment.

czdefence.cz (and rarely others) close TLS from this box mid-handshake.
Daily agent should WebFetch the feed URL and save either:
  - data/feed_cache/<slug>.xml  (raw RSS/Atom), or
  - data/feed_cache/<slug>.json ({"feed_url","items":[{"title","url","pubDate"}]})
Then ingest via ingest_rss_feeds.py / enrich_sites.py.
"""
from __future__ import annotations

import json
import re
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FEED_CACHE = ROOT / "data" / "feed_cache"
TIMEOUT = 25
UA = "ACR-Map-Enrichment/1.0 (+research; polite)"

# site homepage (normalized netloc without www) -> preferred feed URL(s)
KNOWN_FEEDS: dict[str, list[str]] = {
    "czdefence.cz": ["https://www.czdefence.cz/rss2"],
}

# optional explicit slug for cache files
FEED_SLUGS: dict[str, str] = {
    "czdefence.cz": "czdefence",
}


def host_key(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def czech_date(raw: str) -> str:
    """Format to 'DD. MM. YYYY' when parseable; else return stripped raw."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return f"{dt.day:02d}. {dt.month:02d}. {dt.year}"
    except Exception:
        pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{int(m.group(3)):02d}. {int(m.group(2)):02d}. {int(m.group(1))}"
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", raw)
    if m:
        return f"{int(m.group(1)):02d}. {int(m.group(2)):02d}. {int(m.group(3))}"
    return raw


def iso_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_rss_xml(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return items
    # RSS 2.0
    for node in root.iter():
        if _strip_ns(node.tag).lower() != "item":
            continue
        title = link = pub = ""
        for child in list(node):
            t = _strip_ns(child.tag).lower()
            if t == "title" and child.text:
                title = child.text.strip()
            elif t == "link" and (child.text or child.get("href")):
                link = (child.text or child.get("href") or "").strip()
            elif t == "pubdate" and child.text:
                pub = child.text.strip()
            elif t == "guid" and not link and child.text:
                link = child.text.strip()
        if title:
            items.append({"title": title, "url": link, "pubDate": pub, "date": czech_date(pub)})
    if items:
        return items
    # Atom
    for node in root.iter():
        if _strip_ns(node.tag).lower() != "entry":
            continue
        title = link = pub = ""
        for child in list(node):
            t = _strip_ns(child.tag).lower()
            if t == "title" and child.text:
                title = child.text.strip()
            elif t == "link":
                href = child.get("href") or (child.text or "").strip()
                if href and (not link or child.get("rel") in (None, "alternate")):
                    link = href
            elif t in ("updated", "published") and child.text and not pub:
                pub = child.text.strip()
        if title:
            items.append({"title": title, "url": link, "pubDate": pub, "date": czech_date(pub)})
    return items


def http_get(url: str) -> tuple[str | None, str | None]:
    """Return (body, error). Verifying TLS; never silently disable verify."""
    req = Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*;q=0.8"},
        method="GET",
    )
    ctx = ssl.create_default_context()
    try:
        with urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            raw = resp.read(3_000_000)
            charset = "utf-8"
            ctype = resp.headers.get("content-type", "")
            m = re.search(r"charset=([\w-]+)", ctype, re.I)
            if m:
                charset = m.group(1)
            return raw.decode(charset, errors="replace"), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def load_feed_cache(slug: str) -> tuple[list[dict[str, str]], str | None]:
    """Load items from feed_cache JSON or XML. Returns (items, note)."""
    FEED_CACHE.mkdir(parents=True, exist_ok=True)
    json_path = FEED_CACHE / f"{slug}.json"
    xml_path = FEED_CACHE / f"{slug}.xml"
    if json_path.exists():
        try:
            doc = json.loads(json_path.read_text(encoding="utf-8"))
            items = []
            for it in doc.get("items") or []:
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                pub = it.get("pubDate") or it.get("date") or ""
                items.append(
                    {
                        "title": title,
                        "url": (it.get("url") or it.get("link") or "").strip(),
                        "pubDate": pub,
                        "date": czech_date(pub),
                    }
                )
            if items:
                via = doc.get("fetched_via") or "feed_cache_json"
                return items, f"feed_cache:{json_path.name};via={via}"
        except Exception as e:
            return [], f"feed_cache_json_err:{e}"
    if xml_path.exists():
        try:
            text = xml_path.read_text(encoding="utf-8", errors="replace")
            items = parse_rss_xml(text)
            if items:
                return items, f"feed_cache:{xml_path.name}"
        except Exception as e:
            return [], f"feed_cache_xml_err:{e}"
    return [], None


def fetch_known_feed(page_url: str) -> dict[str, Any]:
    """Try live RSS then feed_cache. Returns enrichment-like fragment."""
    hk = host_key(page_url)
    feeds = KNOWN_FEEDS.get(hk) or []
    slug = FEED_SLUGS.get(hk) or re.sub(r"[^a-z0-9]+", "-", hk).strip("-")
    out: dict[str, Any] = {
        "feed_urls_tried": list(feeds),
        "feed_source": None,
        "feed_error": None,
        "items": [],
    }
    errors: list[str] = []
    for feed_url in feeds:
        body, err = http_get(feed_url)
        if err:
            errors.append(f"{feed_url}: {err}")
            continue
        if not body:
            errors.append(f"{feed_url}: empty")
            continue
        items = parse_rss_xml(body)
        if items:
            # refresh cache for next time
            FEED_CACHE.mkdir(parents=True, exist_ok=True)
            (FEED_CACHE / f"{slug}.xml").write_text(body, encoding="utf-8")
            cache_doc = {
                "source": page_url,
                "feed_url": feed_url,
                "fetched_via": "direct_https",
                "fetched_at": datetime.now().astimezone().isoformat(),
                "items": [
                    {"title": i["title"], "url": i["url"], "pubDate": i.get("pubDate") or i.get("date")}
                    for i in items[:40]
                ],
            }
            (FEED_CACHE / f"{slug}.json").write_text(
                json.dumps(cache_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            out["items"] = items
            out["feed_source"] = f"live:{feed_url}"
            return out
        errors.append(f"{feed_url}: parse_empty")

    items, note = load_feed_cache(slug)
    if items:
        out["items"] = items
        out["feed_source"] = note
        if errors:
            out["feed_error"] = "; ".join(errors)
            # clear that we fell back due to TLS/network
            out["feed_error"] = (
                "direct_fetch_failed_using_cache: " + out["feed_error"]
            )
        return out

    out["feed_error"] = "; ".join(errors) if errors else "no_known_feed_or_cache"
    return out


def items_to_news_page(page_url: str, feed_url: str | None, items: list[dict[str, str]]) -> dict[str, Any]:
    if not items:
        return {}
    top = items[0]
    return {
        "url": feed_url or page_url,
        "title": "RSS",
        "last_item": top["title"],
        "last_item_date": top.get("date") or czech_date(top.get("pubDate") or ""),
        "last_item_url": top.get("url") or page_url,
        "items": [
            {
                "title": it["title"],
                "date": it.get("date") or czech_date(it.get("pubDate") or ""),
                "url": it.get("url") or "",
            }
            for it in items[:20]
        ],
    }


def apply_feed_to_entry(page_url: str, entry: dict[str, Any]) -> bool:
    """Mutate enrichment entry with feed data if available. Returns True if applied."""
    hk = host_key(page_url)
    if hk not in KNOWN_FEEDS and not (FEED_CACHE / f"{FEED_SLUGS.get(hk, hk)}.json").exists():
        # still try if cache slug exists for host
        slug = FEED_SLUGS.get(hk) or re.sub(r"[^a-z0-9]+", "-", hk).strip("-")
        if not (FEED_CACHE / f"{slug}.json").exists() and not (FEED_CACHE / f"{slug}.xml").exists():
            if hk not in KNOWN_FEEDS:
                return False

    result = fetch_known_feed(page_url)
    items = result.get("items") or []
    if not items:
        if result.get("feed_error"):
            entry["error"] = ((entry.get("error") or "") + "; feed:" + result["feed_error"]).strip("; ")
        return False

    feed_url = (KNOWN_FEEDS.get(hk) or [None])[0]
    np = items_to_news_page(page_url, feed_url, items)
    # replace/insert RSS news page at front
    news_pages = [p for p in (entry.get("news_pages") or []) if p.get("title") != "RSS"]
    news_pages.insert(0, np)
    entry["news_pages"] = news_pages
    entry["last_post_title"] = items[0]["title"]
    entry["last_post_date"] = items[0].get("date") or czech_date(items[0].get("pubDate") or "")
    entry["feed_source"] = result.get("feed_source")
    if result.get("feed_error"):
        entry["feed_error"] = result["feed_error"]
    if not entry.get("cms") or entry.get("cms") == "unknown":
        entry["cms"] = "custom"
        entry["cms_evidence"] = (entry.get("cms_evidence") or "") + "; rss_feed"
    return True
