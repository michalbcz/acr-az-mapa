#!/usr/bin/env python3
"""Enrich ACR website captures with CMS, last-modified, news pages."""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any
import sys
from pathlib import Path as _Path

_SCRIPTS = _Path(__file__).resolve().parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
try:
    from feed_utils import KNOWN_FEEDS, apply_feed_to_entry, host_key as feed_host_key, iso_date as feed_iso_date
except Exception:  # pragma: no cover
    KNOWN_FEEDS = {}
    def apply_feed_to_entry(page_url, entry):
        return False
    def feed_host_key(url):
        return urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    def feed_iso_date(raw):
        return ""

TIMEOUT = 20
UA = "ACR-Map-Enrichment/1.0 (+research; polite; contact: local)"
MAX_WORKERS = 3
NEWS_RE = re.compile(
    r"aktuality|novinky|news|aktualizace|tiskov[eé]|zpr[aá]vy|čl[aá]nky|clanky",
    re.I,
)
SKIP_HOSTS = ("facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "youtu.be")

# SSL context that still verifies when possible; fall back handled per-request
CTX = ssl.create_default_context()


def is_skip(url: str) -> bool:
    h = urllib.parse.urlparse(url).netloc.lower()
    return any(s in h for s in SKIP_HOSTS)


def abs_url(base: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    try:
        return urllib.parse.urljoin(base, href)
    except Exception:
        return None


def same_site(a: str, b: str) -> bool:
    ha = urllib.parse.urlparse(a).netloc.lower().lstrip("www.")
    hb = urllib.parse.urlparse(b).netloc.lower().lstrip("www.")
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


class PageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.metas: list[dict[str, str]] = []
        self.times: list[str] = []
        self.links: list[tuple[str, str]] = []  # (url, text)
        self.generator_comments: list[str] = []
        self.title = ""
        self._in_title = False
        self._capture_a = None
        self._a_text: list[str] = []
        self.scripts_src: list[str] = []
        self.html_snip_lower = ""

    def handle_starttag(self, tag, attrs):
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            self.metas.append(ad)
        elif tag == "time":
            dt = ad.get("datetime") or ad.get("content") or ""
            if dt:
                self.times.append(dt.strip())
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = ad.get("href", "")
            u = abs_url(self.base, href)
            if u:
                self._capture_a = u
                self._a_text = []
        elif tag == "script":
            src = ad.get("src", "")
            if src:
                self.scripts_src.append(src)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._capture_a:
            text = " ".join(self._a_text).strip()
            self.links.append((self._capture_a, text))
            self._capture_a = None
            self._a_text = []

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._capture_a is not None:
            self._a_text.append(data)

    def handle_comment(self, data):
        d = data.strip()
        if re.search(r"wordpress|drupal|joomla|typo3|sharepoint|generator|cms", d, re.I):
            self.generator_comments.append(d[:200])


def http_request(
    url: str,
    method: str = "GET",
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "cs,en;q=0.8",
        },
    )
    out: dict[str, Any] = {
        "url": url,
        "final_url": url,
        "status": None,
        "headers": {},
        "body": "",
        "error": None,
    }
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as resp:
            out["status"] = resp.status
            out["final_url"] = resp.geturl()
            # normalize headers to dict of str
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            out["headers"] = hdrs
            if method == "GET":
                raw = resp.read(max_bytes)
                # try decode
                charset = "utf-8"
                ctype = hdrs.get("content-type", "")
                m = re.search(r"charset=([\w-]+)", ctype, re.I)
                if m:
                    charset = m.group(1)
                try:
                    out["body"] = raw.decode(charset, errors="replace")
                except Exception:
                    out["body"] = raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["headers"] = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        try:
            raw = e.read(max_bytes) if method == "GET" else b""
            out["body"] = raw.decode("utf-8", errors="replace") if raw else ""
        except Exception:
            pass
        out["error"] = f"HTTPError {e.code}"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def meta_value(metas: list[dict], *, prop: str | None = None, name: str | None = None) -> str | None:
    for m in metas:
        if prop and m.get("property", "").lower() == prop.lower():
            return m.get("content") or None
        if name and m.get("name", "").lower() == name.lower():
            return m.get("content") or None
    return None


def detect_cms(body: str, metas: list[dict], headers: dict, comments: list[str], scripts: list[str]) -> tuple[str, str | None, str]:
    bl = body.lower()
    evidence: list[str] = []
    cms = "unknown"
    version: str | None = None

    gen = meta_value(metas, name="generator") or ""
    xgen = headers.get("x-generator") or headers.get("x-powered-by") or ""

    # WordPress
    if "wp-content" in bl or "wp-includes" in bl or re.search(r"wordpress", gen, re.I):
        cms = "WordPress"
        evidence.append("wp-content/wp-includes or generator")
        m = re.search(r"WordPress\s+([\d.]+)", gen, re.I)
        if m:
            version = m.group(1)
        else:
            m2 = re.search(r"wordpress\s+([\d.]+)", bl[:5000], re.I)
            if m2:
                version = m2.group(1)
    # Drupal
    elif (
        "drupal.settings" in bl
        or "sites/default/files" in bl
        or "/misc/drupal.js" in bl
        or re.search(r"drupal", gen, re.I)
        or "x-drupal" in " ".join(headers.keys())
        or any("drupal" in s.lower() for s in scripts)
    ):
        cms = "Drupal"
        evidence.append("Drupal markers")
        m = re.search(r"Drupal\s+([\d.]+)", gen, re.I)
        if m:
            version = m.group(1)
        # Drupal 7/8/9/10 from paths
        if "sites/default/files" in bl and not version:
            evidence.append("sites/default/files")
        if "Drupal.settings" in body:
            evidence.append("Drupal.settings")
    # Joomla
    elif "/media/jui/" in bl or "option=com_" in bl or re.search(r"joomla", gen, re.I) or "joomla!" in bl:
        cms = "Joomla"
        evidence.append("Joomla markers")
        m = re.search(r"Joomla!?\s*-?\s*([\d.]+)", gen, re.I)
        if m:
            version = m.group(1)
    # Typo3
    elif "typo3" in bl or re.search(r"typo3", gen, re.I) or "/typo3conf/" in bl or "/typo3temp/" in bl:
        cms = "Typo3"
        evidence.append("Typo3 markers")
        m = re.search(r"TYPO3\s+CMS\s+([\d.]+)", gen, re.I) or re.search(r"TYPO3\s+([\d.]+)", gen, re.I)
        if m:
            version = m.group(1)
    # SharePoint
    elif (
        "_layouts/" in bl
        or "sharepoint" in bl
        or "microsoftsharepointteamservices" in " ".join(headers.keys()).lower()
        or headers.get("microsoftsharepointteamservices")
        or "sppagecontextinfo" in bl
    ):
        cms = "SharePoint"
        evidence.append("SharePoint markers")
        version = headers.get("microsoftsharepointteamservices") or version
    # Liferay / gov custom
    elif "liferay" in bl or re.search(r"liferay", gen, re.I):
        cms = "Liferay"
        evidence.append("Liferay")
        m = re.search(r"Liferay\s+([\d.]+)", gen, re.I)
        if m:
            version = m.group(1)
    elif gen:
        # custom named generator
        cms = "custom"
        evidence.append(f"generator={gen[:80]}")
        version = None
    elif comments:
        cms = "custom"
        evidence.append(f"comment={comments[0][:80]}")

    # header powered-by hints
    if cms == "unknown" and xgen:
        if "php" in xgen.lower():
            evidence.append(f"x-powered-by={xgen[:60]}")
        if re.search(r"wordpress|drupal|joomla|typo3", xgen, re.I):
            pass  # already handled usually

    # Czech army Drupal sites often use mo.gov.cz Drupal
    if cms == "unknown":
        if "mo.gov.cz" in bl and ("sites/default" in bl or "drupal" in bl):
            cms = "Drupal"
            evidence.append("mo.gov.cz Drupal-like")
        elif re.search(r"/themes/|/modules/", bl) and "sites/" in bl:
            cms = "Drupal"
            evidence.append("themes/modules/sites paths")

    if gen and cms != "unknown" and not any("generator" in e for e in evidence):
        evidence.append(f"generator={gen[:80]}")

    return cms, version, "; ".join(evidence)[:200] if evidence else "no clear markers"


def admin_candidates(base: str, cms: str) -> list[str]:
    # base should be origin + maybe path root
    p = urllib.parse.urlparse(base)
    origin = f"{p.scheme}://{p.netloc}"
    paths: list[str] = []
    if cms == "WordPress":
        paths = ["/wp-admin/", "/wp-login.php"]
    elif cms == "Drupal":
        paths = ["/user/login", "/admin"]
    elif cms == "Joomla":
        paths = ["/administrator/"]
    elif cms == "Typo3":
        paths = ["/typo3/"]
    elif cms == "SharePoint":
        paths = ["/_layouts/15/start.aspx", "/_layouts/15/Authenticate.aspx"]
    else:
        paths = []
    # always add generic
    for g in ["/admin", "/login", "/cms"]:
        if g not in paths:
            paths.append(g)
    return [origin + path for path in paths]


def score_news_link(url: str, text: str, page_url: str) -> int:
    blob = f"{url} {text}".lower()
    if not NEWS_RE.search(blob):
        return 0
    if not same_site(url, page_url):
        return 0
    # skip social/media files
    if any(x in url.lower() for x in (".pdf", ".jpg", ".png", "facebook", "instagram", "youtube", "twitter", "x.com")):
        return 0
    score = 1
    # prefer path keywords
    path = urllib.parse.urlparse(url).path.lower()
    for kw, pts in [
        ("aktuality", 10),
        ("novinky", 9),
        ("zpravy", 8),
        ("zprávy", 8),
        ("tiskove", 7),
        ("tiskové", 7),
        ("news", 6),
        ("clanky", 5),
        ("články", 5),
        ("aktualizace", 4),
    ]:
        if kw in path or kw in text.lower():
            score += pts
    # prefer shorter paths (listing pages)
    depth = path.strip("/").count("/")
    score += max(0, 3 - depth)
    return score


def extract_news_items(body: str, page_url: str) -> list[dict[str, str]]:
    """Heuristic: find article-like dated items."""
    items: list[dict[str, str]] = []
    # time + nearby link patterns
    # Pattern: datetime attributes near anchors
    for m in re.finditer(
        r'<time[^>]*datetime=["\']([^"\']+)["\'][^>]*>.*?</time>',
        body,
        re.I | re.S,
    ):
        dt = m.group(1).strip()
        # look around for a link title in a window
        start = max(0, m.start() - 400)
        end = min(len(body), m.end() + 400)
        window = body[start:end]
        am = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', window, re.I | re.S)
        title = ""
        href = ""
        if am:
            href = abs_url(page_url, am.group(1)) or ""
            title = re.sub(r"<[^>]+>", "", am.group(2)).strip()
            title = re.sub(r"\s+", " ", title)[:200]
        if title or dt:
            items.append({"title": title, "date": dt, "url": href})

    # Czech date patterns near links: d.m.yyyy or d. m. yyyy
    date_pat = re.compile(
        r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4}|\d{4}-\d{2}-\d{2}[T ][\d:+-]+|\d{4}-\d{2}-\d{2})"
    )
    # article / views / node list patterns common on Drupal
    for m in re.finditer(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        body,
        re.I | re.S,
    ):
        href = abs_url(page_url, m.group(1)) or ""
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        title = re.sub(r"\s+", " ", title)
        if not title or len(title) < 8 or len(title) > 180:
            continue
        if not same_site(href, page_url):
            continue
        # look for date in following 300 chars
        after = body[m.end() : m.end() + 350]
        before = body[max(0, m.start() - 200) : m.start()]
        dm = date_pat.search(after) or date_pat.search(before)
        if not dm:
            continue
        # filter nav-ish
        if NEWS_RE.search(title) and len(title) < 25:
            continue
        path = urllib.parse.urlparse(href).path.lower()
        if path in ("/", "") or path.count("/") < 1:
            continue
        items.append({"title": title, "date": dm.group(1).strip(), "url": href})

    # dedupe by title
    seen = set()
    uniq = []
    for it in items:
        key = it["title"].lower()
        if key in seen or not it["title"]:
            continue
        seen.add(key)
        uniq.append(it)

    def date_key(d: str) -> str:
        # normalize for sort: try ISO first
        d = d.strip()
        m = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", d)
        if m:
            return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        m = re.match(r"(\d{4}-\d{2}-\d{2})", d)
        if m:
            return m.group(1)
        return d

    uniq.sort(key=lambda x: date_key(x.get("date", "")), reverse=True)
    return uniq[:10]


