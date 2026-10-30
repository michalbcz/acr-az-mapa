#!/usr/bin/env python3
"""Capture unit webs + social evidence for ACR AZ report."""
import json, os, re, subprocess, sys, time, html as htmllib
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "screenshots"
DATA = ROOT / "data" / "captures.jsonl"
PROFILE = Path("/tmp/chrome-units-profile")
HTML_DIR = Path("/tmp/units-html")
SHOTS.mkdir(parents=True, exist_ok=True)
HTML_DIR.mkdir(parents=True, exist_ok=True)
PROFILE.mkdir(parents=True, exist_ok=True)

URLS = [
    # Batch A — unit webs
    "https://111prapor.cz/",
    "https://petatricatnici.eu/",
    "https://petatricatnici.eu/forum/",
    "https://www.mpsfar.cz/",
    "https://www.az44lmopr.cz/",
    "https://43vysadkovypluk.mo.gov.cz/",
    "https://14plogp.mo.gov.cz/aktivni-zaloha",
    "https://akis.mo.gov.cz/aktivni-zaloha",
    "https://7mb.mo.gov.cz/aktivni-zaloha-7mb",
    "https://resco-cr.cz/",
    "https://svz-cr.cz/",
    "https://www.csol.cz/",
    "https://pokos.mo.gov.cz/",
    # Batch B — social
    "https://www.facebook.com/ArmadaCeskerepubliky",
    "https://www.facebook.com/MinisterstvoobranyCeskerepubliky",
    "https://www.facebook.com/doarmady.cz",
    "https://www.facebook.com/aktivnizalohy",
    "https://www.facebook.com/111prUzOAZPraha",
    "https://www.facebook.com/AZJihlava",
    "https://www.facebook.com/az44lmopr",
    "https://www.facebook.com/profile.php?id=100090037384494",
    "https://www.facebook.com/43vysadkovypluk",
    "https://www.facebook.com/AZTVcz",
    "https://www.facebook.com/PesirotaAZPraha",
    "https://www.facebook.com/pesirotaazhradeckralove",
    "https://www.facebook.com/AktivniZalohaKVVCeskeBudejovice",
    "https://www.instagram.com/armadaceskerepubliky/",
    "https://www.instagram.com/do_armady/",
    "https://www.instagram.com/az_hlmpraha/",
    "https://www.instagram.com/az44lmopr/",
    "https://www.youtube.com/@AZTVcz",
    "https://www.youtube.com/@tvarmycz",
    "https://x.com/ArmadaCR",
    "https://x.com/ObranaTweetuje",
]

SOCIAL_RE = re.compile(
    r"(?:https?://)?(?:(?:www|cs-cz|m)\.)?(?:"
    r"facebook\.com/(?:groups/|share/g/|people/|profile\.php\?id=)?[^\s\"'<>]+|"
    r"fb\.me/[^\s\"'<>]+|"
    r"t\.me/[^\s\"'<>]+|"
    r"telegram\.me/[^\s\"'<>]+|"
    r"discord\.(?:gg|com)/[^\s\"'<>]+|"
    r"(?:chat\.)?whatsapp\.com/[^\s\"'<>]+|"
    r"wa\.me/[^\s\"'<>]+|"
    r"signal\.me/[^\s\"'<>]+|"
    r"signal\.group/[^\s\"'<>]+"
    r")",
    re.I,
)
HIDDEN_HINT = re.compile(
    r"(facebook|fb\.com|telegram|t\.me|discord|whatsapp|wa\.me|signal|skupina|group)",
    re.I,
)
NAV_TITLES = {
    "kalendář akcí", "kontakty", "o nás", "aktuality", "informace", "krátké zprávy",
    "nábor do ačr", "záloha ozbrojených sil", "vojenští důchodci", "různé k řešení",
    "všechny aktuality", "domů", "úvodní stránka", "home", "menu", "login", "přihlásit",
}

def slug_for(url):
    p = urlparse(url)
    host = p.netloc.replace("www.", "").replace(".mo.gov.cz", "").replace(".", "-")
    path = p.path.strip("/").replace("/", "-").replace("@", "")
    q = ""
    if p.query:
        q = "-" + re.sub(r"[^a-zA-Z0-9]+", "-", p.query).strip("-")
    slug = host if not path else f"{host}-{path}"
    slug = slug + q
    parts = slug.split("-")
    if len(parts) > 12:
        slug = "-".join(parts[:12])
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-").lower()
    return slug[:90]

