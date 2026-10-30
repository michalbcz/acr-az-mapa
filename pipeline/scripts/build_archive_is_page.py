#!/usr/bin/env python3
"""Build the manual archive.is helper page from the report's tracked links.

Run after build_site_v2.py.  The page deliberately does not submit anything
itself: every save action is opened in the user's own browser, where archive.ph
can present its current consent/captcha flow.
"""
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urldefrag, urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site_v2" / "archiv-is.html"
ARCHIVE_DATA = ROOT / "data" / "archive_status.json"
URLS_DATA = ROOT / "urls.json"
CAPTURES_DATA = ROOT / "data" / "captures.jsonl"
HISTORY_DATA = ROOT / "data" / "updates_history.jsonl"
COMMENTS_DATA = ROOT / "data" / "interesting_comments.jsonl"

CATEGORY_NAMES = {
    "official_webs": "Oficiální weby",
    "kvv": "Krajská vojenská velitelství",
    "unit_webs": "Jednotky a jejich weby",
    "other": "Ostatní organizace",
    "experts": "Odborná média a analytici",
    "social": "Sociální sítě",
}
CATEGORY_PRIORITY = {
    "Oficiální weby": 0,
    "Krajská vojenská velitelství": 0,
    "Jednotky a jejich weby": 0,
    "Ostatní organizace": 0,
    "Odborná média a analytici": 0,
    "Sociální sítě": 1,
    "Aktuality a články": 2,
}
SOCIAL_HOSTS = {
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "instagram.com", "www.instagram.com",
    "youtube.com", "www.youtube.com", "youtu.be",
    "x.com", "www.x.com", "twitter.com", "www.twitter.com",
    "linkedin.com", "www.linkedin.com",
    "tiktok.com", "www.tiktok.com",
}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            continue
    return rows