def pick_html_modified(metas: list[dict], times: list[str]) -> tuple[str | None, str | None]:
    """Return (html_modified, meta_article_time)."""
    candidates = []
    for key in (
        ("property", "article:modified_time"),
        ("property", "og:updated_time"),
        ("name", "last-modified"),
        ("name", "DC.date.modified"),
        ("name", "dc.date.modified"),
    ):
        kind, val = key
        v = meta_value(metas, prop=val) if kind == "property" else meta_value(metas, name=val)
        if v:
            candidates.append(v)
    article = (
        meta_value(metas, prop="article:modified_time")
        or meta_value(metas, prop="article:published_time")
        or meta_value(metas, prop="og:updated_time")
        or meta_value(metas, name="date")
        or meta_value(metas, name="DC.date")
        or meta_value(metas, name="dc.date")
    )
    html_mod = candidates[0] if candidates else (times[0] if times else None)
    if not html_mod and article:
        html_mod = article
    return html_mod, article


def enrich_one(url: str) -> dict[str, Any]:
    time.sleep(0.4)  # politeness per worker
    entry: dict[str, Any] = {
        "http_last_modified": None,
        "html_modified": None,
        "meta_article_time": None,
        "cms": "unknown",
        "cms_version": None,
        "cms_evidence": "",
        "admin_urls": [],
        "admin_checks": [],
        "news_pages": [],
        "last_post_title": None,
        "last_post_date": None,
        "last_post_author": None,
        "fetch_status": None,
        "final_url": None,
        "error": None,
    }

    # Prefer GET (also gets headers); HEAD often blocked
    resp = http_request(url, "GET")
    entry["fetch_status"] = resp["status"]
    entry["final_url"] = resp["final_url"]
    entry["error"] = resp["error"]
    hdrs = resp["headers"]
    entry["http_last_modified"] = hdrs.get("last-modified")
    # also keep etag/date in evidence fields optionally — store as notes on entry
    if hdrs.get("etag"):
        entry["etag"] = hdrs["etag"]
    if hdrs.get("date"):
        entry["http_date"] = hdrs["date"]

    body = resp["body"] or ""
    if not body and resp["status"] not in (200, 301, 302, 401, 403):
        # try origin homepage if path failed
        p = urllib.parse.urlparse(url)
        home = f"{p.scheme}://{p.netloc}/"
        if home.rstrip("/") != url.rstrip("/"):
            resp2 = http_request(home, "GET")
            if resp2["body"]:
                body = resp2["body"]
                hdrs = resp2["headers"] or hdrs
                if not entry["http_last_modified"]:
                    entry["http_last_modified"] = hdrs.get("last-modified")
                entry["final_url"] = resp2["final_url"]
                entry["fetch_status"] = resp2["status"]
                entry["error"] = (entry["error"] or "") + f"; fallback_home={resp2['status']}"

    if not body:
        entry["cms_evidence"] = "no body fetched"
        entry["admin_urls"] = admin_candidates(url, "unknown")
        # RSS / feed_cache fallback (e.g. czdefence TLS closed from box)
        if feed_host_key(url) in KNOWN_FEEDS:
            if apply_feed_to_entry(url, entry):
                entry["cms_evidence"] = (entry.get("cms_evidence") or "") + "; rss_or_feed_cache"
                return entry
        # still check a couple admins
        for aurl in entry["admin_urls"][:4]:
            time.sleep(0.2)
            r = http_request(aurl, "HEAD")
            if r["status"] is None:
                r = http_request(aurl, "GET")
            entry["admin_checks"].append({"url": aurl, "status": r["status"]})
        return entry

    parser = PageParser(resp["final_url"] or url)
    try:
        parser.feed(body)
    except Exception as e:
        entry["error"] = (entry["error"] or "") + f"; parse:{e}"

    html_mod, article = pick_html_modified(parser.metas, parser.times)
    entry["html_modified"] = html_mod
    entry["meta_article_time"] = article

    cms, ver, evid = detect_cms(body, parser.metas, hdrs, parser.generator_comments, parser.scripts_src)
    entry["cms"] = cms
    entry["cms_version"] = ver
    entry["cms_evidence"] = evid

    base_for_admin = resp["final_url"] or url
    entry["admin_urls"] = admin_candidates(base_for_admin, cms)

    # admin checks (limited)
    for aurl in entry["admin_urls"][:5]:
        time.sleep(0.25)
        r = http_request(aurl, "HEAD")
        st = r["status"]
        if st is None:
            r = http_request(aurl, "GET")
            st = r["status"]
        # some servers disallow HEAD -> 405; try GET
        if st == 405:
            r = http_request(aurl, "GET")
            st = r["status"]
        entry["admin_checks"].append({"url": aurl, "status": st})

    # news page discovery
    scored: list[tuple[int, str, str]] = []
    seen_u = set()
    for href, text in parser.links:
        sc = score_news_link(href, text, url)
        if sc <= 0:
            continue
        # normalize
        nu = href.split("#")[0]
        if nu in seen_u:
            continue
        seen_u.add(nu)
        # don't pick the page itself
        if nu.rstrip("/") == (resp["final_url"] or url).rstrip("/"):
            continue
        scored.append((sc, nu, text[:80] or "news"))
    scored.sort(key=lambda x: -x[0])
    candidates = scored[:2]

    # also try common paths if none found
    if not candidates:
        p = urllib.parse.urlparse(base_for_admin)
        origin = f"{p.scheme}://{p.netloc}"
        for path, title in [
            ("/aktuality", "Aktuality"),
            ("/novinky", "Novinky"),
            ("/zpravy", "Zprávy"),
            ("/news", "News"),
            ("/tiskove-zpravy", "Tiskové zprávy"),
            ("/o-nas/aktuality", "Aktuality"),
        ]:
            candidates.append((1, origin + path, title))
        candidates = candidates[:2]

    news_pages = []
    best_item = None
    for sc, nurl, ntitle in candidates[:2]:
        time.sleep(0.35)
        nr = http_request(nurl, "GET")
        if nr["status"] not in (200, None) and (nr["status"] or 0) >= 400:
            # skip hard failures unless 200
            if nr["status"] in (404, 410):
                continue
        nbody = nr["body"] or ""
        if not nbody:
            continue
        items = extract_news_items(nbody, nr["final_url"] or nurl)
        last = items[0] if items else None
        page_info = {
            "url": nr["final_url"] or nurl,
            "title": ntitle or "Aktuality",
            "last_item": last["title"] if last else None,
            "last_item_date": last["date"] if last else None,
        }
        if last and last.get("url"):
            page_info["last_item_url"] = last["url"]
        news_pages.append(page_info)
        if last and (best_item is None):
            best_item = last

    # Also extract from main page if no news page items
    if best_item is None:
        main_items = extract_news_items(body, resp["final_url"] or url)
        if main_items:
            best_item = main_items[0]

    entry["news_pages"] = news_pages
    if best_item:
        entry["last_post_title"] = best_item.get("title")
        entry["last_post_date"] = best_item.get("date")
    # Prefer structured RSS when available (overrides HTML heuristics)
    if feed_host_key(url) in KNOWN_FEEDS:
        apply_feed_to_entry(url, entry)

    return entry


