#!/usr/bin/env python3
"""Refine enrichment: better CMS labels, Zpravodajství news, last_post pick."""
from __future__ import annotations

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

TIMEOUT = 20
UA = "ACR-Map-Enrichment/1.0 (+research; polite)"
CTX = ssl.create_default_context()
NEWS_RE = re.compile(
    r"aktuality|novinky|news|aktualizace|tiskov[eé]|zpr[aá]vy|čl[aá]nky|clanky|zpravodajstv[ií]",
    re.I,
)


def abs_url(base, href):
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    return urllib.parse.urljoin(base, href.strip())


def same_site(a, b):
    ha = urllib.parse.urlparse(a).netloc.lower().lstrip("www.")
    hb = urllib.parse.urlparse(b).netloc.lower().lstrip("www.")
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


class PageParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.metas = []
        self.times = []
        self.links = []
        self._a = None
        self._t = []
        self.comments = []

    def handle_starttag(self, tag, attrs):
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            self.metas.append(ad)
        elif tag == "time" and ad.get("datetime"):
            self.times.append(ad["datetime"].strip())
        elif tag == "a":
            u = abs_url(self.base, ad.get("href", ""))
            if u:
                self._a = u
                self._t = []

    def handle_endtag(self, tag):
        if tag == "a" and self._a:
            self.links.append((self._a, " ".join(self._t).strip()))
            self._a = None

    def handle_data(self, data):
        if self._a is not None:
            self._t.append(data)

    def handle_comment(self, data):
        if re.search(r"wordpress|drupal|joomla|typo3|webtodate|generator", data, re.I):
            self.comments.append(data.strip()[:200])


def http_get(url, method="GET"):
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "cs,en;q=0.8"},
    )
    out = {"status": None, "final_url": url, "headers": {}, "body": "", "error": None}
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as resp:
            out["status"] = resp.status
            out["final_url"] = resp.geturl()
            out["headers"] = {k.lower(): v for k, v in resp.headers.items()}
            if method == "GET":
                raw = resp.read(2_000_000)
                # charset
                ctype = out["headers"].get("content-type", "")
                m = re.search(r"charset=([\w-]+)", ctype, re.I)
                enc = m.group(1) if m else None
                for e in ([enc] if enc else []) + ["utf-8", "windows-1250", "iso-8859-2", "latin-1"]:
                    if not e:
                        continue
                    try:
                        out["body"] = raw.decode(e)
                        break
                    except Exception:
                        continue
                else:
                    out["body"] = raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["headers"] = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        try:
            out["body"] = e.read(500000).decode("utf-8", errors="replace")
        except Exception:
            pass
        out["error"] = f"HTTPError {e.code}"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def detect_cms(body, metas, headers, comments):
    bl = body.lower()
    gen = ""
    author = ""
    for m in metas:
        if m.get("name", "").lower() == "generator":
            gen = m.get("content") or ""
        if m.get("name", "").lower() == "author":
            author = m.get("content") or ""

    # WebToDate (MO main sites)
    if "webtodate" in author.lower() or "webtodate" in bl[:5000].lower() or "macron software" in author.lower():
        return "WebToDate", None, f"author={author[:100]}"
    if "wp-content" in bl or "wp-includes" in bl or re.search(r"wordpress", gen, re.I):
        ver = None
        m = re.search(r"WordPress\s+([\d.]+)", gen, re.I)
        if m:
            ver = m.group(1)
        return "WordPress", ver, "wp-content/wp-includes or generator"
    if (
        "drupal.settings" in body
        or "data-drupal" in bl
        or "sites/default/files" in bl
        or re.search(r"drupal", gen, re.I)
        or "/core/misc/" in bl
    ):
        ver = None
        m = re.search(r"Drupal\s+([\d.]+)", gen, re.I)
        if m:
            ver = m.group(1)
        return "Drupal", ver, "Drupal markers"
    if "/media/jui/" in bl or "option=com_" in bl or re.search(r"joomla", gen, re.I):
        return "Joomla", None, "Joomla markers"
    if "typo3conf" in bl or re.search(r"typo3", gen, re.I):
        return "Typo3", None, "Typo3 markers"
    if "_layouts/" in bl or "sharepoint" in bl or headers.get("microsoftsharepointteamservices"):
        return "SharePoint", headers.get("microsoftsharepointteamservices"), "SharePoint markers"
    if "phpbb" in bl or any("phpbb" in (headers.get(k) or "").lower() for k in headers):
        return "phpBB", None, "phpbb cookie/markers"
    if "__next" in bl or "x-nextjs-cache" in headers or "x-powered-by" in headers and "next" in (headers.get("x-powered-by") or "").lower():
        return "Next.js", None, "Next.js markers"
    if headers.get("x-nextjs-cache") or headers.get("x-nextjs-prerender"):
        return "Next.js", None, "x-nextjs-* headers"
    if "nextcloud" in bl or "/nextcloud" in bl:
        return "Nextcloud", None, "nextcloud redirect/app"
    if "groups.io" in bl and "groups.io" in (headers.get("server") or "" or True):
        if "groups.io" in urllib.parse.urlparse("https://x").netloc or True:
            pass
    if re.search(r"groups\.io", bl[:3000]) and "ardos@groups.io" in body:
        return "Groups.io", None, "groups.io platform"
    if "webnode" in gen.lower():
        return "Webnode", None, f"generator={gen}"
    if gen:
        return "custom", None, f"generator={gen[:80]}"
    if author and "barabas" in author.lower():
        return "custom", None, f"author={author[:80]}"
    if comments:
        return "custom", None, f"comment={comments[0][:80]}"
    # Drupal-ish paths without explicit
    if "sites/" in bl and ("/themes/" in bl or "/modules/" in bl):
        return "Drupal", None, "themes/modules/sites paths"
    return "unknown", None, "no clear markers"


