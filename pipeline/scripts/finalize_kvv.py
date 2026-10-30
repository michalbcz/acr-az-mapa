#!/usr/bin/env python3
import json, re, html as htmllib
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = Path("/tmp/kvv-html")
JSONL = ROOT / "data" / "captures.jsonl"

NAV_TITLES = {
    "kalendář akcí", "kontakty", "o nás", "aktuality", "informace", "krátké zprávy",
    "nábor do ačr", "záloha ozbrojených sil", "vojenští důchodci", "různé k řešení",
    "všechny aktuality", "domů", "úvodní stránka",
}

SOCIAL_RE = re.compile(
    r"(?:https?://)?(?:(?:www|cs-cz)\.)?(?:facebook\.com/(?:groups/|share/g/|people/|profile\.php\?id=)?[^\s\"'<>]+|fb\.me/[^\s\"'<>]+|t\.me/[^\s\"'<>]+|telegram\.me/[^\s\"'<>]+|discord\.(?:gg|com)/[^\s\"'<>]+|(?:chat\.)?whatsapp\.com/[^\s\"'<>]+|wa\.me/[^\s\"'<>]+)",
    re.I,
)

def clean_url(u):
    u = htmllib.unescape(u).replace("&amp;", "&").split("#")[0]
    u = re.sub(r"[),.;\"']+$", "", u)
    if not u.startswith("http"):
        u = "https://" + u
    # normalize cs-cz.facebook -> www
    u = u.replace("://cs-cz.facebook.com", "://www.facebook.com")
    return u.rstrip("/")

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
    return "other_social"

def find_hidden(html):
    found, seen = [], set()
    # aria-label hidden pattern (href="#")
    for m in re.finditer(r'aria-label="(https?://[^"]*(?:facebook|t\.me|telegram|discord|whatsapp|wa\.me)[^"]*)"', html, re.I):
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
        if u.endswith("facebook.com"):
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({"url": u, "type": t, "source": "html_scan"})
    return found

def extract_post(html):
    notes = []
    articles = re.findall(r"<article\b[^>]*>(.*?)</article>", html, re.I | re.S)
    best = None
    for block in articles:
        hm = re.search(r"<h[23][^>]*>\s*<a[^>]*>\s*([^<]{3,220})\s*</a>", block, re.I)
        if not hm:
            continue
        title = htmllib.unescape(re.sub(r"\s+", " ", hm.group(1)).strip())
        if title.lower() in NAV_TITLES:
            continue
        dm = re.search(r'class="date"[^>]*>\s*(?:<i[^>]*>.*?</i>\s*)?([^<]{4,40})', block, re.I | re.S)
        if not dm:
            dm = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", block)
        am = re.search(r'class="author"[^>]*>\s*(?:<i[^>]*>.*?</i>\s*)?([^<]{2,80})', block, re.I | re.S)
        date = dm.group(1).strip() if dm else None
        author = am.group(1).strip() if am else None
        if date or author:
            best = (date, title, author, "article_teaser")
            if date:
                break
            if best and best[0]:
                break
    if best:
        date, title, author, src = best
        notes.append(f"post_source={src}")
        return title, author, date, notes

    for m in re.finditer(r'href="(/aktuality/[^"/]+)"[^>]*>\s*([^<]{5,220})\s*<', html, re.I):
        href, text = m.group(1), htmllib.unescape(re.sub(r"\s+", " ", m.group(2)).strip())
        if text.lower() in NAV_TITLES:
            continue
        notes.append(f"post_source=aktuality_href:{href}")
        return text, None, None, notes

    notes.append("no_dated_news_on_page")
    return None, None, None, notes

def main():
    rows = [json.loads(l) for l in JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = []
    for rec in rows:
        slug = rec["slug"]
        html_path = HTML_DIR / f"{slug}.html"
        html = html_path.read_text(encoding="utf-8", errors="replace") if html_path.exists() else ""
        notes = []
        is_404 = bool(re.search(r"<title>[^<]*(nebyla nalezena|404|not found)[^<]*</title>", html, re.I))
        title = author = date = None
        hidden = []
        if not html:
            status = "partial" if rec.get("screenshot_path") else "error"
            notes.append("html_empty")
        elif is_404:
            status = "not_found"
            notes.append("http_404_page")
            # discover alternate AZ path from 404 chrome
            alts = re.findall(r'href="(/[^"]*aktivni[^"]*)"', html, re.I)
            alts = sorted(set(alts))
            if alts:
                host = re.match(r"https?://[^/]+", rec["url"])
                host = host.group(0) if host else ""
                notes.append("alternate_paths=" + ",".join(host + a for a in alts[:5]))
            hidden = find_hidden(html)
        else:
            title, author, date, n2 = extract_post(html)
            notes.extend(n2)
            hidden = find_hidden(html)
            status = "ok" if rec.get("screenshot_path") else "partial"

        # decode %25 encodings in URLs for readability
        for h in hidden:
            h["url"] = unquote(unquote(h["url"]))

        new = {
            "url": rec["url"],
            "slug": slug,
            "screenshot_path": rec.get("screenshot_path"),
            "last_post_title": title,
            "last_post_author": author,
            "last_post_date": date,
            "status": status,
            "hidden_links": hidden,
            "notes": "; ".join(notes) if notes else "",
        }
        out.append(new)
        print(f"{status:10} {slug[:50]:50} | {title!r} | {author!r} | {date!r} | links={len(hidden)}")

    JSONL.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
    print("FINAL", len(out))

if __name__ == "__main__":
    main()