def is_social(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower().rstrip(".") in SOCIAL_HOSTS


def is_obvious_non_archivable(url: str) -> bool:
    """Remove crawler/admin plumbing and fragment variants, not real content."""
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.netloc or p.fragment:
        return True
    low = (p.path or "/").lower().rstrip("/")
    if low in {"/admin", "/login", "/wp-login.php", "/user/login", "/adm"}:
        return True
    if low.endswith("/login") or low.endswith("/wp-login.php") or low.endswith("/adm"):
        return True
    if low.endswith("/rss") or low.endswith("/rss2") or low.endswith(".rss"):
        return True
    if p.path.lower().endswith("/share.php"):
        return True
    return False


def clean_url(url: str) -> str:
    return urldefrag(url)[0]


def host_label(url: str) -> str:
    p = urlsplit(url)
    host = (p.hostname or p.netloc).lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path or "/"
    if path != "/":
        text = host + path
    else:
        text = host
    if p.query:
        text += "?" + p.query
    return text


def short_source(text: str, url: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text or host_label(url)


def archive_org(snapshot: object) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    url = snapshot.get("snapshot_url")
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def build_items() -> list[dict]:
    archive = json.loads(ARCHIVE_DATA.read_text(encoding="utf-8"))
    tracked = archive.get("links", {})
    urls = json.loads(URLS_DATA.read_text(encoding="utf-8"))

    base_category: dict[str, str] = {}
    for key, values in urls.items():
        category = CATEGORY_NAMES.get(key, key)
        for url in values if isinstance(values, list) else []:
            base_category[clean_url(url)] = category

    source_by_url: dict[str, list[str]] = defaultdict(list)
    kind_by_url: dict[str, set[str]] = defaultdict(set)
    for row in read_jsonl(HISTORY_DATA):
        source = row.get("source_label") or row.get("category") or ""
        kind = row.get("kind") or "news_item"
        candidates = list(row.get("urls") or [])
        if row.get("url"):
            candidates.append(row["url"])
        for url in candidates:
            if not isinstance(url, str):
                continue
            key = clean_url(url)
            if source and source not in source_by_url[key]:
                source_by_url[key].append(str(source))
            kind_by_url[key].add(str(kind))

    for row in read_jsonl(COMMENTS_DATA):
        for field in ("channel_url", "post_url", "comment_url"):
            url = row.get(field)
            if isinstance(url, str):
                key = clean_url(url)
                kind_by_url[key].add("comment")
                source = row.get("channel") or row.get("channel_url") or "Komentáře"
                if source not in source_by_url[key]:
                    source_by_url[key].append(str(source))

    # The archive checker is the authoritative list of links currently shown
    # on the report. Keep only real HTTP(S) targets and de-duplicate fragments.
    unique: dict[str, dict] = {}
    for raw_url, status in tracked.items():
        if not isinstance(raw_url, str) or is_obvious_non_archivable(raw_url):
            continue
        url = clean_url(raw_url)
        if not url or url in unique:
            continue
        unique[url] = status if isinstance(status, dict) else {}

    items = []
    for url, status in unique.items():
        key = clean_url(url)
        social = is_social(url)
        category = base_category.get(key)
        if category is None and social:
            category = "Sociální sítě"
        if category is None:
            category = "Aktuality a články"

        sources = source_by_url.get(key, [])
        if key in base_category:
            source = category
            priority = 0
        elif social:
            source = sources[0] if sources else "Sociální stránka"
            priority = 1
        else:
            source = sources[0] if sources else host_label(url)
            priority = 2

        # Mark video/comment URLs clearly without creating a fourth priority tier.
        if "comment" in kind_by_url.get(key, set()) and not social:
            source = sources[0] if sources else "Komentáře / videa"

        items.append({
            "url": url,
            "label": host_label(url),
            "source": short_source(source, url),
            "category": category,
            "priority": priority,
            "wayback": archive_org(status.get("wayback")),
        })

    items.sort(key=lambda x: (
        CATEGORY_PRIORITY.get(x["category"], x["priority"]),
        x["category"].lower(),
        x["source"].lower(),
        x["label"].lower(),
    ))
    return items


CSS = r"""
:root{--bg:#0b1017;--panel:#121a24;--card:#1a2332;--text:#e8eef6;--muted:#93a0b3;--accent:#4b9fff;--ok:#3dd68c;--warn:#f5a524;--border:#2a3545}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);line-height:1.45}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.wrap{max-width:1100px;margin:0 auto;padding:1rem 1.1rem 3rem}.top{position:sticky;top:0;z-index:5;background:rgba(11,16,23,.94);backdrop-filter:blur(8px);border-bottom:1px solid var(--border);padding:.75rem 0}.top h1{font-size:1.2rem;margin:0 0 .35rem}.sub,.muted{color:var(--muted);font-size:.88rem}.notice{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:.85rem 1rem;margin:1rem 0}.controls{display:flex;align-items:center;gap:.7rem;flex-wrap:wrap;margin:.8rem 0}.counter{font-weight:700}.button,.save,.check{display:inline-block;border:1px solid var(--border);border-radius:7px;padding:.38rem .6rem;color:var(--text);background:var(--card);font-size:.82rem;cursor:pointer}.button:hover,.check:hover{border-color:var(--accent);text-decoration:none}.save{background:#173653;border-color:#28639a}.save:hover{background:#1e466a;text-decoration:none}.next{background:#1d4938;border-color:#2c795b}.group{margin:1.5rem 0 2rem}.group h2{font-size:1.05rem;margin:0 0 .55rem}.group-count{color:var(--muted);font-size:.8rem;font-weight:400}.rows{display:grid;gap:.55rem}.row{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.65rem .7rem;display:grid;grid-template-columns:auto 1fr;gap:.55rem;align-items:start}.row.done{opacity:.52}.row.done .label{text-decoration:line-through}.row input{width:1.15rem;height:1.15rem;margin:.15rem 0;accent-color:var(--ok)}.main{min-width:0}.label{font-weight:650;overflow-wrap:anywhere}.source{font-size:.76rem;color:var(--accent);margin:.05rem 0}.url{font-size:.76rem;color:var(--muted);overflow-wrap:anywhere}.actions{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.45rem}.status{font-size:.73rem;color:var(--muted);margin-left:.25rem}.empty{color:var(--muted);padding:1rem 0}.hidden-row{display:none}.footer{border-top:1px solid var(--border);margin-top:2rem;padding-top:1rem;color:var(--muted);font-size:.78rem}@media(max-width:620px){.row{grid-template-columns:auto 1fr}.actions a,.actions button{font-size:.76rem;padding:.42rem .5rem}.wrap{padding-left:.75rem;padding-right:.75rem}}
"""


def js_safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render(items: list[dict]) -> str:
    generated = datetime.now().astimezone().strftime("%d. %m. %Y %H:%M %z")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        grouped[item["category"]].append(item)

    sections = []
    for category in sorted(grouped, key=lambda c: (CATEGORY_PRIORITY.get(c, 9), c.lower())):
        rows = []
        for index, item in enumerate(grouped[category]):
            encoded_url = quote(item["url"], safe="")
            rows.append(
                '<article class="row" data-index="%d" data-url="%s">'
                '<input type="checkbox" class="done" aria-label="hotovo: %s">'
                '<div class="main"><div class="label">%s</div>'
                '<div class="source">%s</div><div class="url">%s</div><div class="actions">'
                '<a class="save" target="_blank" rel="noopener" href="https://archive.ph/submit/?url=%s">Uložit na archive.is</a>'
                '<a class="check" target="_blank" rel="noopener" href="https://archive.ph/newest/%s">Zkontrolovat kopii</a>%s'
                '</div></div></article>' % (
                    index,
                    html.escape(item["url"], quote=True),
                    html.escape(item["label"], quote=True),
                    html.escape(item["label"]),
                    html.escape(item["source"]),
                    html.escape(item["url"]),
                    html.escape(encoded_url, quote=True),
                    html.escape(item["url"], quote=True),
                    (f'<a class="button" target="_blank" rel="noopener" href="{html.escape(item["wayback"], quote=True)}">archive.org</a>' if item.get("wayback") else ""),
                )
            )
        sections.append(
            '<section class="group" data-category="%s"><h2>%s <span class="group-count">(%d)</span></h2><div class="rows">%s</div></section>'
            % (html.escape(category, quote=True), html.escape(category), len(rows), "".join(rows))
        )

    data = js_safe_json(items)
    return f'''<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Archivace na archive.is</title><style>{CSS}</style></head><body>
<div class="wrap"><header class="top"><h1>Archivace na archive.is</h1><p class="sub">Pomocná stránka pro ruční ukládání odkazů</p>
<nav><a href="./">← Zpět na hlavní stránku</a></nav></header>
<main><div class="notice"><strong>Jak na to:</strong> Tento box nemůže archive.is kontaktovat. Klikni na <strong>„Uložit“</strong>, v nové záložce svého prohlížeče vyřeš případnou captchu a potvrď uložení stránky. Odkazy se zde pouze otevírají — nic se neodesílá automaticky.</div>
<div class="controls"><span class="counter" id="counter">Hotovo 0 / {len(items)}</span><label class="muted"><input id="hide-done" type="checkbox"> skrýt hotové</label><button class="button next" id="next" type="button">Otevřít dalších 5 nedokončených</button></div>
<p class="muted">Poznámka: prohlížeč může hromadné otevírání záložek zablokovat; v takovém případě povol vyskakovací okna pro tento web. Stav „hotovo“ se ukládá jen v localStorage tohoto prohlížeče.</p>
<div id="groups">{''.join(sections)}</div>
<footer class="footer">Vygenerováno: {html.escape(generated)} · {len(items)} odkazů · archive.org se zobrazuje jen tam, kde je známý poslední snímek.</footer></main></div>
<script type="application/json" id="link-data">{data}</script>
<script>
(() => {{
  const key = 'acr-az-archive-is-done-v1';
  const rows = [...document.querySelectorAll('.row')];
  const counter = document.getElementById('counter');
  const hide = document.getElementById('hide-done');
  const store = (() => {{ try {{ return JSON.parse(localStorage.getItem(key) || '{{}}'); }} catch (_) {{ return {{}}; }} }})();
  const save = () => {{ try {{ localStorage.setItem(key, JSON.stringify(store)); }} catch (_) {{}} }};
  const update = () => {{
    let done = 0;
    rows.forEach(row => {{
      const cb = row.querySelector('.done');
      cb.checked = !!store[row.dataset.url];
      row.classList.toggle('done', cb.checked);
      row.classList.toggle('hidden-row', hide.checked && cb.checked);
      if (cb.checked) done++;
    }});
    counter.textContent = `Hotovo ${{done}} / ${{rows.length}}`;
  }};
  rows.forEach(row => row.querySelector('.done').addEventListener('change', e => {{
    if (e.target.checked) store[row.dataset.url] = true; else delete store[row.dataset.url];
    save(); update();
  }}));
  hide.addEventListener('change', update);
  document.getElementById('next').addEventListener('click', () => {{
    const undone = rows.filter(row => !store[row.dataset.url]);
    undone.slice(0, 5).forEach(row => {{
      const link = row.querySelector('.save');
      const win = window.open(link.href, '_blank', 'noopener');
      if (!win) link.focus();
    }});
  }});
  update();
}})();
</script></body></html>'''


def main() -> None:
    items = build_items()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(items), encoding="utf-8")
    print(f"Wrote {OUT} ({len(items)} links)")


if __name__ == "__main__":
    main()
