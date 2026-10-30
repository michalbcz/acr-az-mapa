"""Události (events) – UI section #cat-events, 'Nejbližší akce' strip, ICS feed + per-event ICS.

Data: data/events.json (maintained by scripts/update_events.py from data/events_inbox/*.json).
Used by build_site_v2.py:  EV = events_ui.build(ROOT, OUT, items)  → EV.NAV, EV.VIEWBTN, EV.STRIP,
EV.SECTION, EV.CSS, EV.JS; writes OUT/events.ics, OUT/events/<id>.ics, OUT/events.json.
All times Europe/Prague. All-day events store an INCLUSIVE end date; ICS/Google use exclusive end.
"""
import datetime as dt
import html as H
import json
import urllib.parse as up
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Prague')
SITE = 'https://mapa-acr-az.vercel.app'
HOST = 'mapa-acr-az.vercel.app'
CAL_NAME = 'AČR / AZ – události'
TYPES = ['přijímač/základní příprava', 'výběrové řízení/nábor', 'cvičení', 'veřejná akce', 'jiné']
TYPE_CLASS = {'přijímač/základní příprava': 'kzp', 'výběrové řízení/nábor': 'nabor', 'cvičení': 'cviceni',
              'veřejná akce': 'verejna', 'jiné': 'jine'}
TYPE_ICON = {'přijímač/základní příprava': '🎖️', 'výběrové řízení/nábor': '📝', 'cvičení': '🪖',
             'veřejná akce': '🎪', 'jiné': '📌'}
MONTHS = ['leden', 'únor', 'březen', 'duben', 'květen', 'červen', 'červenec', 'srpen', 'září', 'říjen',
          'listopad', 'prosinec']
MONTHS_GEN_SHORT = ['led', 'úno', 'bře', 'dub', 'kvě', 'čvn', 'čvc', 'srp', 'zář', 'říj', 'lis', 'pro']
WDAYS = ['po', 'út', 'st', 'čt', 'pá', 'so', 'ne']

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Prague
X-LIC-LOCATION:Europe/Prague
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


# ------------------------------------------------------------------ helpers
def _d(s):
    return dt.date.fromisoformat(s[:10])


def _dt(s):
    return dt.datetime.strptime(s, '%Y-%m-%dT%H:%M').replace(tzinfo=TZ)


def ev_start(e):
    return dt.datetime.combine(_d(e['start']), dt.time(0), TZ) if e['all_day'] else _dt(e['start'])


def ev_end(e):
    """Exclusive end as aware datetime (for status)."""
    if e['all_day']:
        return dt.datetime.combine(_d(e.get('end') or e['start']) + dt.timedelta(days=1), dt.time(0), TZ)
    if e.get('end'):
        return _dt(e['end'])
    return _dt(e['start']) + dt.timedelta(hours=3)  # unknown end: treat as over ~3 h after start


def source_domain(u):
    try:
        h = up.urlparse(u).hostname or ''
        return h[4:] if h.startswith('www.') else h
    except Exception:
        return u


def fmt_day(d):
    return f'{d.day}. {d.month}. {d.year}'


def human_when(e):
    """Czech human-readable date/time range."""
    s = _d(e['start'])
    end = _d(e['end']) if e.get('end') else None
    wd = WDAYS[s.weekday()]
    if e.get('date_precision') == 'month':
        en = end or s
        a = f'{MONTHS[s.month - 1]}'
        b = f'{MONTHS[en.month - 1]} {en.year}'
        return (f'{a} – {b}' if (s.month, s.year) != (en.month, en.year) else b) + ' (přesné dny neuvedeny)'
    if e['all_day']:
        if not end or end == s:
            return f'{wd} {fmt_day(s)}'
        if s.year == end.year:
            return f'{s.day}. {s.month}. – {fmt_day(end)}'
        return f'{fmt_day(s)} – {fmt_day(end)}'
    t1 = e['start'][11:16]
    if not e.get('end'):
        return f'{wd} {fmt_day(s)}, {t1.lstrip("0") if t1[0] == "0" else t1}'
    t2 = e['end'][11:16]
    if end == s or (end == s + dt.timedelta(days=1) and t2 == '00:00'):
        t2 = '24:00' if t2 == '00:00' and end != s else t2
        return f'{wd} {fmt_day(s)}, {_hm(t1)}–{_hm(t2)}'
    return f'{fmt_day(s)} {_hm(t1)} – {fmt_day(end)} {_hm(t2)}'