def collect_urls() -> list[str]:
    with open(os.path.dirname(os.path.abspath(__file__)) + "/urls.json") as f:
        urls_doc = json.load(f)
    with open(os.path.dirname(os.path.abspath(__file__)) + "/data/captures.jsonl") as f:
        captures = [json.loads(line) for line in f if line.strip()]

    unit_hosts = set()
    for u in urls_doc.get("unit_webs", []):
        unit_hosts.add(urllib.parse.urlparse(u).netloc.lower().lstrip("www."))

    out = []
    seen = set()

    def add(u: str):
        if not u or is_skip(u):
            return
        if not u.startswith("http"):
            return
        key = u.rstrip("/")
        if key in seen:
            return
        seen.add(key)
        out.append(u)

    for k in ("official_webs", "kvv", "unit_webs", "experts", "other"):
        for u in urls_doc.get(k, []):
            add(u)

    # expert / other webs from captures (non-social already filtered by is_skip)
    for c in captures:
        u = c.get("url", "")
        if not u or is_skip(u):
            continue
        h = urllib.parse.urlparse(u).netloc.lower().lstrip("www.")
        if h in KNOWN_FEEDS or any(h.endswith(k) for k in KNOWN_FEEDS):
            add(u)

    for c in captures:
        u = c.get("url", "")
        if is_skip(u):
            continue
        h = urllib.parse.urlparse(u).netloc.lower()
        hl = h.lstrip("www.")
        if "mo.gov.cz" in h or hl in unit_hosts or any(hl.endswith(uh) for uh in unit_hosts):
            add(u)
        # also ardos / groups from official already covered

    return out