def classify(u):
    lu = u.lower()
    if "share.php" in lu:
        return "facebook_share"
    if "facebook.com/groups" in lu or "facebook.com/share/g/" in lu:
        return "facebook_group"
    if "facebook.com" in lu or "fb.me" in lu:
        return "facebook"
    if "t.me" in lu or "telegram" in lu:
        return "telegram"
    if "discord" in lu:
        return "discord"
    if "whatsapp" in lu or "wa.me" in lu:
        return "whatsapp"
    if "signal.me" in lu or "signal.group" in lu:
        return "signal"
    return "other_social"

def clean_url(u):
    u = htmllib.unescape(u).replace("&amp;", "&").split("#")[0]
    u = re.sub(r"[),.;\"']+$", "", u)
    if not u.startswith("http"):
        u = "https://" + u
    u = u.replace("://cs-cz.facebook.com", "://www.facebook.com")
    u = u.replace("://m.facebook.com", "://www.facebook.com")
    return u.rstrip("/")

def is_social_host(url):
    h = urlparse(url).netloc.lower()
    return any(x in h for x in ("facebook.com", "instagram.com", "youtube.com", "x.com", "twitter.com", "t.me"))

def chrome_capture(url, shot_path, html_path):
    """One Chrome launch: viewport screenshot + dump-dom."""
    if shot_path.exists():
        shot_path.unlink()
    cmd = [
        "google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu",
        "--disable-dev-shm-usage", "--hide-scrollbars",
        f"--user-data-dir={PROFILE}",
        "--window-size=1400,1800",
        f"--screenshot={shot_path}",
        "--dump-dom",
        "--virtual-time-budget=10000",
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        html = r.stdout or ""
        err = (r.stderr or "")[-400:]
        if len(html) > 200:
            html_path.write_text(html, encoding="utf-8", errors="replace")
        else:
            html = ""
        ok = shot_path.exists() and shot_path.stat().st_size > 1000
        return ok, html, err
    except Exception as ex:
        return False, "", str(ex)

def fetch_html_curl(url, html_path):
    try:
        r = subprocess.run(
            [
                "curl", "-fsSL", "-L",
                "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "--max-time", "60",
                "-w", "\n__HTTP_CODE__:%{http_code}",
                url,
            ],
            capture_output=True, text=True, timeout=70,
        )
        out = r.stdout or ""
        code = None
        m = re.search(r"\n__HTTP_CODE__:(\d+)\s*$", out)
        if m:
            code = int(m.group(1))
            out = out[: m.start()]
        if r.returncode == 0 and out and len(out) > 100:
            html_path.write_text(out, encoding="utf-8", errors="replace")
            return out, code
        # even on non-zero, keep body if present
        if out and len(out) > 100:
            html_path.write_text(out, encoding="utf-8", errors="replace")
            return out, code
        return "", code
    except Exception:
        return "", None

def find_hidden_links(html, page_url):
    found, seen = [], set()
    # aria-label hidden pattern
    for m in re.finditer(
        r'aria-label="(https?://[^"]*(?:facebook|t\.me|telegram|discord|whatsapp|wa\.me|signal)[^"]*)"',
        html, re.I,
    ):
        u = clean_url(m.group(1))
        t = classify(u)
        if t == "facebook_share":
            continue
        key = u.lower()
        if key not in seen:
            seen.add(key)
            found.append({"url": u, "type": t, "source": "aria_label_hidden"})
    for m in SOCIAL_RE.finditer(html):
        u = clean_url(m.group(0))
        t = classify(u)
        if t == "facebook_share":
            continue
        if u.rstrip("/").endswith(("facebook.com", "instagram.com", "t.me", "telegram.me")):
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({"url": u, "type": t, "source": "html_scan"})
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1)
        if SOCIAL_RE.search(href) and href.startswith("http"):
            u = clean_url(href)
            t = classify(u)
            if t == "facebook_share":
                continue
            key = u.lower()
            if key not in seen:
                seen.add(key)
                found.append({"url": u, "type": t, "source": "href"})
    # don't count the page's own social URL as "hidden"
    own = page_url.rstrip("/").lower()
    found = [h for h in found if h["url"].lower() != own and not own.startswith(h["url"].lower())]
    return found

