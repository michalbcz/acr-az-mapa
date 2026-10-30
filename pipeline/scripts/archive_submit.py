#!/usr/bin/env python3
"""Submit site links to archives.

Wayback Save Page Now (anonymous, `http://web.archive.org/save/<url>` — HTTP because HTTPS to
web.archive.org often fails TLS from the box). Only URLs without a snapshot in the last 7 days
(per data/archive_status.json, run scripts/archive_lookup.py first). Priority: channel main URLs
(websites) → channel main URLs (social) → other links (feed articles, news pages, …).
Throttle ~12 s between submissions; stop on 429 (rate limit); 523/520 (origin unreachable) recorded
and skipped. Cap per run: --cap (default 60).

archive.today (archive.ph/submit/) is best-effort: tries --ais-tries URLs (default 3); on
captcha/429 it stops and records `blocked_captcha`. Captchas are never solved.
Results are written back into data/archive_status.json and appended to data/archive_submit_log.jsonl.
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from check_links import is_social  # noqa: E402

STATUS = ROOT / 'data' / 'archive_status.json'
LOG = ROOT / 'data' / 'archive_submit_log.jsonl'
UA = 'acr-az-mapa-monitor/1.0 (+https://mapa-acr-az.vercel.app)'
SKIP_RE = re.compile(r'calendar\.google\.com|outlook\.(live|office)\.com/calendar|share\.php|sharer|/login|/admin|/wp-admin|/cms$|/user/login|authwall', re.I)


def main_urls():
    out = []
    for line in (ROOT / 'data' / 'captures.jsonl').read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            out.append(json.loads(line)['url'])
        except Exception:
            pass
    try:
        for v in json.loads((ROOT / 'urls.json').read_text()).values():
            out.extend(v)
    except Exception:
        pass
    return set(out)


def recent(ts, days=7):
    if not ts:
        return False
    try:
        t = dt.datetime.strptime(ts, '%Y%m%d%H%M%S').replace(tzinfo=dt.timezone.utc)
    except Exception:
        return False
    return (dt.datetime.now(dt.timezone.utc) - t).days < days


def log(o):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(json.dumps(o, ensure_ascii=False) + '\n')


def save_wayback(url):
    t0 = time.monotonic()
    try:
        r = requests.get('http://web.archive.org/save/' + url, headers={'User-Agent': UA},
                         allow_redirects=False, timeout=150)
        loc = r.headers.get('location') or ''
        m = re.search(r'/web/(\d{14})', loc)
        res = {'http_status': r.status_code, 'secs': round(time.monotonic() - t0, 1)}
        if r.status_code in (301, 302) and m:
            res.update(status='ok', timestamp=m.group(1), snapshot_url='https://web.archive.org/web/' + m.group(1) + '/' + url)
        elif r.status_code == 429:
            res['status'] = 'rate_limited'
        elif r.status_code in (520, 523):
            res['status'] = 'origin_unreachable'
        else:
            res['status'] = 'error'
            res['detail'] = re.sub(r'\s+', ' ', r.text[:300])
        return res
    except Exception as e:
        return {'status': 'error', 'detail': f'{type(e).__name__}: {str(e)[:150]}', 'secs': round(time.monotonic() - t0, 1)}


def save_archive_is(url):
    try:
        s = requests.Session()
        s.headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
        r = s.post('http://archive.ph/submit/', data={'url': url, 'anyway': '1'}, allow_redirects=False, timeout=60)
        body = r.text[:60000].lower() if r.status_code not in (301, 302) else ''
        loc = r.headers.get('location') or r.headers.get('refresh') or ''
        if r.status_code == 429 or 'captcha' in body:
            return {'status': 'blocked_captcha', 'http_status': r.status_code}
        if r.status_code in (301, 302) and loc.startswith('https://archive.') and '/submit' not in loc:
            return {'status': 'ok', 'snapshot_url': loc, 'http_status': r.status_code}
        if r.status_code in (301, 302) and loc.startswith('https://'):
            return {'status': 'blocked_tls_redirect', 'http_status': r.status_code, 'location': loc[:200]}
        return {'status': 'error', 'http_status': r.status_code, 'location': loc[:200]}
    except Exception as e:
        return {'status': 'error', 'detail': f'{type(e).__name__}: {str(e)[:150]}'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cap', type=int, default=60)
    ap.add_argument('--sleep', type=float, default=12.0)
    ap.add_argument('--ais-tries', type=int, default=3)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    st = json.loads(STATUS.read_text(encoding='utf-8'))
    links = st.get('links') or {}
    mains = main_urls()
    cand = []
    for u, v in links.items():
        if SKIP_RE.search(u):
            continue
        w = v.get('wayback') or {}
        if recent(w.get('timestamp')):
            continue
        ls = w.get('last_submit')
        if ls and ls[:10] == dt.date.today().isoformat():
            continue
        prio = 0 if (u in mains and not is_social(u)) else 1 if u in mains else 2
        cand.append((prio, u))
    cand.sort()
    todo = [u for _, u in cand[:a.cap]]
    print(f'wayback candidates: {len(cand)} (no snapshot ≤7 days); submitting {len(todo)}')
    stats = {}
    for i, u in enumerate(todo):
        if a.dry_run:
            print('  would submit', u)
            continue
        res = save_wayback(u)
        now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        stats[res['status']] = stats.get(res['status'], 0) + 1
        w = links[u].setdefault('wayback', {})
        w['last_submit'] = now
        w['last_submit_status'] = res['status']
        if res['status'] == 'ok':
            w.update(status='archived', snapshot_url=res['snapshot_url'], timestamp=res['timestamp'],
                     timestamp_iso=dt.datetime.strptime(res['timestamp'], '%Y%m%d%H%M%S').replace(tzinfo=dt.timezone.utc).isoformat())
        log({'ts': now, 'service': 'wayback', 'url': u, **res})
        print(f"  [{i + 1}/{len(todo)}] {res['status']} {res.get('http_status')} {res.get('secs')}s {u}", flush=True)
        STATUS.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
        if res['status'] == 'rate_limited':
            print('  429 from Save Page Now → stopping this run')
            break
        if i + 1 < len(todo):
            time.sleep(a.sleep)
    # archive.today best effort
    ais_stats = {}
    if not a.dry_run and a.ais_tries > 0:
        for u in [u for _, u in cand][:a.ais_tries]:
            res = save_archive_is(u)
            now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
            ais_stats[res['status']] = ais_stats.get(res['status'], 0) + 1
            ai = links[u].setdefault('archive_is', {})
            ai['last_submit'] = now
            ai['last_submit_status'] = res['status']
            if res['status'] == 'ok':
                ai.update(status='archived', snapshot_url=res['snapshot_url'])
            log({'ts': now, 'service': 'archive.today', 'url': u, **res})
            print(f"  archive.today: {res['status']} {res.get('http_status')} {u}")
            if res['status'] != 'ok':
                print('  archive.today not accepting automated submissions → stopping gracefully')
                break
            time.sleep(a.sleep)
    st.setdefault('last_submit_run', {})
    st['last_submit_run'] = {'at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                             'wayback': stats, 'archive_is': ais_stats, 'candidates': len(cand)}
    STATUS.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
    print('submit summary:', json.dumps(st['last_submit_run'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