def update_captures(enrichment: dict[str, dict]):
    path = os.path.dirname(os.path.abspath(__file__)) + "/data/captures.jsonl"
    with open(path) as f:
        rows = [json.loads(line) for line in f if line.strip()]

    by_url = {r["url"]: r for r in rows}
    updated = 0
    for url, en in enrichment.items():
        title = en.get("last_post_title")
        date = en.get("last_post_date")
        if not title and not date:
            continue
        # find matching capture row (exact or same path key)
        row = by_url.get(url)
        if not row:
            # try match without trailing slash differences
            for k, r in by_url.items():
                if k.rstrip("/") == url.rstrip("/"):
                    row = r
                    break
        if not row:
            continue
        better = False
        old_iso = feed_iso_date(str(row.get("last_post_date") or ""))
        new_iso = feed_iso_date(str(date or ""))
        newer = bool(new_iso and (not old_iso or new_iso > old_iso))
        if title and (not row.get("last_post_title") or row.get("last_post_title") in (None, "")):
            row["last_post_title"] = title
            better = True
        elif title and date and (not row.get("last_post_date")):
            row["last_post_title"] = title
            better = True
        elif title and newer:
            row["last_post_title"] = title
            better = True
        if date and (not row.get("last_post_date") or row.get("last_post_date") in (None, "") or newer):
            row["last_post_date"] = date
            better = True
        if better:
            note = row.get("notes") or ""
            extra = "enriched_from_news_subpage"
            if newer:
                extra = "enriched_newer_from_feed_or_news"
            if extra not in note:
                row["notes"] = (note + "; " if note else "") + extra
            updated += 1

    # write back preserving order
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return updated