def _hm(t):
    return t[1:] if t.startswith('0') else t


# ------------------------------------------------------------------ ICS
def _esc(s):
    return (s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r', '').replace('\n', '\\n')


def _fold(line):
    b = line.encode('utf-8')
    if len(b) <= 75:
        return line
    out, cur, n = [], '', 0
    for ch in line:
        cb = len(ch.encode('utf-8'))
        lim = 75 if not out else 74
        if n + cb > lim:
            out.append(cur)
            cur, n = ch, cb
        else:
            cur += ch
            n += cb
    out.append(cur)
    return '\r\n '.join(out)


def ev_description(e):
    parts = [e.get('description') or '']
    if e.get('organizer'):
        parts.append(f'Pořadatel: {e["organizer"]}')
    if e.get('confidence') == 'orientačně':
        parts.append('Termín je orientační – ověřte ve zdroji.')
    parts.append(f'Zdroj: {e["source_url"]}')
    parts.append(f'Přehled: {SITE}/#cat-events')
    return '\n'.join(p for p in parts if p)


def vevent(e):
    stamp_d = _d(e.get('updated') or e.get('first_seen') or e['start'])
    stamp = stamp_d.strftime('%Y%m%dT000000Z')
    L = ['BEGIN:VEVENT', f'UID:{e["id"]}@{HOST}', f'DTSTAMP:{stamp}', f'LAST-MODIFIED:{stamp}']
    if e['all_day']:
        s = _d(e['start'])
        en = _d(e.get('end') or e['start']) + dt.timedelta(days=1)
        L += [f'DTSTART;VALUE=DATE:{s:%Y%m%d}', f'DTEND;VALUE=DATE:{en:%Y%m%d}']
    else:
        L.append(f'DTSTART;TZID=Europe/Prague:{_dt(e["start"]):%Y%m%dT%H%M%S}')
        if e.get('end'):
            L.append(f'DTEND;TZID=Europe/Prague:{_dt(e["end"]):%Y%m%dT%H%M%S}')
    L += [f'SUMMARY:{_esc(e["title"])}',
          f'DESCRIPTION:{_esc(ev_description(e))}',
          f'URL:{e["source_url"]}',
          f'CATEGORIES:{_esc(e["type"])}',
          'STATUS:' + ('CONFIRMED' if e.get('confidence') == 'potvrzeno' else 'TENTATIVE'),
          'TRANSP:TRANSPARENT']
    if e.get('location'):
        L.append(f'LOCATION:{_esc(e["location"])}')
    if e.get('organizer'):
        L.append(f'X-ACR-ORGANIZER:{_esc(e["organizer"])}')
    L.append('END:VEVENT')
    return L


def calendar(events, name=CAL_NAME):
    L = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:-//{HOST}//AČR AZ mapa – události//CS', 'CALSCALE:GREGORIAN',
         'METHOD:PUBLISH', f'X-WR-CALNAME:{_esc(name)}', 'X-WR-TIMEZONE:Europe/Prague',
         f'X-WR-CALDESC:{_esc("Akce Armády ČR a Aktivní zálohy z oficiálních zdrojů (nábor, základní příprava, cvičení, veřejné akce). " + SITE)}',
         'REFRESH-INTERVAL;VALUE=DURATION:PT12H', 'X-PUBLISHED-TTL:PT12H']
    L += VTIMEZONE.split('\n')
    for e in events:
        L += vevent(e)
    L.append('END:VCALENDAR')
    return '\r\n'.join(_fold(x) for x in L) + '\r\n'


# ------------------------------------------------------------------ links
def gcal_link(e):
    if e['all_day']:
        s = _d(e['start'])
        en = _d(e.get('end') or e['start']) + dt.timedelta(days=1)
        dates = f'{s:%Y%m%d}/{en:%Y%m%d}'
    else:
        s = _dt(e['start'])
        en = _dt(e['end']) if e.get('end') else s
        dates = f'{s:%Y%m%dT%H%M%S}/{en:%Y%m%dT%H%M%S}'
    q = [('action', 'TEMPLATE'), ('text', e['title']), ('dates', dates), ('details', ev_description(e)),
         ('location', e.get('location') or ''), ('ctz', 'Europe/Prague')]
    return 'https://calendar.google.com/calendar/render?' + up.urlencode(q, safe='/', quote_via=up.quote)


