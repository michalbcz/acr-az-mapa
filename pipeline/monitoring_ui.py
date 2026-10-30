"""Monitoring + archive UI helpers for build_site_v2.py (Czech UI).

Data (produced by scripts/check_sites_health.py, check_links.py, archive_lookup.py, archive_submit.py):
  data/health_history.jsonl   one row per website URL per day (HTTP + TLS)
  data/link_status.json       current outbound link check
  data/archive_status.json    latest archive.org / archive.today snapshots
All functions degrade gracefully when files are missing.
"""
import html as H
import json
from datetime import datetime, date
from pathlib import Path
from urllib.parse import quote

ARCHIVE_IS_NOTE = 'archive.today (archive.ph) – z automatu blokováno captchou, odkaz vede na nejnovější snímek (pokud existuje)'


def _esc(s):
    return H.escape(str(s if s is not None else ''), quote=True)


def _fmt_date(iso):
    if not iso:
        return '—'
    try:
        d = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        return f'{d.day}.{d.month}.{d.year}'
    except Exception:
        return str(iso)[:10]


def _exp_time(iso, mode='expiry'):
    """SSL expiry as <time class="reltime" data-rel=…>: server text = absolute date,
    client JS (refreshRelTimes in build_site_v2.py) shows 'za N dní' / N. Never a static relative."""
    try:
        d = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        txt = f'{d.day}. {d.month}. {d.year}'
    except Exception:
        txt = str(iso)[:10]
    ie = _esc(iso)
    return (f'<time class="reltime" datetime="{ie}" data-iso="{ie}" data-rel="{mode}" '
            f'title="platnost do {_esc(txt)}">{_esc(txt)}</time>')


def _fmt_ts14(ts):
    try:
        d = datetime.strptime(ts, '%Y%m%d%H%M%S')
        return f'{d.day}.{d.month}.{d.year}'
    except Exception:
        return ''


def _days_word(n):
    n = abs(int(n))
    if n == 1:
        return 'den'
    if 2 <= n <= 4:
        return 'dny'
    return 'dní'


