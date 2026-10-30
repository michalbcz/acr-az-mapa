#!/usr/bin/env python3
"""Append-only archive of channel updates for feed + histogram.

Dedupe (since 2026-09-25): one entry per *post*, keyed by
channel (social: host + first path segment; web: host) + normalized text
(lowercased, whitespace-collapsed, trailing … stripped, first 120 chars).  Relative dates such as
"1h" / "9h" / "3 days ago" resolve to the run date, which previously produced a
new id every day for the same last_post.  Now:
  * a candidate matching an existing entry only bumps ``last_seen`` (and adds
    its url to ``urls``) instead of being appended;
  * date = real (absolute) published date if known, else the FIRST date seen;
  * two entries with the same text but absolute dates > 1 day apart are
    treated as distinct posts (recurring titles like "Krátké zprávy").
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # permalinks of concrete social posts (x.com/…/status/<id>, FB/IG post, YT watch)
    import social_permalinks as _sp
except Exception:  # pragma: no cover
    _sp = None


def _permalink(u: str) -> str:
    return u if (u and _sp is not None and _sp.is_post_permalink(u)) else ""
HISTORY = DATA / "updates_history.jsonl"


def parse_sort_date(s: str) -> str:
    if not s:
        return ""
    s = str(s).strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    m = re.search(r"(\d+)\s*h(?:ours?)?(?:\s*ago)?\b", s, re.I)
    if m and re.search(r"\d+\s*h", s, re.I):
        return (datetime.now() - timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*m(?:in(?:ute)?s?)?(?:\s*ago)?\b", s, re.I)
    if m and re.match(r"^\d+\s*m", s, re.I):
        return (datetime.now() - timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*days?\s*ago", s, re.I)
    if m:
        return (datetime.now() - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    months = {
        n: i
        for i, n in enumerate(
            [
                "january",
                "february",
                "march",
                "april",
                "may",
                "june",
                "july",
                "august",
                "september",
                "october",
                "november",
                "december",
            ],
            1,
        )
    }
    m = re.search(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:,?\s*(\d{4}))?",
        s,
        re.I,
    )
    if m:
        mon = months[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        try:
            return datetime(year, mon, day).strftime("%Y-%m-%d")
        except Exception:
            return ""
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).strftime(
                "%Y-%m-%d"
            )
        except Exception:
            continue
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except Exception:
        pass
    return ""


def iso_for_archive(raw: str, sort_key: str = "") -> str:
    raw = str(raw or "").strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt.isoformat()
    except Exception:
        pass
    m = re.search(
        r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?)(?:Z|[+-]\d{2}:?\d{2})?)?",
        raw,
    )
    if m:
        if m.group(2):
            return f"{m.group(1)}T{m.group(2)}"
        return m.group(1)
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", raw)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    sk = parse_sort_date(raw) or (sort_key or "")
    return sk


def categorize(url: str) -> str:
    u = (url or "").lower()
    host = urlparse(url or "").netloc.lower().replace("www.", "")
    path = urlparse(url or "").path.lower().strip("/")
    EXPERT_WEB_HOSTS = {
        "czdefence.cz", "armadninoviny.cz", "natoaktual.cz", "securityoutlines.cz",
        "valka.cz", "jirivojacek.cz", "lvisingr.czweb.org", "iir.cz", "aobp.cz",
        "magnetpress.online",
    }
    EXPERT_FB = {"91skupina", "czdefence", "armadninoviny", "natoaktual", "securityoutlines", "casopisatm"}
    EXPERT_IG = {"91skupina", "czdefence", "security_outlines", "ivo.zelinka"}
    EXPERT_X = {"ivozelinka", "carramba66", "czdefence", "natoaktual", "danny_elmayor"}
    EXPERT_LI = {"ivo-zelinka-5240611a"}
    if host in EXPERT_WEB_HOSTS or any(host.endswith("." + h) for h in EXPERT_WEB_HOSTS):
        return "odbornici"
    if "facebook.com" in host:
        if "/groups/" in u:
            return "fb_groups"
        slug = path.split("/")[0] if path else ""
        if slug in EXPERT_FB:
            return "odbornici"
        return "facebook"
    if "instagram.com" in host:
        slug = path.split("/")[0] if path else ""
        if slug in EXPERT_IG:
            return "odbornici"
        return "instagram"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if host in ("x.com", "twitter.com") or host.endswith(".x.com"):
        slug = path.split("/")[0] if path else ""
        if slug in EXPERT_X:
            return "odbornici"
        return "x"
    if "linkedin.com" in host:
        slug = path.rstrip("/").split("/")[-1] if path else ""
        if slug in EXPERT_LI:
            return "odbornici"
        return "linkedin"
    if (
        host in ("portal.ardos.org", "groups.io", "resco-cr.cz", "svz-cr.cz", "csol.cz")
        or "rkacr" in host
        or "radioklub" in host
        or "pokos" in host
    ):
        return "dobrovolnici"
    if host.startswith("kvv") or "kvvusti" in host or "kvvcb" in host:
        return "kvv"
    if "mpsfar.cz" in host and "kontakt" in u:
        return "alt"
    if "discord" in host or "disdex.io" in host:
        return "alt"
    if host in ("forum.vojsko.net", "rentaacr.cz", "zbranekvalitne.cz"):
        return "fora"
    unit_hosts = (
        "111prapor.cz",
        "petatricatnici.eu",
        "mpsfar.cz",
        "az44lmopr.cz",
        "43vysadkovypluk.mo.gov.cz",
        "14plogp.mo.gov.cz",
        "akis.mo.gov.cz",
        "7mb.mo.gov.cz",
    )
    if host in unit_hosts or any(host.endswith(h) for h in unit_hosts):
        return "jednotky"
    if host in (
        "aktivnizaloha.mo.gov.cz",
        "doarmady.mo.gov.cz",
        "veters.mo.gov.cz",
        "acr.mo.gov.cz",
        "mocr.mo.gov.cz",
        "mo.gov.cz",
    ):
        return "oficialni"
    if host.endswith(".mo.gov.cz"):
        return "jednotky"
    return "dobrovolnici"


def short_label(url: str) -> str:
    p = urlparse(url or "")
    path = (p.path or "/").rstrip("/") or "/"
    host = p.netloc.replace("www.", "")
    if "facebook.com" in host:
        if "/groups/" in path:
            gid = path.strip("/").split("/")[-1][:20]
            return "FB skupina " + gid
        if "profile.php" in path:
            return "FB " + (
                p.query.split("id=")[-1][:12] if "id=" in p.query else "profil"
            )
        return "FB /" + path.strip("/").split("/")[0][:40]
    if "instagram.com" in host:
        return "IG @" + path.strip("/").split("/")[0]
    if "youtube.com" in host:
        return "YT " + path.strip("/")
    if host in ("x.com", "twitter.com"):
        return "@" + path.strip("/").split("/")[0]
    if "linkedin.com" in host:
        segs = [s for s in path.split("/") if s]
        return "LI /" + (segs[-1][:40] if segs else host)
    if path in ("", "/"):
        return host
    segs = [s for s in path.split("/") if s]
    return f"{host}/…/{segs[-1][:36]}" if len(segs) > 1 else f"{host}/{segs[0][:40]}"


def make_id(url: str, date_iso: str, title: str) -> str:
    key = f"{url or ''}|{date_iso or ''}|{title or ''}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


RELATIVE_RE = re.compile(
    r"^\s*\d+\s*(?:s|m|min|mins|h|hr|hrs|d|w|hod|hodin|dní|dny|minut)\b"
    r"|\bago\b|\byesterday\b|\bvčera\b|\bpřed\s+\d+",
    re.I,
)


def is_relative_date(raw: str) -> bool:
    return bool(RELATIVE_RE.search(str(raw or "")))


def clean_title(t: str) -> str:
    """Decode HTML entities (&nbsp; from RSS/HTML scrapes) and NBSPs."""
    t = html.unescape(str(t or "")).replace("\xa0", " ").replace("\u202f", " ")
    return t.strip()


def norm_text(t: str) -> str:
    t = re.sub(r"\s+", " ", clean_title(t)).strip().lower()
    t = t.rstrip("…").rstrip(".").strip()
    # 120 chars: X/FB captures truncate the same post differently (…)
    return t[:120]


def channel_key(url: str) -> str:
    p = urlparse(url or "")
    host = p.netloc.lower().replace("www.", "").replace("m.facebook.com", "facebook.com")
    if host in ("twitter.com", "mobile.twitter.com"):
        host = "x.com"
    segs = [x for x in (p.path or "").split("/") if x]
    if any(h in host for h in ("facebook.com", "instagram.com", "x.com", "youtube.com", "linkedin.com", "tiktok.com")):
        if "profile.php" in (p.path or "") and "id=" in (p.query or ""):
            return host + "/profile/" + p.query.split("id=")[-1].split("&")[0]
        if segs and segs[0] in ("groups", "company", "in", "channel", "c", "school") and len(segs) > 1:
            return host + "/" + segs[0] + "/" + segs[1].lower()
        return host + "/" + (segs[0].lower() if segs else "")
    return host


def permalink_key(row: dict) -> str:
    """Concrete post permalink (x.com/…/status/<id>, FB/IG post, YT watch) or ''.

    The same post can arrive with different text (capture = first sentence,
    X syndication = full text truncated with …) – the permalink is the
    stronger identity, so rows sharing it are always the same post.
    """
    return _permalink(row.get("post_url") or "")


def _is_prefix_variant(a: str, b: str) -> bool:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return False
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(short) >= 20 and long_.startswith(short)


def dedupe_key(row: dict) -> tuple:
    return (channel_key(row.get("url") or ""), norm_text(row.get("title") or ""))


def _day(iso: str) -> str:
    return (iso or "")[:10]


def _dates_compatible(a: dict, b: dict) -> bool:
    """Same post unless both carry absolute dates more than 1 day apart."""
    if is_relative_date(a.get("date_raw")) or is_relative_date(b.get("date_raw")):
        return True
    da, db = _day(a.get("date_iso")), _day(b.get("date_iso"))
    if not da or not db:
        return True
    try:
        return abs((datetime.fromisoformat(da) - datetime.fromisoformat(db)).days) <= 1
    except Exception:
        return da == db


def _merge_into(base: dict, other: dict) -> None:
    """Merge ``other`` into ``base`` in place (same post)."""
    urls = list(base.get("urls") or [base.get("url") or ""])
    for u in (other.get("urls") or [other.get("url") or ""]):
        if u and u not in urls:
            urls.append(u)
    base["urls"] = [u for u in urls if u]
    seen = [x for x in (base.get("first_seen"), base.get("captured_at"),
                        other.get("first_seen"), other.get("captured_at")) if x]
    last = [x for x in (base.get("last_seen"), base.get("captured_at"),
                        other.get("last_seen"), other.get("captured_at")) if x]
    if seen:
        base["first_seen"] = min(seen)
        base["captured_at"] = base["first_seen"]
    if last:
        base["last_seen"] = max(last)
    # Date: prefer absolute (real) date; among equals prefer the earliest.
    b_rel = is_relative_date(base.get("date_raw"))
    o_rel = is_relative_date(other.get("date_raw"))
    take = False
    if b_rel and not o_rel and other.get("date_iso"):
        take = True
    elif b_rel == o_rel and other.get("date_iso") and (
        not base.get("date_iso") or other["date_iso"] < base["date_iso"]
    ):
        take = True
    if take:
        base["date_iso"] = other["date_iso"]
        base["date_raw"] = other.get("date_raw") or base.get("date_raw")
    # Prefer the more specific article URL as primary link (news_item)
    if other.get("kind") == "news_item" and base.get("kind") != "news_item" and other.get("url"):
        base["url"] = other["url"]
        base["kind"] = "news_item"
    bt, ot = clean_title(base.get("title")), clean_title(other.get("title"))
    if bt != (base.get("title") or ""):
        base["title"] = bt
    if ot and len(ot) > len(bt) and _is_prefix_variant(bt, ot):
        base["title"] = ot
    if not base.get("author") and other.get("author"):
        base["author"] = other["author"]
    # Social posts: keep the permalink of the concrete post (first known wins)
    if not _permalink(base.get("post_url")) and _permalink(other.get("post_url")):
        base["post_url"] = other["post_url"]


def dedupe_rows(rows: list) -> list:
    """Collapse duplicate posts. Earliest-seen entry keeps its id."""
    def order(o):
        return (o.get("first_seen") or o.get("captured_at") or "", o.get("date_iso") or "", o.get("id") or "")

    groups: dict = {}
    by_pl: dict = {}
    out: list = []
    for r in sorted(rows, key=order):
        r = dict(r)
        if r.get("title"):
            r["title"] = clean_title(r["title"])
        k = dedupe_key(r)
        pl = permalink_key(r)
        target = by_pl.get(pl) if pl else None
        if target is None:
            for cand in groups.get(k, []):
                if _dates_compatible(cand, r):
                    target = cand
                    break
        if target is None:
            r.setdefault("urls", [r.get("url")] if r.get("url") else [])
            r.setdefault("first_seen", r.get("captured_at") or "")
            r.setdefault("last_seen", r.get("captured_at") or "")
            groups.setdefault(k, []).append(r)
            out.append(r)
        else:
            _merge_into(target, r)
        if pl and pl not in by_pl:
            by_pl[pl] = target or r
    return out


def load_existing() -> dict:
    by_id = {}
    if not HISTORY.exists():
        return by_id
    for line in HISTORY.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        oid = obj.get("id")
        if oid:
            by_id[oid] = obj
    return by_id


def upsert(by_id: dict, new_rows: list, now_iso: str) -> tuple[int, int]:
    added = 0
    kept = 0
    index: dict = {}
    by_pl: dict = {}
    for e in by_id.values():
        index.setdefault(dedupe_key(e), []).append(e)
        if permalink_key(e):
            by_pl.setdefault(permalink_key(e), e)
    for row in new_rows:
        title = clean_title(row.get("title"))
        date_raw = row.get("date_raw") or ""
        date_iso = row.get("date_iso") or iso_for_archive(date_raw) or parse_sort_date(
            date_raw
        )
        if not title or not date_iso:
            continue
        url = row.get("url") or ""
        oid = make_id(url, date_iso, title)
        cand = {"url": url, "title": title, "date_raw": date_raw, "date_iso": date_iso,
                "kind": row.get("kind") or "last_post", "author": row.get("author") or "",
                "captured_at": row.get("captured_at") or now_iso,
                "post_url": _permalink(row.get("post_url"))}
        match = by_id.get(oid)
        if match is None and cand["post_url"]:
            match = by_pl.get(cand["post_url"])
        if match is None:
            for e in index.get(dedupe_key(cand), []):
                if _dates_compatible(e, cand):
                    match = e
                    break
        if match is not None:
            kept += 1
            _merge_into(match, cand)  # bumps last_seen; keeps first date
            continue
        entry = {
            "id": oid,
            "url": url,
            "source_label": row.get("source_label") or short_label(url),
            "category": row.get("category") or categorize(url),
            "title": title,
            "author": row.get("author") or "",
            "date_raw": date_raw,
            "date_iso": date_iso,
            "captured_at": row.get("captured_at") or now_iso,
            "kind": row.get("kind") or "last_post",
        }
        entry["urls"] = [url] if url else []
        if _permalink(row.get("post_url")):
            entry["post_url"] = row["post_url"]
        entry["first_seen"] = entry["captured_at"]
        entry["last_seen"] = now_iso
        by_id[oid] = entry
        index.setdefault(dedupe_key(entry), []).append(entry)
        if entry.get("post_url"):
            by_pl.setdefault(entry["post_url"], entry)
        added += 1
    return added, kept


def collect_from_captures(now_iso: str) -> list:
    path = DATA / "captures.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        title = r.get("last_post_title")
        date = r.get("last_post_date")
        if not title or not date:
            continue
        url = r.get("url") or ""
        date_iso = iso_for_archive(str(date)) or parse_sort_date(str(date))
        out.append(
            {
                "url": url,
                "source_label": short_label(url),
                "category": categorize(url),
                "title": str(title).strip(),
                "author": r.get("last_post_author") or "",
                "date_raw": str(date),
                "date_iso": date_iso,
                "captured_at": now_iso,
                "kind": "last_post",
                # permalink of the post itself (social captures must store last_post_url)
                "post_url": _permalink(r.get("last_post_url")),
            }
        )
    return out


def collect_from_enrichment(now_iso: str) -> list:
    path = DATA / "enrichment.json"
    if not path.exists():
        return []
    try:
        enrich = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for page_url, e in (enrich or {}).items():
        for np in e.get("news_pages") or []:
            # Full item lists from RSS (prefer these; avoid double-counting last_item)
            listed = np.get("items") or []
            if listed:
                for it in listed:
                    title = (it.get("title") or "").strip()
                    last_date = it.get("date") or ""
                    if not title or not last_date:
                        continue
                    item_url = it.get("url") or np.get("url") or page_url
                    date_iso = iso_for_archive(str(last_date)) or parse_sort_date(str(last_date))
                    out.append(
                        {
                            "url": item_url,
                            "source_label": short_label(page_url),
                            "category": categorize(page_url),
                            "title": title,
                            "author": "",
                            "date_raw": str(last_date),
                            "date_iso": date_iso,
                            "captured_at": now_iso,
                            "kind": "news_item",
                        }
                    )
                continue
            last = np.get("last_item")
            last_date = np.get("last_item_date")
            if not last or not last_date:
                continue
            item_url = np.get("last_item_url") or np.get("url") or page_url
            date_iso = iso_for_archive(str(last_date)) or parse_sort_date(str(last_date))
            out.append(
                {
                    "url": item_url,
                    "source_label": short_label(page_url),
                    "category": categorize(page_url),
                    "title": str(last).strip(),
                    "author": "",
                    "date_raw": str(last_date),
                    "date_iso": date_iso,
                    "captured_at": now_iso,
                    "kind": "news_item",
                }
            )
        # also enrichment-level last_post if present and not already from captures
        t = e.get("last_post_title")
        d = e.get("last_post_date")
        if t and d:
            date_iso = iso_for_archive(str(d)) or parse_sort_date(str(d))
            out.append(
                {
                    "url": page_url,
                    "source_label": short_label(page_url),
                    "category": categorize(page_url),
                    "title": str(t).strip(),
                    "author": e.get("last_post_author") or "",
                    "date_raw": str(d),
                    "date_iso": date_iso,
                    "captured_at": now_iso,
                    "kind": "last_post",
                }
            )
    return out


def collect_from_x_posts(now_iso: str) -> list:
    path = DATA / "x_posts.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for acc in data.get("accounts") or []:
        handle = acc.get("handle") or ""
        base = acc.get("url") or f"https://x.com/{handle}"
        for post in acc.get("posts") or []:
            text = (post.get("text") or "").strip()
            created = post.get("created_at") or ""
            if not text or not created:
                continue
            # truncate title for id stability
            title = text.replace("\n", " ").strip()
            if len(title) > 200:
                title = title[:197] + "…"
            date_iso = iso_for_archive(str(created)) or parse_sort_date(str(created))
            post_url = _permalink(post.get("url"))
            if not post_url and _sp is not None:
                post_url = _sp.x_status_url(handle, post.get("id") or post.get("id_str"))
            out.append(
                {
                    "url": base,
                    "post_url": post_url,
                    "source_label": f"@{handle}" if handle else short_label(base),
                    "category": "x",
                    "title": title,
                    "author": post.get("author") or (f"@{handle}" if handle else ""),
                    "date_raw": str(created),
                    "date_iso": date_iso,
                    "captured_at": data.get("captured_at") or now_iso,
                    "kind": "x_post",
                }
            )
    return out


def write_history(by_id: dict) -> int:
    # Stable order: by date_iso desc, then id
    def sort_key(o):
        di = o.get("date_iso") or ""
        return (di, o.get("id") or "")

    rows = sorted(by_id.values(), key=sort_key)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def run() -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    raw = load_existing()
    before = len(raw)
    by_id = {e["id"]: e for e in dedupe_rows(list(raw.values()))}
    collapsed = before - len(by_id)
    candidates = []
    candidates.extend(collect_from_captures(now_iso))
    candidates.extend(collect_from_enrichment(now_iso))
    candidates.extend(collect_from_x_posts(now_iso))
    added, kept = upsert(by_id, candidates, now_iso)
    filled = 0
    if _sp is not None:
        try:  # backfill post permalinks from cached data (never invented)
            _sp.refresh_x_cache()
            filled = _sp.fill_post_urls(list(by_id.values()))
        except Exception as ex:  # keep the archive run alive
            print(f"permalink backfill skipped: {ex}", file=sys.stderr)
    total = write_history(by_id)
    return {
        "path": str(HISTORY),
        "before": before,
        "collapsed_existing": collapsed,
        "added": added,
        "kept_existing": kept,
        "total": total,
        "candidates": len(candidates),
        "permalinks_filled": filled,
    }


def main():
    stats = run()
    print(
        f"archive ok total={stats['total']} added={stats['added']} "
        f"collapsed={stats['collapsed_existing']} "
        f"kept={stats['kept_existing']} candidates={stats['candidates']} "
        f"permalinks+={stats['permalinks_filled']} -> {stats['path']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