def subscribe_links():
    https = f'{SITE}/events.ics'
    webcal = f'webcal://{HOST}/events.ics'
    return {
        'https': https,
        'webcal': webcal,
        'google': 'https://calendar.google.com/calendar/r?cid=' + up.quote(webcal, safe=''),
        'outlook': 'https://outlook.live.com/calendar/0/addfromweb?' + up.urlencode(
            [('url', https), ('name', CAL_NAME)], quote_via=up.quote),
    }


# ------------------------------------------------------------------ HTML
def _norm_url(u):
    return (u or '').strip().rstrip('/').lower().replace('://www.', '://')


class Events:
    pass


def card(e, now, url_to_id, past=False):
    t = e['type']
    s = _d(e['start'])
    tcls = TYPE_CLASS.get(t, 'jine')
    conf = e.get('confidence')
    conf_html = ('<span class="evbadge ev-ok" title="Datum převzato přímo ze zdroje">potvrzeno</span>' if conf == 'potvrzeno'
                 else '<span class="evbadge ev-approx" title="Zdroj uvádí jen přibližný termín (např. měsíc)">orientačně</span>')
    running = ev_start(e) <= now < ev_end(e)
    run_html = '<span class="evbadge ev-run">právě probíhá</span>' if running and not past else ''
    meta = []
    if e.get('location'):
        meta.append(f'<span class="evmeta-i">📍 {H.escape(e["location"])}</span>')
    if e.get('organizer'):
        meta.append(f'<span class="evmeta-i">🪖 {H.escape(e["organizer"])}</span>')
    ch = ''
    tid = url_to_id.get(_norm_url(e.get('channel')))
    if tid:
        ch = f' · <a href="#{H.escape(tid, quote=True)}" class="evch">kanál na mapě →</a>'
    if e.get('date_precision') == 'month':
        en = _d(e.get('end') or e['start'])
        mm = MONTHS_GEN_SHORT[s.month - 1] + ('' if (en.year, en.month) == (s.year, s.month) else '–' + MONTHS_GEN_SHORT[en.month - 1])
        dayblock = f'<div class="evdate evdate-m" title="Jen měsíc"><span class="evd">~</span><span class="evm">{mm}</span><span class="evw">{s.year}</span></div>'
    else:
        dayblock = (f'<div class="evdate"><span class="evd">{s.day}.</span><span class="evm">{MONTHS_GEN_SHORT[s.month - 1]}</span>'
                    f'<span class="evw">{WDAYS[s.weekday()]}</span></div>')
    src = e['source_url']
    return (
        f'<article class="evcard ev-{tcls}{" ev-past" if past else ""}" id="{H.escape(e["id"], quote=True)}" '
        f'data-type="{H.escape(t, quote=True)}" data-end="{ev_end(e).isoformat()}" data-start="{ev_start(e).isoformat()}">'
        f'{dayblock}<div class="evbody">'
        f'<div class="evtop"><span class="evchip evc-{tcls}">{TYPE_ICON.get(t, "")} {H.escape(t)}</span>{conf_html}{run_html}</div>'
        f'<h3 class="evtitle">{H.escape(e["title"])}</h3>'
        f'<div class="evwhen">🗓️ {H.escape(human_when(e))}</div>'
        f'<div class="evmeta">{"".join(meta)}</div>'
        + (f'<p class="evdesc">{H.escape(e["description"])}</p>' if e.get('description') else '')
        + f'<div class="evfoot"><a class="evsrc" href="{H.escape(src, quote=True)}" target="_blank" rel="noopener">zdroj: {H.escape(source_domain(src))} ↗</a>{ch}</div>'
        + ('' if past else
           f'<div class="evbtns"><a class="evbtn evbtn-g" href="{H.escape(gcal_link(e), quote=True)}" target="_blank" rel="noopener">＋ Google Kalendář</a>'
           f'<a class="evbtn" href="events/{H.escape(e["id"], quote=True)}.ics" download>⬇ .ics</a></div>')
        + '</div></article>'
    )


