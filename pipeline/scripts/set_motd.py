#!/usr/bin/env python3
"""Validate + write the daily 'Zpráva dne' (MOTD) → data/motd/YYYY-MM-DD.json.

  python3 scripts/set_motd.py draft.json            # validate + write
  python3 scripts/set_motd.py --check draft.json    # validate only
  python3 scripts/set_motd.py --check data/motd/2026-09-25.json

Draft = {"date": "YYYY-MM-DD" (optional, default today), "headline": "...",
         "items": [{"text", "category", "importance", "link", "source_url"?}]}
Text is written by a human/LLM from scripts/motd_facts.py output – no templating, no invention.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOTD = ROOT / 'data' / 'motd'
CATS = ('příspěvek', 'komentář', 'dostupnost', 'SSL', 'odkazy', 'archiv', 'jiné')
IMPS = ('vysoká', 'střední')
ITEM_KEYS = {'text', 'category', 'importance', 'link', 'source_url'}
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import social_permalinks as _sp
except Exception:
    _sp = None


def validate(d):
    err = []
    if not isinstance(d, dict):
        return ['top level must be an object']
    date = d.get('date')
    if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        err.append('date must be YYYY-MM-DD')
    h = d.get('headline')
    if not isinstance(h, str) or not h.strip():
        err.append('headline required')
    elif len(h) > 160:
        err.append(f'headline too long ({len(h)} > 160 chars; 1 short sentence)')
    items = d.get('items')
    if not isinstance(items, list) or not 3 <= len(items) <= 6:
        err.append('items must be a list of 3–6 entries')
        items = items if isinstance(items, list) else []
    for i, it in enumerate(items, 1):
        p = f'items[{i}]'
        if not isinstance(it, dict):
            err.append(f'{p} must be an object'); continue
        extra = set(it) - ITEM_KEYS
        if extra:
            err.append(f'{p} unknown keys {sorted(extra)}')
        t = it.get('text')
        if not isinstance(t, str) or len(t.strip()) < 10:
            err.append(f'{p}.text required (1–2 sentences)')
        elif len(t) > 400:
            err.append(f'{p}.text too long ({len(t)} > 400)')
        elif len(re.findall(r'[.!?…](?=\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ„"(@]|\s*$)', t.strip())) > 2:
            err.append(f'{p}.text: max 2 sentences')
        if it.get('category') not in CATS:
            err.append(f'{p}.category must be one of {CATS}')
        if it.get('importance') not in IMPS:
            err.append(f'{p}.importance must be one of {IMPS}')
        ln = it.get('link')
        if not isinstance(ln, str) or not re.match(r'^(#[\w-]+|card:https?://\S+|https?://\S+)$', ln):
            err.append(f'{p}.link must be "#anchor", "card:<channel URL>" or http(s) URL')
        su = it.get('source_url')
        if su is not None and (not isinstance(su, str) or not re.match(r'^https?://\S+$', su)):
            err.append(f'{p}.source_url must be http(s) URL')
        elif su and _sp is not None and _sp.is_social(su) and not _sp.is_post_permalink(su):
            # social sources must link the concrete post (x.com/<h>/status/<id>, FB/IG post, YT watch…)
            print(f'WARN {p}.source_url is a social account, not a post permalink: {su} '
                  f'(use the post URL, e.g. from data/x_posts.json / x_tweet_index.json)', file=sys.stderr)
    return err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--force', action='store_true', help='overwrite existing file for that date')
    a = ap.parse_args()
    d = json.loads(Path(a.file).read_text(encoding='utf-8'))
    d.setdefault('date', datetime.now().date().isoformat())
    for it in d.get('items') or []:
        if isinstance(it, dict):
            for k in list(it):
                if isinstance(it[k], str):
                    it[k] = it[k].strip()
            if it.get('source_url') in ('', None):
                it.pop('source_url', None)
    err = validate(d)
    if err:
        print('INVALID:\n  - ' + '\n  - '.join(err), file=sys.stderr)
        sys.exit(1)
    if a.check:
        print(f'ok: {d["date"]} · {len(d["items"])} items')
        return
    out = MOTD / f'{d["date"]}.json'
    if out.exists() and not a.force and out.resolve() != Path(a.file).resolve():
        print(f'{out} exists – use --force to overwrite', file=sys.stderr)
        sys.exit(2)
    rec = {'date': d['date'], 'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
           'headline': d['headline'].strip(), 'items': d['items']}
    MOTD.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'wrote {out.relative_to(ROOT)} · {len(rec["items"])} items')


if __name__ == '__main__':
    main()
