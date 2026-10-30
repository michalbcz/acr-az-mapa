#!/usr/bin/env python3
"""Merge event candidates (data/events_inbox/YYYY-MM-DD.json) into data/events.json.

Usage:
  python3 scripts/update_events.py data/events_inbox/2026-09-25.json [more.json ...]
  python3 scripts/update_events.py --check data/events_inbox/2026-09-25.json   # validate only
  python3 scripts/update_events.py --list                                         # print current events

Candidate fields (JSON list of objects):
  title, type, start, end, all_day, location, organizer, description, source_url,
  channel (mapped channel URL or null), confidence, [date_precision: "day"|"month"],
  [replaces: "<old id>"]  (use when a known event moved to a new date/title)
Dates: all-day -> "YYYY-MM-DD" (end INCLUSIVE); timed -> "YYYY-MM-DDTHH:MM" local Europe/Prague.
Only real events with a source_url you actually opened. Never invent dates.
Id = stable hash of normalized title + start date + normalized location. Re-running is idempotent:
existing ids keep first_seen, changed fields bump `updated`. Past events stay in the file
(the site shows them under "Proběhlé události").
"""
import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data' / 'events.json'
TYPES = ['přijímač/základní příprava', 'výběrové řízení/nábor', 'cvičení', 'veřejná akce', 'jiné']
CONF = ['potvrzeno', 'orientačně']
FIELDS = ['title', 'type', 'start', 'end', 'all_day', 'location', 'organizer', 'description',
          'source_url', 'channel', 'confidence', 'date_precision']
RE_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
RE_DT = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')


def norm(s):
    s = unicodedata.normalize('NFKD', (s or '').lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def make_id(ev):
    key = f"{norm(ev['title'])}|{ev['start'][:10]}|{norm(ev.get('location'))}"
    return 'ev-' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]


def parse(s):
    if RE_DATE.match(s):
        return dt.datetime.strptime(s, '%Y-%m-%d')
    return dt.datetime.strptime(s, '%Y-%m-%dT%H:%M')


def validate(ev):
    errs = []
    for f in ('title', 'type', 'start', 'source_url', 'confidence'):
        if not ev.get(f):
            errs.append(f'chybí {f}')
    if errs:
        return errs
    if ev['type'] not in TYPES:
        errs.append(f"neznámý type {ev['type']!r} (povoleno: {TYPES})")
    if ev['confidence'] not in CONF:
        errs.append(f"neznámé confidence {ev['confidence']!r}")
    if not re.match(r'^https?://', ev['source_url']):
        errs.append('source_url musí být http(s) URL')
    allday = bool(ev.get('all_day'))
    for f in ('start', 'end'):
        v = ev.get(f)
        if v is None and f == 'end':
            continue
        if allday and not RE_DATE.match(v or ''):
            errs.append(f'{f} u celodenní akce musí být YYYY-MM-DD')
        if not allday and not RE_DT.match(v or ''):
            errs.append(f'{f} u akce s časem musí být YYYY-MM-DDTHH:MM')
    if not errs and ev.get('end') and parse(ev['end']) < parse(ev['start']):
        errs.append('end je před start')
    if ev.get('date_precision') not in (None, 'day', 'month'):
        errs.append('date_precision musí být day|month')
    if ev.get('date_precision') == 'month' and ev['confidence'] != 'orientačně':
        errs.append('měsíční přesnost => confidence "orientačně"')
    if ev.get('channel') and not re.match(r'^https?://', ev['channel']):
        errs.append('channel musí být URL nebo null')
    return errs


def load(p):
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding='utf-8'))


def main(argv):
    check = '--check' in argv
    if '--list' in argv:
        for e in load(DATA):
            print(e['start'], '|', e['type'], '|', e['title'], '|', e['id'])
        return 0
    files = [a for a in argv if not a.startswith('--')]
    if not files:
        print(__doc__)
        return 2
    today = dt.date.today().isoformat()
    events = load(DATA)
    by_id = {e['id']: e for e in events}
    bad = 0
    added = updated = same = 0
    for fp in files:
        p = Path(fp)
        if not p.is_absolute() and not p.exists():
            p = ROOT / fp
        cands = json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(cands, list):
            print(f'{p}: očekáván JSON list'); return 1
        for i, c in enumerate(cands):
            errs = validate(c)
            if errs:
                bad += 1
                print(f'✗ {p.name}[{i}] {c.get("title")!r}: ' + '; '.join(errs))
                continue
            ev = {f: c.get(f) for f in FIELDS}
            ev['all_day'] = bool(ev['all_day'])
            if not ev.get('end'):
                ev['end'] = None
            if not ev.get('date_precision'):
                ev['date_precision'] = 'day'
            ev['id'] = make_id(ev)
            old = by_id.get(c.get('replaces')) if c.get('replaces') else None
            if old is not None and old['id'] != ev['id']:
                by_id.pop(old['id'], None)
                ev['first_seen'] = old.get('first_seen', today)
                ev['replaced_ids'] = sorted(set((old.get('replaced_ids') or []) + [old['id']]))
                ev['updated'] = today
                by_id[ev['id']] = ev
                updated += 1
                continue
            cur = by_id.get(ev['id'])
            if cur is None:
                ev['first_seen'] = today
                ev['updated'] = today
                by_id[ev['id']] = ev
                added += 1
            else:
                changed = any(cur.get(f) != ev.get(f) for f in FIELDS)
                if changed:
                    for f in FIELDS:
                        cur[f] = ev.get(f)
                    cur['updated'] = today
                    updated += 1
                else:
                    same += 1
    print(f'kandidáti: přidáno {added}, aktualizováno {updated}, beze změny {same}, chybných {bad}')
    if check:
        print('(--check: nic nezapsáno)')
        return 1 if bad else 0
    out = sorted(by_id.values(), key=lambda e: (e['start'], e['title']))
    key_order = ['id'] + FIELDS + ['first_seen', 'updated', 'replaced_ids']
    out = [{k: e[k] for k in key_order if k in e} for e in out]
    DATA.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'{DATA} – celkem {len(out)} událostí')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