def build(root, out, items=None, now=None):
    root, out = Path(root), Path(out)
    import mapa_db  # db/mapa_db.py
    events = mapa_db.events()
    now = now or dt.datetime.now(TZ)
    url_to_id = {}
    for it in items or []:
        if it.get('url') and it.get('id'):
            url_to_id.setdefault(_norm_url(it['url']), it['id'])

    # files
    (out / 'events').mkdir(parents=True, exist_ok=True)
    (out / 'events.ics').write_text(calendar(events), encoding='utf-8', newline='')
    for e in events:
        (out / 'events' / f'{e["id"]}.ics').write_text(calendar([e], name=e['title']), encoding='utf-8', newline='')
    (out / 'events.json').write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding='utf-8')

    upcoming = sorted([e for e in events if ev_end(e) > now], key=lambda e: (ev_start(e), e['title']))
    past = sorted([e for e in events if ev_end(e) <= now], key=lambda e: ev_start(e), reverse=True)
    running = sorted([e for e in upcoming if ev_start(e) <= now],
                     key=lambda e: (e.get('date_precision') == 'month', ev_end(e), e['title']))
    future = [e for e in upcoming if ev_start(e) > now]
    S = Events()
    S.upcoming, S.past, S.events = upcoming, past, events
    n_up = len(upcoming)
    S.NAV = (f'<button type="button" class="navbtn navbtn-events" data-cat="events">📅 Události '
             f'<span class="n">{n_up}</span></button>')
    S.VIEWBTN = '<button type="button" class="viewbtn" id="viewEvents" data-view="events">📅 Události</button>'

    # strip: next 3 not-yet-started (prefer concrete dates), fallback running
    pick = [e for e in future if e.get('date_precision') != 'month'][:3] or running[:3]
    if pick:
        lis = ''.join(
            f'<li><a href="#{H.escape(e["id"], quote=True)}" data-ev-jump="{H.escape(e["id"], quote=True)}">'
            f'<span class="nx-date">{H.escape(human_when(e))}</span> '
            f'<span class="evchip evc-{TYPE_CLASS.get(e["type"], "jine")}">{TYPE_ICON.get(e["type"], "")}</span> '
            f'<span class="nx-title">{H.escape(e["title"])}</span></a></li>' for e in pick)
        S.STRIP = (f'<aside class="evstrip" id="evStrip" aria-label="Nejbližší akce"><div class="evstrip-h">'
                   f'<span class="evstrip-k">📅 Nejbližší akce</span>'
                   f'<a href="#cat-events" class="evstrip-all" data-ev-jump="">všechny události ({n_up}) →</a></div>'
                   f'<ul>{lis}</ul></aside>')
    else:
        S.STRIP = ''

    # section
    L = subscribe_links()
    counts = {t: sum(1 for e in upcoming if e['type'] == t) for t in TYPES}
    chips = [f'<button type="button" class="fchip evf active" data-evtype="">Vše <span class="n">{n_up}</span></button>']
    for t in TYPES:
        if counts[t]:
            chips.append(f'<button type="button" class="fchip evf" data-evtype="{H.escape(t, quote=True)}">'
                         f'{TYPE_ICON[t]} {H.escape(t)} <span class="n">{counts[t]}</span></button>')
    groups = []
    if running:
        groups.append(('Právě probíhá', 'running', running))
    by_month = {}
    for e in future:
        s = _d(e['start'])
        by_month.setdefault((s.year, s.month), []).append(e)
    for (y, m), evs in sorted(by_month.items()):
        groups.append((f'{MONTHS[m - 1]} {y}', f'{y}-{m:02d}', evs))
    body = ''.join(
        f'<div class="evgroup" data-g="{g}"><h3 class="evmonth">{H.escape(label.capitalize())} '
        f'<span class="n">{len(evs)}</span></h3><div class="evlist">'
        + ''.join(card(e, now, url_to_id) for e in evs) + '</div></div>'
        for label, g, evs in groups)
    empty = ('<p class="evempty" id="evEmpty"' + ('' if not upcoming else ' hidden') +
             '>Zatím nemáme žádné nadcházející události z ověřených zdrojů. Nové akce doplňujeme průběžně – '
             'odebírejte kalendář a budou se vám objevovat samy.</p>')
    past_html = ''
    if past:
        past_html = (f'<details class="evpast" id="evPast"><summary>Proběhlé události <span class="n">{len(past)}</span></summary>'
                     '<div class="evlist">' + ''.join(card(e, now, url_to_id, past=True) for e in past) + '</div></details>')
    upd = max([e.get('updated') or '' for e in events] or [''])
    upd_txt = f' · data aktualizována {fmt_day(_d(upd))}' if upd else ''
    S.SECTION = (
        '<section class="evsec" id="cat-events" aria-label="Události" hidden>'
        '<header class="evhead"><h2>📅 Události</h2>'
        f'<p class="muted">Nadcházející akce Armády ČR a Aktivní zálohy – základní příprava (přijímač), nábor a dny otevřených dveří, '
        f'cvičení a veřejné akce. Jen z ověřitelných zdrojů, každá akce má odkaz na zdroj. Časy jsou v SEČ/SELČ (Praha){upd_txt}.</p>'
        '<p><button type="button" class="back" data-ev-back>← Zpět na přehled</button></p></header>'
        '<div class="evsub" id="evSubscribe"><span class="evsub-k">Odebírat kalendář:</span>'
        f'<a class="evbtn evbtn-g" href="{H.escape(L["google"], quote=True)}" target="_blank" rel="noopener">Google Kalendář</a>'
        f'<a class="evbtn" href="{H.escape(L["webcal"], quote=True)}">Apple / Outlook (webcal)</a>'
        f'<a class="evbtn" href="{H.escape(L["outlook"], quote=True)}" target="_blank" rel="noopener">Outlook.com</a>'
        f'<a class="evbtn" href="events.ics" download>⬇ events.ics</a>'
        f'<span class="evsub-url"><code id="evIcsUrl">{H.escape(L["https"])}</code>'
        '<button type="button" class="evcopy" id="evCopy" title="Zkopírovat adresu kalendáře">Kopírovat</button></span>'
        '<span class="evsub-note muted">Odebíraný kalendář se aktualizuje sám (Google obvykle do 24 h). Když odkaz pro Google nahlásí chybu, přidejte adresu ručně: Google Kalendář → Nastavení → Přidat kalendář → Z adresy URL.</span></div>'
        f'<div class="fchips evfilters" id="evFilters">{"".join(chips)}</div>'
        f'<div id="evUpcoming">{body}</div>{empty}'
        '<p class="evempty" id="evFilterEmpty" hidden>Pro zvolený typ teď nejsou žádné nadcházející akce.</p>'
        f'{past_html}</section>'
    )
    S.CSS = CSS
    S.JS = JS
    return S


