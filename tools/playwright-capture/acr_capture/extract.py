"""Content extraction: latest post, hidden social links, page status.

The HTML (regex) parts are ported 1:1 from ``report/scripts/capture_units.py``
so records stay comparable with the historical captures. Social networks get
best-effort DOM extractors (their markup changes often — failures are noted,
never fatal).
"""
from __future__ import annotations

import html as htmllib
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from .services import Service, is_facebook_group

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
    r"signal\.group/[^\s\"'<>]+|"
    r"instagram\.com/[A-Za-z0-9_.]{2,40}"
    r")",
    re.I,
)
HIDDEN_HINT = re.compile(r"(facebook|fb\.com|telegram|t\.me|discord|whatsapp|wa\.me|signal|skupina|group)", re.I)
NAV_TITLES = {
    "kalendář akcí", "kontakty", "o nás", "aktuality", "informace", "krátké zprávy",
    "nábor do ačr", "záloha ozbrojených sil", "vojenští důchodci", "různé k řešení",
    "všechny aktuality", "domů", "úvodní stránka", "home", "menu", "login", "přihlásit",
    "kdo jsme", "kontakt", "galerie", "fotogalerie", "dokumenty", "historie", "nábor", "mapa stránek",
    "prohlášení o přístupnosti", "zásady cookies", "ochrana osobních údajů",
}
CZ_MONTHS = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4, "května": 5, "června": 6, "července": 7,
    "srpna": 8, "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------
def classify_link(u: str) -> str:
    lu = u.lower()
    if "share.php" in lu or "sharer" in lu:
        return "facebook_share"
    if "facebook.com/groups" in lu or "facebook.com/share/g/" in lu:
        return "facebook_group"
    if "facebook.com" in lu or "fb.me" in lu:
        return "facebook"
    if "instagram.com" in lu:
        return "instagram"
    if "t.me" in lu or "telegram" in lu:
        return "telegram"
    if "discord" in lu:
        return "discord"
    if "whatsapp" in lu or "wa.me" in lu:
        return "whatsapp"
    if "signal.me" in lu or "signal.group" in lu:
        return "signal"
    return "other_social"


def clean_url(u: str) -> str:
    u = htmllib.unescape(u).replace("&amp;", "&").split("#")[0]
    u = re.sub(r"[),.;\"'\\]+$", "", u)
    if not u.startswith("http"):
        u = "https://" + u
    u = u.replace("://cs-cz.facebook.com", "://www.facebook.com").replace("://m.facebook.com", "://www.facebook.com")
    return u.rstrip("/")


_GENERIC_ENDINGS = ("facebook.com", "instagram.com", "t.me", "telegram.me",
                    "instagram.com/p", "instagram.com/reel", "instagram.com/explore", "instagram.com/accounts")


def find_hidden_links(html: str, page_url: str) -> list[dict]:
    found, seen = [], set()

    def add(u: str, source: str):
        u = clean_url(u)
        t = classify_link(u)
        if t == "facebook_share" or u.lower().endswith(_GENERIC_ENDINGS):
            return
        if "facebook.com/tr?" in u.lower() or "/plugins/" in u.lower() or "/dialog/" in u.lower():
            return
        k = u.lower()
        if k not in seen:
            seen.add(k)
            found.append({"url": u, "type": t, "source": source})

    for m in re.finditer(
        r'aria-label="(https?://[^"]*(?:facebook|t\.me|telegram|discord|whatsapp|wa\.me|signal)[^"]*)"', html, re.I):
        add(m.group(1), "aria_label_hidden")
    for m in SOCIAL_RE.finditer(html):
        add(m.group(0), "html_scan")
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1)
        if href.startswith("http") and SOCIAL_RE.search(href):
            add(href, "href")
    own = page_url.rstrip("/").lower()
    return [h for h in found if h["url"].lower() != own and not own.startswith(h["url"].lower())]


# ---------------------------------------------------------------------------
# web pages: latest post (ported from capture_units.extract_latest_post)
# ---------------------------------------------------------------------------
def _clean(t: str) -> str:
    return htmllib.unescape(re.sub(r"\s+", " ", t).strip())


