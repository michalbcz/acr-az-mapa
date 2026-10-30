#!/usr/bin/env python3
"""Reparse dumped HTML into improved captures.jsonl."""
import json, re, html as htmllib
from pathlib import Path
from urllib.parse import urlparse, urljoin

ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = Path("/tmp/kvv-html")
JSONL = ROOT / "data" / "captures.jsonl"

SOCIAL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:facebook\.com/(?:groups/|share/g/)?[^\s\"'<>]+|fb\.me/[^\s\"'<>]+|t\.me/[^\s\"'<>]+|telegram\.me/[^\s\"'<>]+|discord\.(?:gg|com)/[^\s\"'<>]+|(?:chat\.)?whatsapp\.com/[^\s\"'<>]+|wa\.me/[^\s\"'<>]+)",
    re.I,
)

def clean_url(u):
    u = htmllib.unescape(u).replace("&amp;", "&").split("#")[0]
    u = re.sub(r"[),.;\"']+$", "", u)
    if not u.startswith("http"):
        u = "https://" + u
    return u.rstrip("/")

def classify(u):
    u = u.lower()
    if "share.php" in u or "facebook.com/share" in u and "share/g/" not in u:
        return "facebook_share"
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

def find_hidden(html):
    found, seen = [], set()
    for m in SOCIAL_RE.finditer(html):
        u = clean_url(m.group(0))
        t = classify(u)
        if t == "facebook_share":
            continue
        # drop bare # facebook icon placeholders later via empty
        if u.endswith("facebook.com") or u.endswith("facebook.com/"):
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({"url": u, "type": t, "source": "html_scan"})
    return found

def extract_post(html):
    notes = []
    # Primary: news carousel / article teaser with date+author
    articles = re.findall(r"<article\b[^>]*>(.*?)</article>", html, re.I | re.S)
    best = None
    for block in articles:
        hm = re.search(r"<h[23][^>]*>\s*<a[^>]*>\s*([^<]{3,220})\s*</a>", block, re.I)
        if not hm:
            hm = re.search(r"<h[23][^>]*>\s*([^<]{3,220})", block, re.I)
        dm = re.search(r'class="date"[^>]*>\s*(?:<i[^>]*>.*?</i>\s*)?([^<]{4,40})', block, re.I | re.S)
        if not dm:
            dm = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", block)
        am = re.search(r'class="author"[^>]*>\s*(?:<i[^>]*>.*?</i>\s*)?([^<]{2,80})', block, re.I | re.S)
        title = hm.group(1).strip() if hm else None
        if title:
            title = re.sub(r"\s+", " ", title)
        date = dm.group(1).strip() if dm else None
        author = am.group(1).strip() if am else None
        if title and (date or author or "/aktuality/" in block):
            cand = (date, title, author, "article_teaser")
            if date:
                best = cand
                break
            if best is None:
                best = cand
    if best:
        date, title, author, src = best
        notes.append(f"post_source={src}")
        return title, author, date, notes

    # Secondary: first /aktuality/SLUG link that isn't a section index
    for m in re.finditer(r'href="(/aktuality/[^"]+)"[^>]*>\s*([^<]{5,220})\s*<', html, re.I):
        href, text = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if href.rstrip("/") in ("/aktuality",) or text.lower() in ("aktuality", "informace", "všechny aktuality"):
            continue
        # skip nav-ish short labels that are category pages
        if href.count("/") == 2 and text.lower() in ("nábor do ačr", "záloha ozbrojených sil", "vojenští důchodci", "různé k řešení"):
            notes.append(f"nav_aktuality_link={text}")
            continue
        notes.append("post_source=aktuality_href")
        return text, None, None, notes

    notes.append("no_dated_news_on_page")
    return None, None, None, notes

def main():
    rows = []
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))

    out = []
    for rec in rows:
        slug = rec["slug"]
        html_path = HTML_DIR / f"{slug}.html"
        html = html_path.read_text(encoding="utf-8", errors="replace") if html_path.exists() else ""
        notes = []
        if not html:
            title = author = date = None
            hidden = []
            status = rec.get("status") or "error"
            notes.append("html_empty")
            if rec.get("screenshot_path"):
                status = "partial"
        else:
            # detect 404 page
            if re.search(r"Stránka nenalezena|404|Page not found", html, re.I) and len(html) < 80000:
                # soft check: title
                if re.search(r"<title>[^<]*(404|nenalezena|not found)[^<]*</title>", html, re.I):
                    notes.append("http_404_page")
            title, author, date, n2 = extract_post(html)
            notes.extend(n2)
            hidden = find_hidden(html)
            # Instagram also useful? task asked FB groups, Telegram, Discord, WhatsApp — keep FB pages too as social
            status = "ok" if rec.get("screenshot_path") else "partial"
            if "http_404_page" in notes:
                status = "not_found"
                notes.append("url_returns_404")

        # preserve screenshot path
        shot = rec.get("screenshot_path")
        if shot and not (ROOT / shot).exists():
            notes.append("screenshot_missing")
            if status == "ok":
                status = "partial"

        new = {
            "url": rec["url"],
            "slug": slug,
            "screenshot_path": shot,
            "last_post_title": title,
            "last_post_author": author,
            "last_post_date": date,
            "status": status,
            "hidden_links": hidden,
            "notes": "; ".join(notes) if notes else "",
        }
        out.append(new)
        print(json.dumps({"slug": slug, "status": status, "title": title, "author": author, "date": date, "hidden": [(h["type"], h["url"][:60]) for h in hidden]}, ensure_ascii=False))

    JSONL.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
    print("WROTE", len(out))

if __name__ == "__main__":
    main()