def score_news(url, text, page_url):
    blob = f"{url} {text}"
    if not NEWS_RE.search(blob):
        return 0
    if not same_site(url, page_url):
        return 0
    if any(x in url.lower() for x in (".pdf", ".jpg", ".png", "facebook", "instagram", "youtube")):
        return 0
    score = 1
    path = urllib.parse.urlparse(url).path.lower()
    for kw, pts in [
        ("zpravodajstvi", 12),
        ("zpravodajství", 12),
        ("aktuality", 10),
        ("novinky", 9),
        ("zpravy", 8),
        ("tiskove", 7),
        ("news", 6),
        ("clanky", 5),
    ]:
        if kw in path or kw in text.lower():
            score += pts
    # prefer listing over article detail (articles often have long numeric ids)
    if re.search(r"\d{5,}", path):
        score -= 5
    if "detail.php" in path:
        score += 8  # MO listing via detail.php?pgid=
    depth = path.strip("/").count("/")
    score += max(0, 3 - depth)
    return score


def extract_items(body, page_url):
    items = []
    date_pat = re.compile(
        r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4}|\d{4}-\d{2}-\d{2}(?:[T ][\d:+-]+)?)"
    )
    # MO WebToDate: often date in nearby text; titles in img alt or link path slug
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', body, re.I | re.S):
        href = abs_url(page_url, m.group(1)) or ""
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        title = re.sub(r"\s+", " ", title)
        # derive title from slug if empty / "Více"
        if (not title or title.lower() in ("více >>", "vice >>", "více", "more")) and href:
            slug = urllib.parse.urlparse(href).path.rstrip("/").split("/")[-1]
            # strip trailing --id
            slug = re.sub(r"--?\d+$", "", slug)
            title = slug.replace("-", " ").replace(":", " – ").strip()
            if len(title) < 12:
                continue
            title = title[:1].upper() + title[1:]
        if not title or len(title) < 8 or len(title) > 200:
            continue
        if not same_site(href, page_url):
            continue
        if title.lower().endswith(".pdf"):
            continue
        after = body[m.end() : m.end() + 400]
        before = body[max(0, m.start() - 250) : m.start()]
        dm = date_pat.search(after) or date_pat.search(before)
        path = urllib.parse.urlparse(href).path.lower()
        # accept zpravodajstvi article paths even without nearby date
        is_article = bool(re.search(r"zpravodajstvi/.+\d{4,}", path)) or bool(re.search(r"/node/\d+", path)) or bool(re.search(r"/\d{4}/\d{2}/", path))
        if not dm and not is_article:
            continue
        if NEWS_RE.search(title) and len(title) < 30:
            continue
        items.append({"title": title[:200], "date": dm.group(1).strip() if dm else None, "url": href})

    for m in re.finditer(r'<time[^>]*datetime=["\']([^"\']+)["\'][^>]*>', body, re.I):
        dt = m.group(1)
        window = body[max(0, m.start() - 400) : m.end() + 400]
        am = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', window, re.I | re.S)
        if am:
            href = abs_url(page_url, am.group(1)) or ""
            title = re.sub(r"<[^>]+>", " ", am.group(2))
            title = re.sub(r"\s+", " ", title).strip()[:200]
            if title and not title.lower().endswith(".pdf"):
                items.append({"title": title, "date": dt, "url": href})

    seen = set()
    uniq = []
    for it in items:
        key = it["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    def date_key(d):
        if not d:
            return ""
        m = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", d)
        if m:
            return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        m = re.match(r"(\d{4}-\d{2}-\d{2})", d)
        return m.group(1) if m else d

    # prefer dated items first, then by date desc
    uniq.sort(key=lambda x: (1 if x.get("date") else 0, date_key(x.get("date") or "")), reverse=True)
    return uniq[:15]


def best_last_post(news_pages, fallback=None):
    candidates = []
    for np in news_pages:
        if np.get("last_item") and not str(np["last_item"]).lower().endswith(".pdf"):
            candidates.append((np.get("last_item"), np.get("last_item_date")))
    if candidates:
        return candidates[0]
    if fallback:
        return fallback
    return None, None


def admin_candidates(base, cms):
    p = urllib.parse.urlparse(base)
    origin = f"{p.scheme}://{p.netloc}"
    paths = []
    if cms == "WordPress":
        paths = ["/wp-admin/", "/wp-login.php"]
    elif cms == "Drupal":
        paths = ["/user/login", "/admin"]
    elif cms == "Joomla":
        paths = ["/administrator/"]
    elif cms == "Typo3":
        paths = ["/typo3/"]
    elif cms == "SharePoint":
        paths = ["/_layouts/15/start.aspx"]
    elif cms == "Nextcloud":
        paths = ["/nextcloud", "/nextcloud/login"]
    elif cms == "phpBB":
        paths = ["/forum/adm/", "/adm/"]
    elif cms == "WebToDate":
        paths = ["/admin", "/login", "/scripts/login.php"]
    for g in ["/admin", "/login", "/cms"]:
        if g not in paths:
            paths.append(g)
    return [origin + x for x in paths]


def refine_url(url, existing):
    print(f"REFINE {url}")
    time.sleep(0.35)
    resp = http_get(url)
    body = resp["body"] or ""
    hdrs = resp["headers"]
    entry = dict(existing)
    entry["fetch_status"] = resp["status"]
    entry["final_url"] = resp["final_url"]
    entry["error"] = resp.get("error")
    if hdrs.get("last-modified"):
        entry["http_last_modified"] = hdrs["last-modified"]
    if hdrs.get("etag"):
        entry["etag"] = hdrs["etag"]
    if hdrs.get("date"):
        entry["http_date"] = hdrs["date"]

    if not body:
        return entry

    parser = PageParser(resp["final_url"] or url)
    try:
        parser.feed(body)
    except Exception:
        pass

    # meta times
    def mv(prop=None, name=None):
        for m in parser.metas:
            if prop and m.get("property", "").lower() == prop.lower():
                return m.get("content")
            if name and m.get("name", "").lower() == name.lower():
                return m.get("content")
        return None

    html_mod = (
        mv(prop="article:modified_time")
        or mv(prop="og:updated_time")
        or mv(name="last-modified")
        or (parser.times[0] if parser.times else None)
        or entry.get("html_modified")
    )
    article = (
        mv(prop="article:modified_time")
        or mv(prop="article:published_time")
        or mv(prop="og:updated_time")
        or mv(name="date")
        or mv(name="DC.date")
        or entry.get("meta_article_time")
    )
    entry["html_modified"] = html_mod
    entry["meta_article_time"] = article

    cms, ver, evid = detect_cms(body, parser.metas, hdrs, parser.comments)
    # special host overrides
    host = urllib.parse.urlparse(url).netloc.lower()
    if "groups.io" in host:
        cms, ver, evid = "Groups.io", None, "groups.io platform"
    if "portal.ardos.org" in host or "nextcloud" in body.lower():
        if "nextcloud" in body.lower() or "url=/nextcloud" in body.lower():
            cms, ver, evid = "Nextcloud", None, "meta refresh to /nextcloud"

    entry["cms"] = cms
    entry["cms_version"] = ver
    entry["cms_evidence"] = evid
    entry["admin_urls"] = admin_candidates(resp["final_url"] or url, cms)

    # admin checks if empty or cms changed
    if not entry.get("admin_checks") or existing.get("cms") != cms:
        checks = []
        for aurl in entry["admin_urls"][:5]:
            time.sleep(0.2)
            r = http_get(aurl, "HEAD")
            st = r["status"]
            if st in (None, 405):
                r = http_get(aurl, "GET")
                st = r["status"]
            checks.append({"url": aurl, "status": st})
        entry["admin_checks"] = checks

    # news
    scored = []
    seen = set()
    for href, text in parser.links:
        sc = score_news(href, text, url)
        if sc <= 0:
            continue
        nu = href.split("#")[0]
        if nu in seen:
            continue
        if nu.rstrip("/") == (resp["final_url"] or url).rstrip("/"):
            continue
        seen.add(nu)
        scored.append((sc, nu, text[:80] or "news"))
    scored.sort(key=lambda x: -x[0])
    cands = scored[:2]

    # MO default news path
    if not cands and cms == "WebToDate":
        p = urllib.parse.urlparse(resp["final_url"] or url)
        origin = f"{p.scheme}://{p.netloc}"
        cands = [(10, origin + "/informacni-servis/zpravodajstvi/", "Zpravodajství")]

    news_pages = []
    for sc, nurl, ntitle in cands[:2]:
        time.sleep(0.3)
        nr = http_get(nurl)
        if (nr["status"] or 0) >= 400 and nr["status"] != 200:
            if nr["status"] in (404, 410):
                continue
        nbody = nr["body"] or ""
        if not nbody:
            continue
        items = extract_items(nbody, nr["final_url"] or nurl)
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

    if news_pages:
        entry["news_pages"] = news_pages

    # also extract from homepage
    main_items = extract_items(body, resp["final_url"] or url)
    title, date = best_last_post(entry.get("news_pages") or [], (main_items[0]["title"], main_items[0].get("date")) if main_items else None)
    if title:
        entry["last_post_title"] = title
        entry["last_post_date"] = date

    # prefer non-PDF from news_pages over existing PDF last_post
    t2, d2 = best_last_post(entry.get("news_pages") or [])
    if t2:
        entry["last_post_title"] = t2
        entry["last_post_date"] = d2

    return entry


def update_captures(enrichment):
    path = os.path.dirname(os.path.abspath(__file__)) + "/data/captures.jsonl"
    with open(path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    by = {r["url"]: i for i, r in enumerate(rows)}
    updated = 0
    for url, en in enrichment.items():
        title, date = en.get("last_post_title"), en.get("last_post_date")
        if not title and not date:
            continue
        idx = by.get(url)
        if idx is None:
            for k, i in by.items():
                if k.rstrip("/") == url.rstrip("/"):
                    idx = i
                    break
        if idx is None:
            continue
        row = rows[idx]
        changed = False
        # upgrade if missing or looks like pdf filename
        old_t = row.get("last_post_title") or ""
        if title and (not old_t or old_t.lower().endswith(".pdf")):
            if title != old_t:
                row["last_post_title"] = title
                changed = True
        if date and (not row.get("last_post_date") or (old_t.lower().endswith(".pdf") if old_t else False)):
            row["last_post_date"] = date
            changed = True
        if changed:
            note = row.get("notes") or ""
            if "enriched_from_news_subpage" not in note:
                row["notes"] = (note + "; " if note else "") + "enriched_from_news_subpage"
            updated += 1
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return updated


def main():
    path = os.path.dirname(os.path.abspath(__file__)) + "/data/enrichment.json"
    with open(path) as f:
        enrichment = json.load(f)

    # Fix last_post selection for all entries that have news_pages (no refetch)
    for url, e in enrichment.items():
        t, d = best_last_post(e.get("news_pages") or [])
        if t:
            e["last_post_title"] = t
            e["last_post_date"] = d
        # rename custom Webnode
        if e.get("cms") == "custom" and "Webnode" in (e.get("cms_evidence") or ""):
            e["cms"] = "Webnode"

    # Re-fetch unknowns + WebToDate suspects
    targets = [
        u
        for u, e in enrichment.items()
        if e.get("cms") in ("unknown",) or "acr.mo.gov.cz" in u or "mocr.mo.gov.cz" in u
    ]
    for u in targets:
        enrichment[u] = refine_url(u, enrichment[u])

    with open(path, "w") as f:
        json.dump(enrichment, f, ensure_ascii=False, indent=2)

    updated = update_captures(enrichment)
    from collections import Counter

    cms_c = Counter(e.get("cms") for e in enrichment.values())
    news_n = sum(1 for e in enrichment.values() if e.get("news_pages"))
    news_items = sum(1 for e in enrichment.values() for np in e.get("news_pages") or [] if np.get("last_item"))
    print("---SUMMARY---")
    print(f"entries={len(enrichment)}")
    print(f"cms_counts={dict(cms_c)}")
    print(f"sites_with_news_pages={news_n}")
    print(f"news_pages_with_items={news_items}")
    print(f"captures_updated={updated}")


if __name__ == "__main__":
    main()