def extract_latest_post_html(html: str, url: str) -> tuple[str | None, str | None, str | None, list[str]]:
    title = author = dt_out = None
    notes: list[str] = []
    candidates = []
    for block in re.findall(r"<article[^>]*>(.*?)</article>", html, re.I | re.S)[:12]:
        hm = re.search(r"<h[1-4][^>]*>\s*(?:<a[^>]*>)?\s*([^<]{5,220})", block, re.I)
        tm = re.search(r'<time[^>]*(?:datetime=["\']([^"\']+)["\'])?[^>]*>([^<]*)</time>', block, re.I)
        am = re.search(r'(?:field--name-uid|username|author|byline)[^>]*>.*?>([^<]{2,80})<', block, re.I | re.S)
        if hm:
            t = _clean(hm.group(1))
            if t.lower() in NAV_TITLES:
                continue
            d = (tm.group(1) or tm.group(2) or "").strip() if tm else None
            if not d:
                dm = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})", block)
                d = dm.group(1) if dm else None
            candidates.append((d or None, t, am.group(1).strip() if am else None, "article"))
    for block in re.findall(r'class="[^"]*views-row[^"]*"[^>]*>(.{0,3000}?)(?:class="[^"]*views-row|$)',
                            html, re.I | re.S)[:10]:
        hm = re.search(r"<h[2-4][^>]*>\s*(?:<a[^>]*>)?\s*([^<]{5,220})", block, re.I) or \
            re.search(r'<a[^>]+href="/[^"]+"[^>]*>\s*([^<]{8,220})\s*</a>', block, re.I)
        tm = re.search(r'<time[^>]*(?:datetime=["\']([^"\']+)["\'])?[^>]*>([^<]*)</time>', block, re.I)
        if tm:
            d = (tm.group(1) or tm.group(2) or "").strip() or None
        else:
            dm = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})", block)
            d = dm.group(1) if dm else None
        if hm:
            t = _clean(hm.group(1))
            if t.lower() not in NAV_TITLES:
                candidates.append((d, t, None, "views-row"))
    for m in re.finditer(r'(?:class="[^"]*topic[^"]*"|class="[^"]*topictitle[^"]*").{0,400}?<a[^>]+>([^<]{5,200})</a>',
                         html, re.I | re.S):
        t = _clean(m.group(1))
        if t.lower() not in NAV_TITLES:
            candidates.append((None, t, None, "forum_topic"))
            break
    for m in re.finditer(r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4}).{0,80}?(?:</[^>]+>\s*){0,6}<a[^>]+>\s*([^<]{8,220})\s*</a>",
                         html, re.I | re.S):
        candidates.append((m.group(1), _clean(m.group(2)), None, "dated_link"))
    for m in re.finditer(r'<a[^>]+>\s*([^<]{8,220})\s*</a>.{0,80}?(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})', html, re.I | re.S):
        t = _clean(m.group(1))
        if t.lower() not in NAV_TITLES and len(t) > 10:
            candidates.append((m.group(2), t, None, "link_dated"))
    best = None
    for c in candidates:
        if not c[1]:
            continue
        if c[0]:
            best = c
            break
        best = best or c
    if best:
        dt_out, title, author, src = best
        notes.append(f"post_source={src}")
    if not title:
        m = re.search(r"Aktualit[ya].{0,500}?href=\"[^\"]+\"[^>]*>\s*([^<]{8,220})", html, re.I | re.S)
        if m and _clean(m.group(1)).lower() not in NAV_TITLES:
            title = _clean(m.group(1))
            notes.append("post_source=aktuality_section")
    og = og_title(html)
    if og:
        notes.append(f"og:title={og[:120]}")
    return title, author, dt_out, notes


def og_title(html: str) -> str | None:
    og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
    return htmllib.unescape(og.group(1)) if og else None


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def detect_status(url: str, html: str, http_code: int | None, shot_ok: bool, logged_in: bool) -> str:
    low = (html or "").lower()
    host = urlparse(url).netloc.lower()
    if http_code in (404, 410):
        return "not_found"
    if "facebook.com" in host and is_facebook_group(url):
        if re.search(r"(soukromá skupina|private group)", low) and re.search(
                r"(přidat se ke skupině|join group|připojit se ke skupině)", low):
            return "private_group"
    if not logged_in:
        signals = 0
        if "facebook.com" in host and any(x in low for x in (
                "log into facebook", "log in to facebook", "přihlaste se k facebooku", "you must log in",
                "id=\"login_form\"", "přihlásit se k facebooku")):
            signals += 2
        if "instagram.com" in host and ("accounts/login" in low and "loginform" in low.replace("_", "")):
            signals += 2
        if host.endswith("x.com") or host.endswith("twitter.com"):
            if "before you can continue" in low or "i/flow/login" in low and "data-testid=\"tweet\"" not in low:
                signals += 2
        if signals >= 2:
            return "login_wall"
    if len(low) < 30000 and any(x in low for x in (
            "access denied", "request blocked", "cf-browser-verification", "just a moment",
            "attention required", "captcha")):
        return "blocked"
    if http_code == 403:
        return "blocked"
    if not shot_ok:
        return "error"
    return "ok"


