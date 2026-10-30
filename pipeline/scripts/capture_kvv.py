#!/usr/bin/env python3
import json, os, re, subprocess, sys, time, html as htmllib
from pathlib import Path
from urllib.parse import urlparse, urljoin
from html.parser import HTMLParser
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "screenshots"
DATA = ROOT / "data" / "captures.jsonl"
PROFILE = Path("/tmp/chrome-kvv-profile")
HTML_DIR = Path("/tmp/kvv-html")
SHOTS.mkdir(parents=True, exist_ok=True)
HTML_DIR.mkdir(parents=True, exist_ok=True)
PROFILE.mkdir(parents=True, exist_ok=True)

URLS = [
    "https://kvv-hlavnimestopraha.mo.gov.cz/aktivni-zaloha/111-prapor-uzemni-obrany-aktivni-zaloha",
    "https://kvv-praha-stredoceskykraj.mo.gov.cz/zaloha-ozbrojenych-sil/aktivni-zaloha",
    "https://kvv-hradeckralove.mo.gov.cz/aktivni-zaloha",
    "https://kvv-liberec.mo.gov.cz/",
    "https://kvvusti.mo.gov.cz/aktivni-zaloha/aktivni-zaloha-u-kvv-ul-151-prapor-uzemni-obrany",
    "https://kvv-karlovyvary.mo.gov.cz/161-prapor-uzemni-obrany",
    "https://kvv-plzen.mo.gov.cz/",
    "https://kvvcb.mo.gov.cz/zaloha-ozbrojenych-sil/aktivni-zaloha/181-prapor-uzemni-obrany",
    "https://kvv-jihlava.mo.gov.cz/",
    "https://kvv-pardubice.mo.gov.cz/naborkariera/aktivni-zaloha/201-prapor-uzemni-obrany",
    "https://kvv-ostrava.mo.gov.cz/do-armady/aktivni-zaloha/aktivni-zaloha-u-kvv-ostrava",
    "https://kvv-olomouc.mo.gov.cz/az-u-kvv",
    "https://kvv-zlin.mo.gov.cz/vykon-branne-povinnosti/aktivni-zaloha-ozbrojenych-sil-cr/aktivni-zaloha-u-krajskeho-vojenskeho",
    "https://kvv-brno.mo.gov.cz/aktivni-zaloha-0",
]
SOCIAL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:facebook\.com/(?:groups/|share/g/)?[^\s\"'<>]+|fb\.me/[^\s\"'<>]+|t\.me/[^\s\"'<>]+|telegram\.me/[^\s\"'<>]+|discord\.(?:gg|com)/[^\s\"'<>]+|(?:chat\.)?whatsapp\.com/[^\s\"'<>]+|wa\.me/[^\s\"'<>]+)",
    re.I,
)
HIDDEN_HINT = re.compile(r"(facebook|fb\.com|telegram|t\.me|discord|whatsapp|wa\.me|skupina|group)", re.I)

def slug_for(url):
    p = urlparse(url)
    host = p.netloc.replace(".mo.gov.cz", "").replace(".", "-")
    path = p.path.strip("/").replace("/", "-")
    slug = host if not path else f"{host}-{path}"
    parts = slug.split("-")
    if len(parts) > 10:
        slug = "-".join(parts[:10])
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-").lower()
    return slug[:90]

def classify(u):
    u = u.lower()
    if "facebook.com/groups" in u or "facebook.com/share/g/" in u:
        return "facebook_group"
    if "facebook.com" in u or "fb.me" in u:
        return "facebook"
    if "t.me" in u or "telegram" in u:
        return "telegram"
    if "discord" in u:
        return "discord"
    if "whatsapp" in u or "wa.me" in u:
        return "whatsapp"
    return "other_social"

def clean_url(u):
    u = htmllib.unescape(u).split("#")[0]
    u = re.sub(r"[),.;\"']+$", "", u)
    if not u.startswith("http"):
        u = "https://" + u
    return u.rstrip("/")
def chrome_screenshot(url, shot_path):
    cmd = [
        "google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu",
        "--disable-dev-shm-usage", f"--user-data-dir={PROFILE}",
        "--window-size=1400,1800", f"--screenshot={shot_path}", url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    ok = shot_path.exists() and shot_path.stat().st_size > 1000
    return ok, (r.stderr or "")[-300:]

def fetch_html(url, html_path):
    # Prefer curl for HTML (faster, less chrome contention)
    try:
        r = subprocess.run(
            ["curl", "-fsSL", "-A", "Mozilla/5.0 (compatible; ACR-map/1.0)", "--max-time", "60", url],
            capture_output=True, text=True, timeout=70,
        )
        if r.returncode == 0 and r.stdout and len(r.stdout) > 200:
            html_path.write_text(r.stdout, encoding="utf-8", errors="replace")
            return r.stdout
    except Exception:
        pass
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ACR-map/1.0)"})
        html = urlopen(req, timeout=60).read().decode("utf-8", "replace")
        html_path.write_text(html, encoding="utf-8")
        return html
    except Exception as ex:
        html_path.write_text("", encoding="utf-8")
        return ""