def extract_latest_post(html, url):
    title = author = date = None
    notes = []
    candidates = []

    for block in re.findall(r"<article[^>]*>(.*?)</article>", html, re.I | re.S)[:12]:
        hm = re.search(r"<h[1-4][^>]*>\s*(?:<a[^>]*>)?\s*([^<]{5,220})", block, re.I)
        tm = re.search(r'<time[^>]*(?:datetime=["\']([^"\']+)["\'])?[^>]*>([^<]*)</time>', block, re.I)
        am = re.search(r'(?:field--name-uid|username|author|byline)[^>]*>.*?>([^<]{2,80})<', block, re.I | re.S)
        if not am:
            am = re.search(r'class="author"[^>]*>\s*(?:<i[^>]*>.*?</i>\s*)?([^<]{2,80})', block, re.I | re.S)
        if hm:
            t = htmllib.unescape(re.sub(r"\s+", " ", hm.group(1)).strip())
            if t.lower() in NAV_TITLES:
                continue
            dt = None
            if tm:
                dt = (tm.group(1) or tm.group(2) or "").strip() or None
            if not dt:
                dm = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})", block)
                dt = dm.group(1) if dm else None
            candidates.append((dt, t, am.group(1).strip() if am else None, "article"))

    for block in re.findall(
        r'class="[^"]*views-row[^"]*"[^>]*>(.{0,3000}?)(?:class="[^"]*views-row|$)',
        html, re.I | re.S,
    )[:10]:
        hm = re.search(r"<h[2-4][^>]*>\s*(?:<a[^>]*>)?\s*([^<]{5,220})", block, re.I)
        if not hm:
            hm = re.search(r'<a[^>]+href="/[^"]+"[^>]*>\s*([^<]{8,220})\s*</a>', block, re.I)
        tm = re.search(r'<time[^>]*(?:datetime=["\']([^"\']+)["\'])?[^>]*>([^<]*)</time>', block, re.I)
        if tm:
            dt = (tm.group(1) or tm.group(2) or "").strip() or None
        else:
            dm = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})", block)
            dt = dm.group(1) if dm else None
        am = re.search(r'(?:field--name-uid|username|author)[^>]*>.*?>([^<]{2,80})<', block, re.I | re.S)
        if hm:
            t = htmllib.unescape(re.sub(r"\s+", " ", hm.group(1)).strip())
            if t.lower() not in NAV_TITLES:
                candidates.append((dt, t, am.group(1).strip() if am else None, "views-row"))

    # phpbb / forum topics
    for m in re.finditer(
        r'(?:class="[^"]*topic[^"]*"|class="[^"]*topictitle[^"]*").{0,400}?<a[^>]+>([^<]{5,200})</a>',
        html, re.I | re.S,
    ):
        t = htmllib.unescape(re.sub(r"\s+", " ", m.group(1)).strip())
        if t.lower() not in NAV_TITLES:
            candidates.append((None, t, None, "forum_topic"))
            break

    # news list items with Czech dates
    for m in re.finditer(
        r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4}).{0,80}?(?:</[^>]+>\s*){0,6}<a[^>]+>\s*([^<]{8,220})\s*</a>",
        html, re.I | re.S,
    ):
        candidates.append((m.group(1), htmllib.unescape(re.sub(r"\s+", " ", m.group(2)).strip()), None, "dated_link"))

    for m in re.finditer(
        r'<a[^>]+>\s*([^<]{8,220})\s*</a>.{0,80}?(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})',
        html, re.I | re.S,
    ):
        t = htmllib.unescape(re.sub(r"\s+", " ", m.group(1)).strip())
        if t.lower() not in NAV_TITLES and len(t) > 10:
            candidates.append((m.group(2), t, None, "link_dated"))

    # YouTube channel / video title
    if "youtube.com" in url:
        og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not og:
            og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
        vt = re.search(r'"title":\{"runs":\[\{"text":"([^"]{5,200})"\}', html)
        if vt:
            candidates.append((None, htmllib.unescape(vt.group(1)), None, "yt_video"))
        elif og:
            candidates.append((None, htmllib.unescape(og.group(1)), None, "yt_og"))

    best = None
    for c in candidates:
        if not c[1]:
            continue
        if c[0]:
            best = c
            break
        if best is None:
            best = c
    if best:
        date, title, author, src = best
        notes.append(f"post_source={src}")

    if not title:
        m = re.search(r"Aktualit[ya].{0,500}?href=\"[^\"]+\"[^>]*>\s*([^<]{8,220})", html, re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            notes.append("post_source=aktuality_section")

    og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not og:
        og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
    if og:
        notes.append(f"og:title={og.group(1)[:120]}")

    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]{0,120})</a>', html, re.I):
        href, text = m.group(1), m.group(2)
        if HIDDEN_HINT.search(text) and not SOCIAL_RE.search(href):
            if re.search(r"facebook|telegram|whatsapp|discord|signal", text, re.I):
                notes.append(f"social_mention text={text.strip()[:60]!r} href={href[:100]}")

    return title, author, date, notes

