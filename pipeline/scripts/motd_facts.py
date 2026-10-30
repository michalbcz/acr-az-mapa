#!/usr/bin/env python3
"""Compact JSON digest of candidate facts for the daily 'Zpráva dne' (MOTD).

Prints facts from the last ~36 h so a human/LLM can pick 3–6 genuinely
important items and write them via scripts/set_motd.py. Never writes prose.

  python3 scripts/motd_facts.py [--hours 36] [--no-snapshot]

Side effect (unless --no-snapshot): stores today's counts in
data/motd/stats/YYYY-MM-DD.json so tomorrow's run can diff them.
"""
import argparse
import importlib.util
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
MOTD = DATA / 'motd'
STATS = MOTD / 'stats'

SOCIAL = ('facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'youtube.com', 'youtu.be', 'linkedin.com')


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def jsonl(p):
    out = []
    if not p.exists():
        return out
    for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def parse_ts(s):
    if not s:
        return None
    s = str(s).strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def is_social(url):
    h = urlparse(url or '').netloc.lower()
    return any(h == k or h.endswith('.' + k) for k in SOCIAL)


def officiality(url):
    try:
        b = _load_mod('build_site_v2_facts', ROOT / 'build_site_v2.py')
        officiality.fn = b.officiality
    except Exception:
        officiality.fn = lambda u: ('oficialni' if '.mo.gov.cz' in (u or '') else 'neoficialni', '')
    return officiality.fn(url)


def off(url):
    fn = getattr(officiality, 'fn', None)
    if fn is None:
        officiality(url)
        fn = officiality.fn
    return fn(url)[0] == 'oficialni'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hours', type=float, default=36)
    ap.add_argument('--no-snapshot', action='store_true')
    a = ap.parse_args()

    now = datetime.now().astimezone()
    today = now.date().isoformat()
    cutoff = now - timedelta(hours=a.hours)
    cutoff_day = cutoff.date().isoformat()
    facts = {'generated_at': now.isoformat(timespec='seconds'), 'window_hours': a.hours,
             'cutoff': cutoff.isoformat(timespec='seconds')}

    # --- previous MOTD (avoid repeating unless still critical)
    prev = sorted(p for p in MOTD.glob('????-??-??.json') if p.stem < today)
    if prev:
        pm = json.loads(prev[-1].read_text(encoding='utf-8'))
        facts['previous_motd'] = {'date': pm.get('date'), 'headline': pm.get('headline'),
                                  'items': [i.get('text') for i in pm.get('items', [])]}

    caps = {r.get('url'): r for r in jsonl(DATA / 'captures.jsonl')}

    # --- history: new posts per channel
    hist = jsonl(DATA / 'updates_history.jsonl')
    try:
        amod = _load_mod('archive_updates_facts', ROOT / 'scripts' / 'archive_updates.py')
        ck = amod.channel_key
    except Exception:
        ck = lambda u: urlparse(u or '').netloc
    per_ch = defaultdict(list)
    for r in hist:
        day = (r.get('date_iso') or '')[:10]
        fs = parse_ts(r.get('first_seen'))
        fresh_pub = day >= cutoff_day and day <= today
        fresh_seen = fs is not None and fs >= cutoff and (not day or day >= cutoff_day)
        if not (fresh_pub or fresh_seen):
            continue
        if re.fullmatch(r'\d+\s+koment\w*', (r.get('title') or '').strip(), re.I):
            continue  # comment-counter noise, not a post
        per_ch[ck(r.get('url'))].append(r)
    cap_by_ch = {}
    for u in caps:
        cap_by_ch.setdefault(ck(u), u)
    posts = []
    for ch, rows in per_ch.items():
        rows.sort(key=lambda r: (r.get('date_iso') or '', r.get('first_seen') or ''), reverse=True)
        for r in rows[:2]:
            posts.append({'channel': ch, 'official': off(r.get('url')), 'source': r.get('source_label'),
                          'category': r.get('category'), 'title': (r.get('title') or '')[:200],
                          'date': r.get('date_iso'), 'date_raw': r.get('date_raw'),
                          'first_seen': r.get('first_seen'), 'url': r.get('url'),
                          # permalink of the concrete post → use as MOTD source_url
                          'post_url': r.get('post_url') or None,
                          'card': next(('card:' + u for u in (r.get('urls') or []) + [r.get('url')] if u in caps),
                                      ('card:' + cap_by_ch[ch]) if ch in cap_by_ch else None),
                          'more_in_channel': max(0, len(rows) - 2) if r is rows[0] else 0})
    posts.sort(key=lambda p: (not p['official'], -(int(re.sub(r'\D', '', (p['date'] or '0')[:10]) or 0))))
    facts['new_posts'] = {'official': [p for p in posts if p['official']],
                          'unofficial': [p for p in posts if not p['official']]}

    # --- captures: status / last post changes vs the latest pre-refresh backup
    baks = sorted(DATA.glob('captures.jsonl.bak-pre-*'))
    cap_changes = []
    if baks:
        old = {r.get('url'): r for r in jsonl(baks[-1])}
        for u, r in caps.items():
            o = old.get(u)
            if not o:
                cap_changes.append({'url': u, 'change': 'new_channel'})
                continue
            if (o.get('status') or '') != (r.get('status') or ''):
                cap_changes.append({'url': u, 'change': 'status', 'from': o.get('status'), 'to': r.get('status')})
        facts['capture_changes'] = {'vs_backup': baks[-1].name, 'items': cap_changes}

    # --- comments
    com = [c for c in jsonl(DATA / 'interesting_comments.jsonl')
           if (parse_ts(c.get('first_seen')) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
    facts['new_comments'] = [{'channel': c.get('channel'), 'post': (c.get('post_title_or_excerpt') or '')[:120],
                              'text': (c.get('comment_text') or '')[:220], 'reasons': c.get('reasons'),
                              'note': c.get('reason_note'), 'author_type': c.get('author_type'),
                              'engagement': c.get('engagement'), 'comment_url': c.get('comment_url'),
                              'date': c.get('comment_date_iso'), 'approx': c.get('comment_date_approx')}
                             for c in com]

    # --- health
    hh = jsonl(DATA / 'health_history.jsonl')
    by_date = defaultdict(dict)
    for r in hh:
        by_date[r.get('date')][r.get('url')] = r
    dates = sorted(d for d in by_date if d)
    health = {'dates_available': dates[-3:]}
    if dates:
        cur = by_date[dates[-1]]
        prevd = by_date[dates[-2]] if len(dates) > 1 else {}
        health['latest_date'] = dates[-1]
        health['sites'] = len(cur)
        health['up'] = sum(1 for r in cur.values() if r.get('up'))
        health['down_now'] = [{'url': u, 'http_status': r.get('http_status'), 'error': (r.get('error') or '')[:120],
                               'was_up_before': (prevd.get(u) or {}).get('up') if prevd else None}
                              for u, r in cur.items() if not r.get('up')]
        health['transitions'] = [{'url': u, 'from': 'up' if prevd[u].get('up') else 'down',
                                  'to': 'up' if r.get('up') else 'down'}
                                 for u, r in cur.items() if u in prevd and bool(prevd[u].get('up')) != bool(r.get('up'))]
        certs = {}
        for u, r in cur.items():
            t = r.get('tls') or {}
            d = t.get('days_to_expiry')
            h = r.get('host') or urlparse(u).netloc
            if isinstance(d, int) and d <= 30 and h not in certs:
                certs[h] = {'host': h, 'days_to_expiry': d, 'not_after': t.get('not_after'),
                            'issuer': t.get('issuer_o'), 'url': u}
        health['certs_le_30d'] = sorted(certs.values(), key=lambda x: x['days_to_expiry'])
        tlserr = {}
        for u, r in cur.items():
            t = r.get('tls') or {}
            if u.startswith('https') and not t.get('ok'):
                h = r.get('host') or urlparse(u).netloc
                tlserr.setdefault(h, {'host': h, 'error': (t.get('error') or r.get('error') or '')[:100]})
        health['tls_errors'] = list(tlserr.values())
    facts['health'] = health

    # --- links
    ls = json.loads((DATA / 'link_status.json').read_text()) if (DATA / 'link_status.json').exists() else {}
    lh = jsonl(DATA / 'link_history.jsonl')
    ldates = sorted({r.get('date') for r in lh if r.get('date')})
    prev_broken = set()
    if len(ldates) > 1:
        prev_broken = {r['url'] for r in lh if r.get('date') == ldates[-2] and r.get('status') == 'broken'}
    broken = [{'url': u, 'http_status': v.get('http_status'), 'error': (v.get('error') or '')[:100],
               'sources': v.get('sources'), 'new': u not in prev_broken}
              for u, v in (ls.get('links') or {}).items() if v.get('status') == 'broken']
    unk_hosts = Counter(urlparse(u).netloc for u, v in (ls.get('links') or {}).items() if v.get('status') == 'unknown')
    facts['links'] = {'checked_at': ls.get('checked_at'), 'counts': ls.get('counts'),
                      'previous_run_available': len(ldates) > 1, 'broken': broken,
                      'unknown_by_host': dict(unk_hosts)}

    # --- archive
    log = [r for r in jsonl(DATA / 'archive_submit_log.jsonl') if (parse_ts(r.get('ts')) or cutoff) >= cutoff]
    sub = Counter(f"{r.get('service')}:{r.get('status')}" for r in log)
    ast = json.loads((DATA / 'archive_status.json').read_text()) if (DATA / 'archive_status.json').exists() else {}
    wb = Counter((v.get('wayback') or {}).get('status') for v in (ast.get('links') or {}).values())
    facts['archive'] = {'submits_in_window': dict(sub), 'submit_attempts': len(log),
                        'unreachable': sorted({r['url'] for r in log if r.get('status') == 'origin_unreachable'}),
                        'wayback_status_counts': dict(wb), 'links_tracked': len(ast.get('links') or {})}

    # --- counts + diff vs previous snapshot
    counts = {'channels': len(caps), 'history_rows': len(hist),
              'comments': len(jsonl(DATA / 'interesting_comments.jsonl')),
              'health_sites': health.get('sites'), 'health_up': health.get('up'),
              'links_broken': (ls.get('counts') or {}).get('broken'),
              'wayback_archived': wb.get('archived', 0), 'certs_le_30d': len(health.get('certs_le_30d', []))}
    prev_stats = sorted(p for p in STATS.glob('????-??-??.json') if p.stem < today)
    diff = {}
    if prev_stats:
        ps = json.loads(prev_stats[-1].read_text())
        diff = {k: {'from': ps.get(k), 'to': v} for k, v in counts.items() if ps.get(k) != v}
        diff['_vs'] = prev_stats[-1].stem
    facts['counts'] = counts
    facts['counts_diff'] = diff or None
    if not a.no_snapshot:
        STATS.mkdir(parents=True, exist_ok=True)
        (STATS / f'{today}.json').write_text(json.dumps(counts, ensure_ascii=False, indent=1), encoding='utf-8')

    facts['hints'] = ['link: "#cat-health", "#kontrola-odkazu", "#cat-komentare", "#cat-feed", '
                      '"card:<mapped channel URL>" (builder resolves to the tile detail), or external https URL',
                      'categories: příspěvek|komentář|dostupnost|SSL|odkazy|archiv|jiné; importance: vysoká|střední']
    print(json.dumps(facts, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