CSS = r"""
.evsec{margin:1.2rem 0 2rem}
.evsec[hidden],.evstrip[hidden],.evcard[hidden],.evgroup[hidden],.evempty[hidden],.evstrip li[hidden]{display:none !important}
.evhead h2{margin:.2rem 0 .3rem}
.evhead .muted{max-width:60rem;font-size:.86rem;line-height:1.45}
.evsub{display:flex;flex-wrap:wrap;gap:.4rem .5rem;align-items:center;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:.6rem .75rem;margin:.6rem 0 .8rem}
.evsub-k{font-size:.78rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.evsub-url{display:inline-flex;gap:.35rem;align-items:center;flex-wrap:wrap;max-width:100%}
.evsub-url code{font-size:.72rem;color:var(--muted);word-break:break-all}
.evsub-note{flex-basis:100%;font-size:.72rem}
.evbtn,.evcopy{display:inline-flex;align-items:center;gap:.25rem;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:.3rem .6rem;font-size:.78rem;text-decoration:none;cursor:pointer;white-space:nowrap}
.evbtn:hover,.evcopy:hover{border-color:var(--accent);color:var(--text)}
.evbtn-g{border-color:#2f4a6b;background:#132238}
.evfilters{margin:.2rem 0 .6rem}
.evf.active{border-color:var(--accent);background:#132238}
.evmonth{font-size:.95rem;margin:1.1rem 0 .5rem;color:var(--text);border-bottom:1px solid var(--border);padding-bottom:.25rem}
.evmonth .n,.evpast .n,.evfilters .n{color:var(--muted);font-weight:400;font-size:.8em}
.evgroup[data-g="running"] .evmonth{color:#3dd68c}
.evlist{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:.6rem}
.evcard{display:flex;gap:.7rem;background:var(--panel);border:1px solid var(--border);border-left:4px solid var(--border);border-radius:12px;padding:.65rem .75rem;scroll-margin-top:5rem}
.evcard:target,.evcard.ev-flash{box-shadow:0 0 0 2px var(--accent)}
.evcard.ev-kzp{border-left-color:#b48cff}.evcard.ev-nabor{border-left-color:#4b9fff}.evcard.ev-cviceni{border-left-color:#f5a524}.evcard.ev-verejna{border-left-color:#3dd68c}.evcard.ev-jine{border-left-color:#93a0b3}
.evcard.ev-past{opacity:.75}
.evdate{flex:0 0 3.3rem;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;background:var(--card);border-radius:8px;padding:.35rem .2rem;height:max-content}
.evd{font-size:1.25rem;font-weight:750;line-height:1}
.evm{font-size:.72rem;text-transform:uppercase;color:var(--muted);margin-top:.15rem}
.evw{font-size:.66rem;color:var(--muted)}
.evbody{flex:1;min-width:0}
.evtop{display:flex;flex-wrap:wrap;gap:.3rem;align-items:center}
.evchip{display:inline-block;font-size:.68rem;padding:.1rem .45rem;border-radius:999px;border:1px solid var(--border);background:var(--card);white-space:nowrap}
.evc-kzp{color:#cdb4ff;border-color:#4a3a73}.evc-nabor{color:#8cc4ff;border-color:#2f4a6b}.evc-cviceni{color:#ffc768;border-color:#6b4f1f}.evc-verejna{color:#7ee7b0;border-color:#1f5a3d}.evc-jine{color:#c3cbd8}
.evbadge{font-size:.64rem;font-weight:700;padding:.08rem .4rem;border-radius:999px;border:1px solid var(--border)}
.ev-ok{background:#143528;color:#3dd68c;border-color:#1f5a3d}
.ev-approx{background:#33280f;color:#f5c26b;border-color:#6b4f1f}
.ev-run{background:#132238;color:#8cc4ff;border-color:#2f4a6b}
.evtitle{font-size:.98rem;margin:.3rem 0 .15rem;line-height:1.3}
.evwhen{font-size:.84rem;font-weight:600}
.evmeta{display:flex;flex-direction:column;gap:.1rem;font-size:.78rem;color:var(--muted);margin-top:.2rem}
.evdesc{font-size:.8rem;line-height:1.45;margin:.35rem 0 .2rem;color:#c9d3e0}
.evfoot{font-size:.74rem;color:var(--muted);margin-top:.2rem}
.evsrc{color:var(--muted)}.evsrc:hover{color:var(--accent)}
.evbtns{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.45rem}
.evempty{background:var(--panel);border:1px dashed var(--border);border-radius:12px;padding:.9rem;color:var(--muted)}
.evpast{margin-top:1.4rem}
.evpast>summary{cursor:pointer;font-weight:650;padding:.4rem 0}
.evpast .evlist{margin-top:.5rem}
.evstrip{margin:.6rem 0 .4rem;background:var(--panel);border:1px solid var(--border);border-left:4px solid #3dd68c;border-radius:12px;padding:.5rem .8rem}
.evstrip-h{display:flex;flex-wrap:wrap;justify-content:space-between;gap:.3rem;align-items:baseline}
.evstrip-k{font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#3dd68c}
.evstrip-all{font-size:.78rem}
.evstrip ul{list-style:none;margin:.35rem 0 0;padding:0;display:flex;flex-direction:column;gap:.25rem}
.evstrip li a{color:var(--text);font-size:.86rem;display:flex;gap:.4rem;align-items:baseline;flex-wrap:wrap}
.evstrip li a:hover .nx-title{color:var(--accent)}
.nx-date{color:var(--muted);font-size:.78rem;min-width:9.5rem}
.navbtn-events{border-color:#1f5a3d}
@media (max-width:720px){
  .evlist{grid-template-columns:1fr}
  .evcard{padding:.6rem}
  .evdate{flex-basis:2.7rem}
  .evbtn,.evcopy{min-height:36px}
  .evsub{padding:.55rem}
  .nx-date{min-width:0;flex-basis:100%}
}
"""