def detect_status(url, html, http_code, shot_ok, notes):
    low = (html or "").lower()
    host = urlparse(url).netloc.lower()

    if http_code == 404 or re.search(r"\b404\b|not found|stránka nebyla nalezena|page not found", low):
        if len(low) < 50000 and ("404" in low or "not found" in low or "nenalezena" in low):
            return "not_found"

    # login walls
    login_signals = 0
    if "facebook.com" in host:
        if any(x in low for x in ("log into facebook", "log in to facebook", "přihlaste se", "must log in",
                                   "login_form", "email_container", "create new account", "přihlásit se")):
            login_signals += 2
        if "login" in low and ("password" in low or "heslo" in low):
            login_signals += 1
    if "instagram.com" in host:
        if any(x in low for x in ("log in", "přihlásit", "loginpage", "accounts/login", "signup")):
            login_signals += 2
    if host in ("x.com", "twitter.com") or host.endswith(".x.com"):
        if any(x in low for x in ("sign in", "log in", "přihlásit", "before you can continue", "login")):
            login_signals += 2
    if login_signals >= 2:
        return "login_wall"

    if any(x in low for x in ("access denied", "request blocked", "captcha", "cf-browser-verification",
                               "just a moment", "attention required", "blocked")):
        if len(low) < 30000:
            return "blocked"

    if not shot_ok and not html:
        return "error"
    if not shot_ok:
        notes.append("screenshot_failed")
        return "error" if not html else "ok"
    return "ok"

def process(url):
    slug = slug_for(url)
    shot = SHOTS / f"{slug}.png"
    html_path = HTML_DIR / f"{slug}.html"
    print(f"=== {slug} ===", flush=True)

    if shot.exists() and shot.stat().st_size > 1000:
        print(f"SKIP existing PNG {shot.name}", flush=True)
        return None

    notes_extra = []
    ok = False
    html = ""
    http_code = None
    try:
        ok, html, err = chrome_capture(url, shot, html_path)
        if not ok and err:
            notes_extra.append(f"chrome_err={err[:180]}")
    except Exception as ex:
        ok = False
        notes_extra.append(f"shot_exception={ex}")

    # curl fallback / supplement for HTTP code and if chrome DOM empty
    curl_html, http_code = fetch_html_curl(url, Path(str(html_path) + ".curl"))
    if not html and curl_html:
        html = curl_html
        html_path.write_text(html, encoding="utf-8", errors="replace")
        notes_extra.append("html_from_curl")
    elif curl_html and len(curl_html) > len(html) * 1.5 and not is_social_host(url):
        # prefer richer static HTML for news parsing on unit sites
        html = curl_html
        html_path.write_text(html, encoding="utf-8", errors="replace")
        notes_extra.append("html_prefer_curl")

    title, author, date, notes = extract_latest_post(html, url) if html else (None, None, None, ["html_empty"])
    notes = list(notes) + notes_extra
    hidden = find_hidden_links(html, url) if html else []

    # For pure social pages, last_post from public scrape is often unavailable
    if is_social_host(url) and not date:
        if not title or (title and any(x in (title or "").lower() for x in ("facebook", "instagram", "log in", "x.com", "twitter"))):
            # keep og:title in notes but null out generic login titles as "post"
            if title and re.search(r"^(facebook|instagram|log in|x\.com)", title, re.I):
                title = None
            notes.append("no_public_post_extractable")

    status = detect_status(url, html, http_code, ok, notes)
    if http_code:
        notes.append(f"http={http_code}")

    # soft note when no dated news on unit sites
    if status == "ok" and not is_social_host(url) and not date and not title:
        notes.append("no_dated_news_on_page")

    rec = {
        "url": url,
        "slug": slug,
        "screenshot_path": f"screenshots/{slug}.png" if shot.exists() and shot.stat().st_size > 1000 else None,
        "last_post_title": title,
        "last_post_author": author,
        "last_post_date": date,
        "status": status,
        "hidden_links": hidden,
        "notes": "; ".join(notes) if notes else "",
    }
    print(
        json.dumps(
            {"slug": slug, "status": status, "title": (title or "")[:60], "date": date, "hidden": len(hidden), "shot": bool(rec["screenshot_path"])},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return rec

def main():
    urls = URLS
    if len(sys.argv) > 1:
        # allow index selection
        idxs = [int(x) for x in sys.argv[1:]]
        urls = [URLS[i] for i in idxs]

    results = []
    skipped = 0
    with DATA.open("a", encoding="utf-8") as f:
        for url in urls:
            slug = slug_for(url)
            shot = SHOTS / f"{slug}.png"
            if shot.exists() and shot.stat().st_size > 1000:
                print(f"SKIP {slug}", flush=True)
                skipped += 1
                continue
            rec = process(url)
            if rec is None:
                skipped += 1
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            results.append(rec)
            time.sleep(0.5)

    from collections import Counter
    c = Counter(r["status"] for r in results)
    print(f"DONE processed={len(results)} skipped={skipped} statuses={dict(c)}", flush=True)
    for r in results:
        print(f"  {r['status']:12} {r['slug']}", flush=True)

if __name__ == "__main__":
    main()