def main():
    urls = collect_urls()
    print(f"Processing {len(urls)} URLs with max_workers={MAX_WORKERS}")
    out_path = os.path.dirname(os.path.abspath(__file__)) + "/data/enrichment.json"
    try:
        with open(out_path) as f:
            enrichment: dict[str, dict] = json.load(f) or {}
    except Exception:
        enrichment = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(enrich_one, u): u for u in urls}
        for fut in concurrent.futures.as_completed(futs):
            u = futs[fut]
            try:
                enrichment[u] = fut.result()
                e = enrichment[u]
                print(
                    f"OK {u} cms={e['cms']} news={len(e['news_pages'])} "
                    f"lm={e.get('http_last_modified')} status={e.get('fetch_status')}"
                )
            except Exception as exn:
                enrichment[u] = {
                    "http_last_modified": None,
                    "html_modified": None,
                    "meta_article_time": None,
                    "cms": "unknown",
                    "cms_version": None,
                    "cms_evidence": f"exception: {exn}",
                    "admin_urls": admin_candidates(u, "unknown"),
                    "admin_checks": [],
                    "news_pages": [],
                    "last_post_title": None,
                    "last_post_date": None,
                    "last_post_author": None,
                    "error": str(exn),
                }
                print(f"ERR {u}: {exn}")

    with open(out_path, "w") as f:
        json.dump(enrichment, f, ensure_ascii=False, indent=2)

    updated = update_captures(enrichment)

    # summary
    from collections import Counter

    cms_c = Counter(e.get("cms") for e in enrichment.values())
    news_n = sum(1 for e in enrichment.values() if e.get("news_pages"))
    news_items = sum(
        1
        for e in enrichment.values()
        for np in e.get("news_pages") or []
        if np.get("last_item")
    )
    print("---SUMMARY---")
    print(f"entries={len(enrichment)}")
    print(f"cms_counts={dict(cms_c)}")
    print(f"sites_with_news_pages={news_n}")
    print(f"news_pages_with_items={news_items}")
    print(f"captures_updated={updated}")
    print(f"wrote={out_path}")


if __name__ == "__main__":
    main()