def find_hidden_links(html):
    found = []
    seen = set()
    for m in SOCIAL_RE.finditer(html):
        u = clean_url(m.group(0))
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({"url": u, "type": classify(u), "source": "html_scan"})
    # also from href attributes more carefully
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1)
        if SOCIAL_RE.search(href):
            u = clean_url(urljoin("https://example.com", href) if href.startswith("http") else clean_url(href))
            if href.startswith("http"):
                u = clean_url(href)
            else:
                continue
            key = u.lower()
            if key not in seen:
                seen.add(key)
                found.append({"url": u, "type": classify(u), "source": "href"})
    return found

def extract_latest_post(html):
    title = author = date = None
    notes = []
    candidates = []

    for block in re.findall(r"<article[^>]*>(.*?)</article>", html, re.I | re.S)[:8]:
        hm = re.search(r"<h[23][^>]*>\s*(?:<a[^>]*>)?\s*([^<]{5,220})", block, re.I)
        tm = re.search(r'<time[^>]*(?:datetime=["\']([^"\']+)["\'])?[^>]*>([^<]*)</time>', block, re.I)
        am = re.search(r'(?:field--name-uid|username|author)[^>]*>.*?>([^<]{2,80})<', block, re.I | re.S)
        if hm:
            dt = None
            if tm:
                dt = (tm.group(1) or tm.group(2) or "").strip() or None
            candidates.append((dt, hm.group(1).strip(), am.group(1).strip() if am else None, "article"))

    for block in re.findall(r'class="[^"]*views-row[^"]*"[^>]*>(.{0,2500}?)(?:class="[^"]*views-row|$)', html, re.I | re.S)[:8]:
        hm = re.search(r"<h[2-4][^>]*>\s*(?:<a[^>]*>)?\s*([^<]{5,220})", block, re.I)
        if not hm:
            hm = re.search(r'<a[^>]+href="/[^"]+"[^>]*>\s*([^<]{8,220})\s*</a>', block, re.I)
        tm = re.search(r'<time[^>]*(?:datetime=["\']([^"\']+)["\'])?[^>]*>([^<]*)</time>', block, re.I)
        if not tm:
            dm = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})", block)
            dt = dm.group(1) if dm else None
        else:
            dt = (tm.group(1) or tm.group(2) or "").strip() or None
        am = re.search(r'(?:field--name-uid|username|author)[^>]*>.*?>([^<]{2,80})<', block, re.I | re.S)
        if hm:
            candidates.append((dt, hm.group(1).strip(), am.group(1).strip() if am else None, "views-row"))

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
        m = re.search(r"Aktualit[ya].{0,400}?href=\"[^\"]+\"[^>]*>\s*([^<]{8,220})", html, re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            notes.append("post_source=aktuality_section")

    og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not og:
        og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
    if og:
        notes.append(f"og:title={og.group(1)[:120]}")

    # soft social mentions in anchor text
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]{0,120})</a>', html, re.I):
        href, text = m.group(1), m.group(2)
        if HIDDEN_HINT.search(text) and not SOCIAL_RE.search(href):
            if re.search(r"facebook|telegram|whatsapp|discord", text, re.I):
                notes.append(f"social_mention text={text.strip()[:60]!r} href={href[:100]}")

    return title, author, date, notes
def process(url):
    slug = slug_for(url)
    shot = SHOTS / f"{slug}.png"
    html_path = HTML_DIR / f"{slug}.html"
    print(f"=== {slug} ===", flush=True)
    notes_extra = []
    try:
        ok, err = chrome_screenshot(url, shot)
        if not ok and err:
            notes_extra.append(f"chrome_err={err[:180]}")
    except Exception as ex:
        ok = False
        notes_extra.append(f"shot_exception={ex}")
    html = fetch_html(url, html_path)
    title, author, date, notes = extract_latest_post(html) if html else (None, None, None, ["html_empty"])
    notes = list(notes) + notes_extra
    hidden = find_hidden_links(html) if html else []
    status = "ok" if ok and html else ("partial" if (ok or html) else "error")
    if not ok:
        notes.append("screenshot_failed")
    rec = {
        "url": url,
        "slug": slug,
        "screenshot_path": f"screenshots/{slug}.png" if shot.exists() else None,
        "last_post_title": title,
        "last_post_author": author,
        "last_post_date": date,
        "status": status,
        "hidden_links": hidden,
        "notes": "; ".join(notes) if notes else "",
    }
    print(json.dumps({"slug": slug, "status": status, "title": title, "date": date, "hidden": len(hidden)}, ensure_ascii=False), flush=True)
    return rec

def main():
    urls = URLS
    if len(sys.argv) > 1:
        urls = [URLS[int(x)] for x in sys.argv[1:]]
    results = []
    with DATA.open("a", encoding="utf-8") as f:
        for url in urls:
            rec = process(url)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            results.append(rec)
            time.sleep(0.8)
    ok_n = sum(1 for r in results if r["status"] in ("ok", "partial"))
    print(f"DONE {ok_n}/{len(results)}", flush=True)
    for r in results:
        print(f"  {r['status']:8} {r['slug']}", flush=True)

if __name__ == "__main__":
    main()