class Monitoring:
    def __init__(self, root):
        root = Path(root)
        self.hist = []
        import mapa_db  # db/mapa_db.py (sys.path nastavuje build_site_v2)
        self.hist = mapa_db.health_history()
        self.by_url = {}
        for r in self.hist:
            self.by_url.setdefault(r.get('url'), []).append(r)
        for v in self.by_url.values():
            v.sort(key=lambda r: r.get('date') or '')
        try:
            _al = mapa_db.url_aliases()
            self.retired = {k for k in _al if not k.startswith('_')}
        except Exception:
            self.retired = set()
        self.days = sorted({r.get('date') for r in self.hist if r.get('date')})
        ls = {}
        if True:
            try:
                ls = mapa_db.link_status()
            except Exception:
                ls = {}
        self.link_meta = ls
        self.links = ls.get('links') or {}
        self.arch_meta = {}
        self.arch = {}
        if True:
            try:
                self.arch_meta = mapa_db.archive_status()
                self.arch = self.arch_meta.get('links') or {}
            except Exception:
                pass

    # ---------- per-URL helpers ----------
    def latest(self, url):
        v = self.by_url.get(url)
        return v[-1] if v else None

    def uptime(self, url):
        v = self.by_url.get(url) or []
        if not v:
            return None
        return round(100.0 * sum(1 for r in v if r.get('up')) / len(v), 1)

    def link_state(self, url):
        return (self.links.get(url) or {}).get('status')

    def is_broken(self, url):
        return self.link_state(url) == 'broken'

    def wayback_url(self, url):
        w = (self.arch.get(url) or {}).get('wayback') or {}
        return w.get('snapshot_url') or ('https://web.archive.org/web/*/' + url), w.get('timestamp')

    def archive_is_url(self, url):
        a = (self.arch.get(url) or {}).get('archive_is') or {}
        return a.get('snapshot_url') or ('https://archive.ph/newest/' + url)

    @staticmethod
    def exp_class(days):
        if days is None:
            return 'exp-na'
        if days <= 14:
            return 'exp-red'
        if days <= 30:
            return 'exp-orange'
        return 'exp-ok'

    # ---------- HTML fragments ----------
    def arch_links(self, url, in_anchor=False):
        """Small archive.org / archive.is links. in_anchor=True → spans (tiles are <a>)."""
        if not url or not url.startswith('http'):
            return ''
        wb, ts = self.wayback_url(url)
        ai = self.archive_is_url(url)
        wt = f'archive.org – snímek z {_fmt_ts14(ts)}' if ts else 'archive.org – přehled snímků (zatím bez snímku)'
        if in_anchor:
            return (f'<span class="arch"><span class="archl" role="link" tabindex="0" data-href="{_esc(wb)}" title="{_esc(wt)}">archive.org</span>'
                    f'<span class="archl" role="link" tabindex="0" data-href="{_esc(ai)}" title="{_esc(ARCHIVE_IS_NOTE)}">archive.is</span></span>')
        return (f'<span class="arch"><a href="{_esc(wb)}" target="_blank" rel="noopener" title="{_esc(wt)}">archive.org</a>'
                f'<a href="{_esc(ai)}" target="_blank" rel="noopener" title="{_esc(ARCHIVE_IS_NOTE)}">archive.is</a></span>')

    def broken_mark(self, url):
        if self.is_broken(url):
            r = self.links.get(url) or {}
            why = r.get('http_status') or (r.get('error') or '')[:60]
            return f'<span class="lbroken" title="Kontrola odkazů: {_esc(why)}">⚠ nedostupný</span>'
        return ''

    def link_cls(self, url):
        return ' class="lk-broken"' if self.is_broken(url) else ''

    def tile_html(self, url):
        """Health/SSL block for website tiles (inside <a>, so no nested links)."""
        r = self.latest(url)
        out = []
        if r:
            t = r.get('tls') or {}
            if r.get('up'):
                av = f'<span class="hb hb-up">● online{f" · {r.get("response_ms")} ms" if r.get("response_ms") is not None else ""}</span>'
            else:
                why = r.get('http_status') or ('TLS chyba' if 'TLS' in (r.get('error') or '') else 'chyba')
                av = f'<span class="hb hb-down">● nedostupné ({_esc(why)})</span>'
            up = self.uptime(url)
            upt = f'<span class="upt" title="dostupnost za {len(self.by_url.get(url) or [])} měření">uptime {up:g} %</span>' if up is not None else ''
            out.append(f'<div class="thealth">{av}{upt}</div>')
            if t.get('not_after'):
                d = t.get('days_to_expiry')
                iss = t.get('issuer_o') or t.get('issuer_cn') or '?'
                out.append(f'<div class="tssl">🔒 {_esc(iss)} · do {_fmt_date(t.get("not_after"))} · '
                           f'<span class="{self.exp_class(d)}">vyprší {_exp_time(t.get("not_after"))}</span></div>')
            elif t.get('error'):
                out.append(f'<div class="tssl"><span class="exp-red">🔓 SSL: {_esc("TLS chyba" if "SSL" in t["error"] or "EOF" in t["error"] else t["error"][:40])}</span></div>')
        return ''.join(out)

    def detail_html(self, url):
        r = self.latest(url)
        if not r:
            return ''
        t = r.get('tls') or {}
        up = self.uptime(url)
        d = t.get('days_to_expiry')
        av = ('<span class="hb hb-up">● online</span>' if r.get('up') else '<span class="hb hb-down">● nedostupné</span>')
        rows = [
            ('Dostupnost', f'{av} HTTP {_esc(r.get("http_status") or "—")} · {_esc(r.get("response_ms") if r.get("response_ms") is not None else "—")} ms · měřeno {_esc(_fmt_date(r.get("ts")))}'),
            ('Uptime', f'{up:g} % ({len(self.by_url.get(url) or [])} měření)' if up is not None else '—'),
            ('Cílová URL', _esc(r.get('final_url') or '—')),
            ('SSL vydavatel', _esc(' / '.join(x for x in (t.get('issuer_o'), t.get('issuer_cn')) if x) or '—')),
            ('SSL subjekt', _esc(f'{t.get("subject_cn") or "—"} · SAN: {t.get("san_count") if t.get("san_count") is not None else "—"}')),
            ('Platnost', f'{_fmt_date(t.get("not_before"))} – {_fmt_date(t.get("not_after"))}'
             + (f' · <span class="{self.exp_class(d)}">vyprší {_exp_time(t.get("not_after"))}</span>' if d is not None and t.get('not_after') else '')),
            ('TLS', _esc(t.get('version') or '—')),
        ]
        errs = [e for e in (r.get('error'), t.get('error'), t.get('verify_error')) if e]
        if errs:
            rows.append(('Chyby', f'<span class="exp-red">{_esc(" | ".join(errs)[:300])}</span>'))
        strip = self.strip_html(url)
        return ('<section><h3>Dostupnost a SSL</h3><dl>'
                + ''.join(f'<dt>{k}</dt><dd>{v}</dd>' for k, v in rows)
                + f'<dt>Historie</dt><dd>{strip}</dd></dl>'
                  '<p class="post-history"><a href="#cat-health">Přehled dostupnosti a SSL →</a></p></section>')

    def strip_html(self, url, n=30):
        v = {r.get('date'): r for r in (self.by_url.get(url) or [])}
        days = self.days[-n:]
        cells = []
        for dday in days:
            r = v.get(dday)
            if r is None:
                cells.append(f'<i class="st st-na" title="{_esc(dday)}: neměřeno"></i>')
            elif r.get('up'):
                cells.append(f'<i class="st st-up" title="{_esc(dday)}: online · HTTP {_esc(r.get("http_status"))} · {_esc(r.get("response_ms"))} ms"></i>')
            else:
                cells.append(f'<i class="st st-down" title="{_esc(dday)}: nedostupné · {_esc(r.get("http_status") or (r.get("error") or "")[:60])}"></i>')
        return f'<span class="strip">{"".join(cells)}</span>'

    def spark_svg(self, url, w=120, h=28):
        pts = [(r.get('date'), (r.get('tls') or {}).get('days_to_expiry')) for r in (self.by_url.get(url) or [])]
        pts = [(d, v) for d, v in pts if v is not None]
        if not pts:
            return '<span class="muted">—</span>'
        days = self.days or [p[0] for p in pts]
        idx = {d: i for i, d in enumerate(days)}
        n = max(1, len(days) - 1)
        vmax = max(100, max(v for _, v in pts))
        coords = []
        for d, v in pts:
            x = 4 + (w - 8) * (idx.get(d, 0) / n if len(days) > 1 else 0.5)
            y = 3 + (h - 6) * (1 - max(0, v) / vmax)
            coords.append((round(x, 1), round(y, 1), d, v))
        y30 = round(3 + (h - 6) * (1 - 30 / vmax), 1)
        path = ' '.join(f'{x},{y}' for x, y, *_ in coords)
        dots = ''.join(f'<circle cx="{x}" cy="{y}" r="2.2"><title>{_esc(d)}: {v} dní do expirace</title></circle>' for x, y, d, v in coords)
        return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="dny do expirace v čase">'
                f'<line x1="0" x2="{w}" y1="{y30}" y2="{y30}" class="sp30"><title>hranice 30 dní</title></line>'
                f'<polyline points="{path}" class="spl"/>{dots}</svg>')

    # ---------- section ----------
    def health_section(self, items_by_url=None):
        items_by_url = items_by_url or {}
        urls = [u for u in self.by_url.keys() if u not in self.retired]
        latest = {u: self.latest(u) for u in urls}
        rows = []
        for u in urls:
            r = latest[u]
            t = r.get('tls') or {}
            d = t.get('days_to_expiry')
            up = self.uptime(u)
            it = items_by_url.get(u)
            name = f'<a href="#{it["id"]}">{_esc(it["label"])}</a>' if it else _esc(u.split('://', 1)[-1][:60])
            av = (f'<span class="hb hb-up">● online</span>' if r.get('up') else '<span class="hb hb-down">● nedostupné</span>')
            ms = r.get('response_ms')
            if t.get('error') and not t.get('not_after'):
                ssl_cell = f'<span class="exp-red" title="{_esc(t.get("error"))}">{"TLS chyba" if "http" not in (t.get("error") or "") else "bez TLS (http)"}</span>'
            else:
                ssl_cell = _esc(t.get('issuer_o') or t.get('issuer_cn') or '—')
            dsort = d if d is not None else 99999
            rows.append(
                f'<tr><td class="hn">{name}<br><a class="hu" href="{_esc(u)}" target="_blank" rel="noopener">{_esc(u.split("://", 1)[-1][:70])}</a> {self.arch_links(u)}</td>'
                f'<td data-sort="{1 if r.get("up") else 0}">{av}<br><span class="muted">HTTP {_esc(r.get("http_status") or "—")}</span></td>'
                f'<td data-sort="{ms if ms is not None else 999999}">{_esc(ms if ms is not None else "—")}{" ms" if ms is not None else ""}</td>'
                f'<td data-sort="{up if up is not None else -1}">{f"{up:g} %" if up is not None else "—"}</td>'
                f'<td data-sort="{_esc(ssl_cell)}">{ssl_cell}</td>'
                f'<td data-sort="{_esc(t.get("not_after") or "9999")}">{_fmt_date(t.get("not_after"))}</td>'
                f'<td data-sort="{dsort}"><span class="{self.exp_class(d)}">{_exp_time(t.get("not_after"), "days") if (d is not None and t.get("not_after")) else "—"}</span></td>'
                f'<td>{_esc(t.get("version") or "—")}</td>'
                f'<td>{self.strip_html(u)}</td><td>{self.spark_svg(u)}</td></tr>'
            )
        # summary
        hosts = {}
        for u in urls:
            r = latest[u]
            if r and r.get('host'):
                hosts[r['host']] = r.get('tls') or {}
        issuers = {}
        for h, t in hosts.items():
            k = t.get('issuer_o') or t.get('issuer_cn') or ('TLS chyba' if t.get('error') else '—')
            if 'http' in (t.get('error') or '') and not t.get('not_after'):
                k = 'bez TLS (http)'
            issuers[k] = issuers.get(k, 0) + 1
        exp30 = sorted(((h, t['days_to_expiry']) for h, t in hosts.items() if t.get('days_to_expiry') is not None and t['days_to_expiry'] <= 30), key=lambda x: x[1])
        tls_err = sorted(h for h, t in hosts.items() if t.get('error') and 'http' not in t.get('error') and not t.get('not_after'))
        n_up = sum(1 for u in urls if latest[u].get('up'))
        last_day = self.days[-1] if self.days else ''
        # global uptime chart (histogram style)
        bars = []
        for dday in self.days[-60:]:
            rs = [r for r in self.hist if r.get('date') == dday]
            if not rs:
                continue
            pct = 100.0 * sum(1 for r in rs if r.get('up')) / len(rs)
            hpx = max(2, round(pct / 100 * 140))
            tip = f'{dday}: {pct:.1f} % online ({sum(1 for r in rs if r.get("up"))}/{len(rs)})'
            bars.append(f'<div class="hist-bar{" hb-bad" if pct < 95 else ""}" style="height:{hpx}px" tabindex="0" aria-label="{_esc(tip)}"><span class="tip">{_esc(tip)}</span><span class="lbl">{_esc(dday[5:])}</span></div>')
        chart = f'<div class="hist-wrap"><div class="hist hist-health" role="img" aria-label="Globální dostupnost">{"".join(bars)}</div></div>'
        exp_html = (', '.join(f'<span class="{self.exp_class(d)}">{_esc(h)} ({d} {_days_word(d)})</span>' for h, d in exp30) or 'žádný')
        iss_html = ' · '.join(f'{_esc(k)}: {v}' for k, v in sorted(issuers.items(), key=lambda x: -x[1]))
        summary = (
            '<div class="hsum">'
            f'<div class="hcard"><b>{n_up}/{len(urls)}</b><span>webů online ({_esc(last_day)})</span></div>'
            f'<div class="hcard"><b>{len(hosts)}</b><span>domén / certifikátů</span></div>'
            f'<div class="hcard"><b class="{"exp-orange" if exp30 else ""}">{len(exp30)}</b><span>cert. vyprší ≤ 30 dní</span></div>'
            f'<div class="hcard"><b class="{"exp-red" if tls_err else ""}">{len(tls_err)}</b><span>TLS chyby</span></div>'
            '</div>'
            f'<p class="muted"><b>Vydavatelé:</b> {iss_html}</p>'
            f'<p class="muted"><b>Expirace ≤ 30 dní:</b> {exp_html}</p>'
            + (f'<p class="muted"><b>TLS chyby (z měřicího serveru):</b> {", ".join(_esc(h) for h in tls_err)} – handshake selhává z boxu, nemusí jít o výpadek pro běžné návštěvníky.</p>' if tls_err else '')
        )
        table = (
            '<div class="htable-wrap"><table class="htable" id="healthTable"><thead><tr>'
            '<th data-k="0">Web</th><th data-k="1">Stav</th><th data-k="2">Odezva</th><th data-k="3">Uptime</th>'
            '<th data-k="4">SSL vydavatel</th><th data-k="5">Platnost do</th><th data-k="6" class="sorted-asc">Zbývá dní ▲</th>'
            '<th>TLS</th><th>Dostupnost (denně)</th><th>Dny do expirace v čase</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        )
        return (
            '<section class="catsec" id="cat-health" data-cat="health" hidden>'
            '<header class="cathead"><h2>Dostupnost a SSL</h2>'
            f'<p class="muted">Denní měření HTTP dostupnosti a TLS certifikátů webů (bez sociálních sítí) · {len(self.days)} {_days_word(len(self.days))} historie · '
            'barvy: <span class="exp-red">≤ 14 dní</span>, <span class="exp-orange">≤ 30 dní</span></p></header>'
            f'{summary}<h3 class="hh">Globální dostupnost v čase</h3>{chart}'
            f'<h3 class="hh">Weby (klikni na záhlaví pro řazení)</h3>{table}'
            f'{self.links_block()}</section>'
        )

    def links_block(self):
        if not self.links:
            return ''
        c = {}
        for r in self.links.values():
            c[r.get('status')] = c.get(r.get('status'), 0) + 1
        broken = sorted((r for r in self.links.values() if r.get('status') == 'broken'), key=lambda r: r['url'])
        unknown = sorted((r for r in self.links.values() if r.get('status') == 'unknown'), key=lambda r: r['url'])
        src_lab = {'page': 'stránka', 'feed': 'feed/historie', 'comments': 'komentáře'}

        def li(r):
            why = r.get('http_status') or (r.get('error') or '')[:90]
            srcs = ', '.join(src_lab.get(s, s) for s in r.get('sources') or [])
            return (f'<li><a class="lk-broken" href="{_esc(r["url"])}" target="_blank" rel="noopener">{_esc(r["url"])}</a> '
                    f'<span class="muted">{_esc(why)} · {_esc(srcs)}</span> {self.arch_links(r["url"])}</li>')
        am = self.arch_meta.get('summary') or {}
        sub = self.arch_meta.get('last_submit_run') or {}
        wb_sub = sub.get('wayback') or {}
        ai_sub = sub.get('archive_is') or {}
        arch_html = ''
        if am:
            arch_html = (
                f'<p class="muted"><b>Archivy:</b> archive.org má snímek u {am.get("wayback_archived", 0)} z {am.get("links", 0)} odkazů'
                + (f' (poslední dávka Save Page Now {_esc(_fmt_date(sub.get("at")))}: ' + ', '.join(f'{_esc(k)} {v}' for k, v in wb_sub.items()) + ')' if wb_sub else '')
                + '. archive.today (archive.ph) automatické vyhledání i uložení blokuje captchou/429 – odkazy „archive.is“ vedou na nejnovější snímek, pokud existuje'
                + (f' (pokus o uložení: ' + ', '.join(f'{_esc(k)} {v}' for k, v in ai_sub.items()) + ')' if ai_sub else '') + '.</p>'
            )
        return (
            '<h3 class="hh" id="kontrola-odkazu">Kontrola odkazů</h3>'
            f'<p class="muted">Všechny odchozí odkazy mapy (dlaždice, detaily, feed, historie, komentáře) · kontrola {_esc(_fmt_date(self.link_meta.get("checked_at")))}</p>'
            '<div class="hsum">'
            f'<div class="hcard"><b class="ok-t">{c.get("ok", 0)}</b><span>OK</span></div>'
            f'<div class="hcard"><b class="exp-red">{c.get("broken", 0)}</b><span>nefunkční</span></div>'
            f'<div class="hcard"><b class="exp-orange">{c.get("login_wall", 0)}</b><span>login wall / blokace botů</span></div>'
            f'<div class="hcard"><b>{c.get("unknown", 0)}</b><span>neověřeno (TLS/timeout)</span></div>'
            '</div>'
            + (f'<h4>Nefunkční odkazy</h4><ul class="blist">{"".join(li(r) for r in broken)}</ul>' if broken else '<p>Žádné nefunkční odkazy.</p>')
            + (f'<details><summary>Neověřené odkazy ({len(unknown)})</summary><ul class="blist">{"".join(li(r) for r in unknown)}</ul></details>' if unknown else '')
            + arch_html
        )

    def js_data(self):
        """Compact maps for client-side feed rendering."""
        st = {u: r.get('status') for u, r in self.links.items() if r.get('status') != 'ok'}
        wb = {}
        for u, v in self.arch.items():
            w = (v.get('wayback') or {})
            if w.get('timestamp'):
                wb[u] = w['timestamp']
        return st, wb


CSS = '''
.arch{display:inline-flex;gap:.35rem;margin-left:.35rem;font-size:.66rem;vertical-align:middle}
.arch a,.arch .archl{color:var(--muted);border:1px solid var(--border);border-radius:4px;padding:0 .3rem;line-height:1.5;cursor:pointer;text-decoration:none;white-space:nowrap}
.arch a:hover,.arch .archl:hover{color:var(--accent);border-color:var(--accent)}
.tarch{margin-top:.35rem}
.tarch .arch{margin-left:0}
.lk-broken,.lk-broken a{text-decoration:line-through;color:var(--bad) !important}
.lbroken{font-size:.7rem;color:var(--bad);font-weight:700;margin-left:.3rem;white-space:nowrap}
.thealth,.tssl{font-size:.7rem;color:var(--muted);margin-top:.25rem;display:flex;flex-wrap:wrap;gap:.2rem .5rem}
.hb{font-weight:600}.hb-up{color:var(--ok)}.hb-down{color:var(--bad)}
.upt{color:var(--muted)}
.exp-red{color:var(--bad);font-weight:700}.exp-orange{color:var(--wall);font-weight:700}.exp-ok{color:var(--ok)}.exp-na{color:var(--muted)}
.ok-t{color:var(--ok)}
.hsum{display:flex;flex-wrap:wrap;gap:.6rem;margin:.75rem 0}
.hcard{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.55rem .8rem;min-width:130px;display:flex;flex-direction:column}
.hcard b{font-size:1.25rem}.hcard span{font-size:.75rem;color:var(--muted)}
.hh{font-size:.85rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:1.2rem 0 .3rem}
.htable-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--border);border-radius:10px;margin-top:.5rem}
.htable{border-collapse:collapse;width:100%;font-size:.8rem;min-width:900px}
.htable th,.htable td{padding:.4rem .5rem;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
.htable th{background:var(--panel);position:sticky;top:0;cursor:pointer;white-space:nowrap;font-weight:600;color:var(--muted)}
.htable th:hover{color:var(--accent)}
.htable td.hn{max-width:280px;overflow-wrap:anywhere}
.htable .hu{font-size:.7rem;color:var(--muted)}
.strip{display:inline-flex;gap:2px;flex-wrap:wrap;max-width:260px}
.st{display:inline-block;width:8px;height:16px;border-radius:2px;background:#2a3545}
.st-up{background:var(--ok)}.st-down{background:var(--bad)}.st-na{background:#2a3545}
.spark .spl{fill:none;stroke:var(--accent);stroke-width:1.6}
.spark circle{fill:var(--accent)}
.spark .sp30{stroke:var(--wall);stroke-dasharray:3 3;stroke-width:1;opacity:.6}
.hist-health .hist-bar{background:linear-gradient(180deg,#3dd68c,#1f8a57)}
.hist-health .hist-bar.hb-bad{background:linear-gradient(180deg,#f07178,#a83e44)}
.blist{font-size:.8rem;padding-left:1.1rem}.blist li{margin:.3rem 0;overflow-wrap:anywhere}
#overview.mode-cats #cat-health,#overview.mode-chrono #cat-health,#overview.mode-feed #cat-health,#overview.mode-histogram #cat-health,#overview.mode-komentare #cat-health{display:none !important}
#overview.mode-health .catsec:not(#cat-health){display:none !important}
#overview.mode-health #cat-health{display:block !important}
#overview.mode-health .absence{display:none}
@media (max-width:720px){.hcard{min-width:calc(50% - .3rem);flex:1 1 calc(50% - .3rem)}.arch{font-size:.72rem}.arch a,.arch .archl{padding:.15rem .4rem}}
'''

JS = r'''
function archLinksHtml(u){
  if(!u||!/^https?:/.test(u)) return '';
  const ts=(window.ARCH_WB||{})[u];
  const wb=ts?('https://web.archive.org/web/'+ts+'/'+u):('https://web.archive.org/web/*/'+u);
  const ai='https://archive.ph/newest/'+u;
  return `<span class="arch"><a href="${esc(wb)}" target="_blank" rel="noopener" title="archive.org${ts?' – snímek '+ts.slice(6,8)+'.'+ts.slice(4,6)+'.'+ts.slice(0,4):''}">archive.org</a><a href="${esc(ai)}" target="_blank" rel="noopener" title="archive.today – nejnovější snímek">archive.is</a></span>`;
}
function brokenMarkHtml(u){ return ((window.LINK_STATE||{})[u]==='broken')?'<span class="lbroken">⚠ nedostupný</span>':''; }
document.addEventListener('click',e=>{
  const a=e.target.closest('.archl[data-href]');
  if(a){ e.preventDefault(); e.stopPropagation(); window.open(a.dataset.href,'_blank','noopener'); }
},true);
document.addEventListener('keydown',e=>{
  const a=e.target.closest&&e.target.closest('.archl[data-href]');
  if(a&&(e.key==='Enter'||e.key===' ')){ e.preventDefault(); e.stopPropagation(); window.open(a.dataset.href,'_blank','noopener'); }
},true);
(function healthSort(){
  const tb=document.getElementById('healthTable'); if(!tb) return;
  const ths=[...tb.querySelectorAll('th[data-k]')];
  const body=tb.tBodies[0];
  function val(tr,k){ const td=tr.cells[k]; const v=td.dataset.sort!==undefined?td.dataset.sort:td.textContent.trim(); const n=parseFloat(v); return isNaN(n)?v.toLowerCase():n; }
  function sortBy(k,dir){
    const rows=[...body.rows];
    rows.sort((a,b)=>{ const x=val(a,k),y=val(b,k); return (x>y?1:x<y?-1:0)*dir; });
    rows.forEach(r=>body.appendChild(r));
    ths.forEach(t=>{ t.textContent=t.textContent.replace(/ [▲▼]$/,''); t.classList.remove('sorted-asc','sorted-desc'); });
    const th=ths.find(t=>+t.dataset.k===k); th.textContent+=dir>0?' ▲':' ▼'; th.classList.add(dir>0?'sorted-asc':'sorted-desc');
  }
  ths.forEach(th=>th.addEventListener('click',()=>{ const k=+th.dataset.k; sortBy(k, th.classList.contains('sorted-asc')?-1:1); }));
  sortBy(6,1);
})();
'''