# ---------------------------------------------------------------------------
# relative / Czech dates → ISO
# ---------------------------------------------------------------------------
def normalize_date(raw: str | None, now: datetime | None = None) -> str | None:
    """'14h' / '3 d' / 'Včera' / '5. října' / '12.9.2026' / ISO → 'YYYY-MM-DD'."""
    if not raw:
        return None
    now = now or datetime.now()
    today = now.date()
    s = raw.strip().lower()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})\.\s*(\d{1,2})\.?$", s)  # X: "2. 10."
    if m:
        try:
            d = date(today.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        if d > today:
            d = d.replace(year=today.year - 1)
        return d.isoformat()
    if s in ("právě teď", "just now", "now", "teď"):
        return today.isoformat()
    if s.startswith(("včera", "yesterday")):
        return (today - timedelta(days=1)).isoformat()
    m = re.match(r"^(\d+)\s*(s|sec|m|min|h|hod|hr|d|dn|day|days|w|t|týd|wk|y|r)\b", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit in ("s", "sec", "m", "min", "h", "hod", "hr"):
            secs = {"s": 1, "sec": 1, "m": 60, "min": 60}.get(unit, 3600) * n
            return (now - timedelta(seconds=secs)).date().isoformat()
        if unit in ("d", "dn", "day", "days"):
            return (today - timedelta(days=n)).isoformat()
        if unit in ("w", "t", "týd", "wk"):
            return (today - timedelta(weeks=n)).isoformat()
        return None
    m = re.match(r"^(\d{1,2})\.?\s+([a-zá-ž]+)(?:\s+(\d{4}))?", s) or None
    if m and m.group(2) in CZ_MONTHS:
        y = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(y, CZ_MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
        if not m.group(3) and d > today:
            d = d.replace(year=y - 1)
        return d.isoformat()
    m = re.match(r"^([a-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?", s)  # "Oct 3, 2026" / "Oct 3"
    if m:
        mon = next((v for k, v in CZ_MONTHS.items() if k.startswith(m.group(1)[:3])), None)
        if mon:
            y = int(m.group(3)) if m.group(3) else today.year
            try:
                d = date(y, mon, int(m.group(2)))
            except ValueError:
                return None
            if not m.group(3) and d > today:
                d = d.replace(year=y - 1)
            return d.isoformat()
    return None


# ---------------------------------------------------------------------------
# social DOM extractors (run inside the live page)
# ---------------------------------------------------------------------------
_JS_X = r"""
() => {
  // logged-in / classic markup
  const arts = [...document.querySelectorAll('article[data-testid="tweet"]')];
  for (const a of arts) {
    const ctx = (a.querySelector('[data-testid="socialContext"]')||{}).innerText || '';
    if (/pinned|připnut/i.test(ctx)) continue;
    const t = a.querySelector('time');
    const link = t ? t.closest('a') : null;
    const txt = (a.querySelector('[data-testid="tweetText"]')||{}).innerText || '';
    const author = (a.querySelector('[data-testid="User-Name"]')||{}).innerText || '';
    return {text: txt, datetime: t ? t.getAttribute('datetime') : null, raw: t ? t.innerText : null,
            url: link ? link.href : null, author: author.split('\n')[0] || null, reposted: /repost|sdílel/i.test(ctx)};
  }
  // anonymous (logged-out) markup, 2026: <article> without data-testid
  for (const a of document.querySelectorAll('article')) {
    const head = a.innerText.slice(0, 200);
    if (/pinned|připnut/i.test(head)) continue;
    let link = null;
    for (const l of a.querySelectorAll('a[href*="/status/"]')) {
      const tx = (l.innerText || '').trim();
      if (tx && tx.length <= 20) { link = l; break; }
    }
    if (!link) continue;
    const body = a.querySelector('div[dir="auto"]');
    const t = a.querySelector('time');
    const author = (a.querySelector('.font-bold') || {}).innerText || null;
    return {text: body ? body.innerText.replace(/Zobrazit více|Show more/g, '').trim() : '',
            datetime: t ? t.getAttribute('datetime') : null, raw: (link.innerText || '').trim(),
            url: link.href, author, reposted: /reposted|sdílel|repostoval/i.test(head)};
  }
  return null;
}
"""

_JS_FB = r"""
() => {
  const arts = [...document.querySelectorAll('div[role="article"]')].filter(a => !a.parentElement.closest('div[role="article"]'));
  for (const a of arts) {
    const msg = a.querySelector('[data-ad-preview="message"], [data-ad-comet-preview="message"], div[dir="auto"]');
    const text = msg ? msg.innerText : '';
    if (!text || text.length < 3) continue;
    const author = (a.querySelector('h2, h3, strong') || {}).innerText || null;
    let raw = null, url = null;
    for (const l of a.querySelectorAll('a[href*="/posts/"], a[href*="/videos/"], a[href*="story_fbid"], a[href*="/permalink/"], a[href*="/reel/"], a[href*="/photo"]')) {
      const t = (l.getAttribute('aria-label') || l.innerText || '').trim();
      if (t && t.length < 40) { raw = t; url = l.href; break; }
      if (!url) url = l.href;
    }
    return {text, author, raw, url};
  }
  return null;
}
"""

_JS_IG = r"""
() => {
  const l = document.querySelector('main a[href*="/p/"], main a[href*="/reel/"]');
  if (!l) return null;
  const img = l.querySelector('img');
  return {url: l.href, text: img ? (img.getAttribute('alt') || '') : '', author: null, raw: null};
}
"""

_JS_YT = r"""
() => {
  // 2026 "lockup view model" markup
  const lock = document.querySelector('yt-lockup-view-model');
  if (lock) {
    const h = lock.querySelector('h3[title]');
    const a = lock.querySelector('a[href*="/watch"], a[href*="/shorts/"]');
    const spans = [...lock.querySelectorAll('span')].map(s => (s.innerText || '').trim()).filter(Boolean);
    const raw = spans.find(t => /před|ago|streamed|vysíláno/i.test(t)) || null;
    return {text: h ? h.getAttribute('title') : (a ? a.innerText : ''), url: a ? a.href : null, raw, author: null};
  }
  const v = document.querySelector('ytd-rich-item-renderer #video-title, ytd-grid-video-renderer #video-title, #video-title');
  if (!v) return null;
  const meta = v.closest('ytd-rich-item-renderer, ytd-grid-video-renderer, ytd-video-renderer');
  const spans = meta ? [...meta.querySelectorAll('#metadata-line span, .inline-metadata-item')].map(s => s.innerText) : [];
  return {text: v.innerText || v.getAttribute('title'), url: v.href || (v.closest('a')||{}).href || null,
          raw: spans.length ? spans[spans.length - 1] : null, author: null};
}
"""

_JS_BY_SERVICE = {"x": _JS_X, "facebook": _JS_FB, "instagram": _JS_IG, "youtube": _JS_YT}


def _yt_relative(raw: str | None, today: date) -> str | None:
    """'před 5 l.' / 'před 3 dny' / '2 weeks ago' / 'Streamed 4 hours ago' → approx ISO date."""
    if not raw:
        return None
    s = raw.lower()
    m = re.search(r"(\d+)\s*(sekund|second|minut|min|hodin|hod|hour|h\b|dn|den|day|d\.|týd|week|t\.|měs|month|rok|let|year|r\.|l\.)", s)
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    if u.startswith(("sekund", "second", "minut", "min", "hodin", "hod", "hour", "h")):
        return today.isoformat()
    if u.startswith(("dn", "den", "day", "d.")):
        return (today - timedelta(days=n)).isoformat()
    if u.startswith(("týd", "week", "t.")):
        return (today - timedelta(weeks=n)).isoformat()
    if u.startswith(("měs", "month")):
        return (today - timedelta(days=30 * n)).isoformat()
    return (today - timedelta(days=365 * n)).isoformat()


def extract_social_post(page, svc: Service) -> tuple[dict, list[str]]:
    """Return (fields, notes). Fields use captures.jsonl names."""
    js = _JS_BY_SERVICE.get(svc.key)
    if not js:
        return {}, ["no_extractor_for_service"]
    try:
        data = page.evaluate(js)
    except Exception as ex:
        return {}, [f"extract_error={type(ex).__name__}"]
    if not data:
        return {}, ["no_post_found_in_dom"]
    now = datetime.now()
    today = now.date()
    text = re.sub(r"\s+", " ", (data.get("text") or "")).strip()
    title = (text[:200] + " …") if len(text) > 200 else (text or None)
    raw = data.get("raw")
    iso = None
    if data.get("datetime"):
        iso = data["datetime"][:10]
    elif svc.key == "youtube":
        iso = _yt_relative(raw, today)
    else:
        iso = normalize_date(raw, now)
    fields = {
        "last_post_title": title,
        "last_post_author": data.get("author"),
        "last_post_date": iso,
        "last_post_date_raw": raw,
        "last_post_url": data.get("url"),
    }
    if svc.key == "instagram":
        fields["last_post_caption"] = text or None
    notes = [f"post_source=dom_{svc.key}"]
    if data.get("reposted"):
        notes.append("latest_is_repost")
    return {k: v for k, v in fields.items() if v}, notes


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