JS = r"""
(function initEvents(){
  const sec=document.getElementById('cat-events'); if(!sec) return;
  const strip=document.getElementById('evStrip');
  const viewEv=document.getElementById('viewEvents');
  let evType='';
  const now=Date.now();
  // cards that ended since the last build: hide from upcoming
  sec.querySelectorAll('#evUpcoming .evcard').forEach(c=>{ const e=Date.parse(c.dataset.end||''); if(e && e<=now) c.dataset.ended='1'; });
  function applyEv(){
    let shown=0;
    sec.querySelectorAll('#evUpcoming .evgroup').forEach(g=>{
      let n=0;
      g.querySelectorAll('.evcard').forEach(c=>{ const ok=!c.dataset.ended && (!evType||c.dataset.type===evType); c.hidden=!ok; if(ok) n++; });
      g.hidden=n===0; shown+=n;
    });
    sec.querySelectorAll('.evf').forEach(b=>b.classList.toggle('active',(b.dataset.evtype||'')===evType));
    const fe=document.getElementById('evFilterEmpty'); const em=document.getElementById('evEmpty');
    const any=[...sec.querySelectorAll('#evUpcoming .evcard')].some(c=>!c.dataset.ended);
    if(em) em.hidden=any; if(fe) fe.hidden=!(any && shown===0);
  }
  window.EV_hide=function(){ if(!sec.hidden){ sec.hidden=true; } if(strip) strip.hidden=false; if(viewEv) viewEv.classList.remove('active'); };
  window.EV_show=function(target){
    try{ activeView='cats'; }catch(_){}
    if(typeof overview!=='undefined') overview.hidden=true;
    if(typeof dets!=='undefined') dets.forEach(d=>d.hidden=true);
    const ma=document.getElementById('cat-motd'); if(ma) ma.hidden=true;
    const mb=document.getElementById('motd'); if(mb) mb.hidden=true;
    sec.hidden=false; if(strip) strip.hidden=true;
    document.querySelectorAll('.viewbtn').forEach(b=>b.classList.toggle('active', b===viewEv));
    if(typeof nav!=='undefined') nav.querySelectorAll('.navbtn').forEach(b=>b.classList.toggle('active', b.dataset.cat==='events'));
    applyEv();
    let el=null;
    if(target && target!=='cat-events'){
      el=document.getElementById(target);
      if(el && el.closest('#evPast')) el.closest('#evPast').open=true;
      if(el && el.hidden){ evType=''; applyEv(); }
    }
    history.replaceState(null,'','#'+(el?target:'cat-events'));
    if(el){ el.classList.add('ev-flash'); setTimeout(()=>el.classList.remove('ev-flash'),2200); el.scrollIntoView({behavior:'smooth',block:'center'}); }
    else window.scrollTo({top:0,behavior:'smooth'});
  };
  sec.addEventListener('click',e=>{
    const f=e.target.closest('.evf'); if(f){ evType=f.dataset.evtype||''; applyEv(); return; }
    if(e.target.closest('[data-ev-back]')){ window.EV_hide(); if(typeof setView==='function') setView('cats'); return; }
    const cp=e.target.closest('#evCopy');
    if(cp){ const u=document.getElementById('evIcsUrl').textContent;
      const done=()=>{ cp.textContent='Zkopírováno ✓'; setTimeout(()=>cp.textContent='Kopírovat',1800); };
      if(navigator.clipboard) navigator.clipboard.writeText(u).then(done,()=>{}); return; }
    const ch=e.target.closest('.evch');
    if(ch){ e.preventDefault(); window.EV_hide(); const id=ch.getAttribute('href').slice(1); if(typeof showDetail==='function') showDetail(id); }
  });
  document.addEventListener('click',e=>{
    const j=e.target.closest('[data-ev-jump]'); if(!j) return;
    e.preventDefault(); window.EV_show(j.dataset.evJump||'cat-events');
  });
  if(viewEv) viewEv.addEventListener('click',()=>window.EV_show());
  // hide strip when strip events already ended
  if(strip){ strip.querySelectorAll('li').forEach(li=>{ const id=li.querySelector('a').dataset.evJump; const c=document.getElementById(id); if(c && c.dataset.ended) li.hidden=true; });
    if(![...strip.querySelectorAll('li')].some(li=>!li.hidden)) strip.remove(); }
  applyEv();
})();
"""
