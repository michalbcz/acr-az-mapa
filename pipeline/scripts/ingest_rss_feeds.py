#!/usr/bin/env python3
"""Ingest known RSS feeds (live or feed_cache) into enrichment + captures.

Usage:
  python3 scripts/ingest_rss_feeds.py              # all known feeds
  python3 scripts/ingest_rss_feeds.py --only czdefence.cz
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from feed_utils import (  # noqa: E402
    KNOWN_FEEDS,
    apply_feed_to_entry,
    czech_date,
    fetch_known_feed,
    host_key,
    iso_date,
)

ENRICH = ROOT / "data" / "enrichment.json"
CAPTURES = ROOT / "data" / "captures.jsonl"

# Homepage URLs we maintain in captures for each known feed host
HOME_URLS: dict[str, str] = {
    "czdefence.cz": "https://www.czdefence.cz/",
}


def load_enrichment() -> dict:
    if ENRICH.exists():
        return json.loads(ENRICH.read_text(encoding="utf-8"))
    return {}


def save_enrichment(data: dict) -> None:
    ENRICH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_captures_from_feed(home: str, title: str, date_cz: str, note: str) -> bool:
    if not CAPTURES.exists():
        return False
    rows = []
    changed = False
    for line in CAPTURES.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            rows.append(line)
            continue
        if row.get("url", "").rstrip("/") != home.rstrip("/"):
            rows.append(json.dumps(row, ensure_ascii=False))
            continue
        old_iso = iso_date(str(row.get("last_post_date") or ""))
        new_iso = iso_date(date_cz)
        better = False
        if title and (not row.get("last_post_title") or (new_iso and old_iso and new_iso > old_iso)):
            better = True
        elif title and new_iso and not old_iso:
            better = True
        elif title and date_cz and row.get("last_post_title") != title:
            # same-day refresh or missing iso
            if not old_iso or not new_iso or new_iso >= old_iso:
                better = True
        if better:
            row["last_post_title"] = title
            row["last_post_date"] = date_cz
            notes = row.get("notes") or ""
            tag = "rss_feed_refresh"
            if tag not in notes:
                row["notes"] = (notes + "; " if notes else "") + tag
            if note and note not in (row.get("notes") or ""):
                row["notes"] = (row.get("notes") or "") + f"; {note}"
            # keep partial if screenshot still missing, else ok
            if row.get("status") == "error":
                row["status"] = "partial"
            changed = True
        rows.append(json.dumps(row, ensure_ascii=False))
    if changed:
        CAPTURES.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return changed


def ingest_host(hk: str, enrich: dict) -> dict:
    home = HOME_URLS.get(hk) or f"https://www.{hk}/"
    result = fetch_known_feed(home)
    items = result.get("items") or []
    stats = {
        "host": hk,
        "home": home,
        "items": len(items),
        "feed_source": result.get("feed_source"),
        "feed_error": result.get("feed_error"),
        "enrich_updated": False,
        "captures_updated": False,
    }
    if not items:
        print(f"FAIL {hk}: {result.get('feed_error')}")
        return stats

    entry = enrich.get(home) or {
        "http_last_modified": None,
        "html_modified": None,
        "meta_article_time": None,
        "cms": "custom",
        "cms_version": None,
        "cms_evidence": "rss_feed",
        "admin_urls": [],
        "admin_checks": [],
        "news_pages": [],
        "last_post_title": None,
        "last_post_date": None,
        "last_post_author": None,
        "fetch_status": None,
        "final_url": home,
        "error": None,
    }
    applied = apply_feed_to_entry(home, entry)
    enrich[home] = entry
    stats["enrich_updated"] = applied
    title = items[0]["title"]
    date_cz = items[0].get("date") or czech_date(items[0].get("pubDate") or "")
    note = f"feed_source={result.get('feed_source')}"
    stats["captures_updated"] = update_captures_from_feed(home, title, date_cz, note)
    print(
        f"OK {hk} items={len(items)} source={result.get('feed_source')} "
        f"last={title[:60]!r} date={date_cz} capt={stats['captures_updated']}"
    )
    if result.get("feed_error"):
        print(f"  note: {result['feed_error'][:200]}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="host key e.g. czdefence.cz")
    args = ap.parse_args()
    hosts = [args.only] if args.only else list(KNOWN_FEEDS.keys())
    enrich = load_enrichment()
    all_stats = []
    for hk in hosts:
        if hk not in KNOWN_FEEDS:
            print(f"unknown host {hk}; known={list(KNOWN_FEEDS)}")
            continue
        all_stats.append(ingest_host(hk, enrich))
    save_enrichment(enrich)
    print(f"wrote {ENRICH} entries={len(enrich)}")
    return 0 if any(s.get("items") for s in all_stats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
