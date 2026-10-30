#!/usr/bin/env python3
"""Look up the latest archive snapshots for every outbound link of the site.

- archive.org: `http://web.archive.org/web/2/<url>` redirects (302) to the newest snapshot
  (HTTP is used because HTTPS to web.archive.org has frequent TLS handshake failures from the box;
  stored/displayed links are https). Fallback: availability API https://archive.org/wayback/available.
- archive.today (archive.ph): best-effort probe of `/newest/<url>`; on 429/captcha we stop and mark
  every remaining link `blocked_captcha` (never solve captchas).
Output: data/archive_status.json  {"updated_at", "summary", "links": {url: {"wayback": {...}, "archive_is": {...}}}}
Existing wayback info for a URL is kept if the new lookup errors.
"""
import concurrent.futures as cf
import datetime as dt
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from check_links import extract_links  # noqa: E402

OUT = ROOT / 'data' / 'archive_status.json'
UA = 'acr-az-mapa-monitor/1.0 (+https://mapa-acr-az.vercel.app)'
TS_RE = re.compile(r'/web/(\d{14})')


def load():
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'links': {}}


def ts_iso(ts):
    try:
        return dt.datetime.strptime(ts, '%Y%m%d%H%M%S').replace(tzinfo=dt.timezone.utc).isoformat()
    except Exception:
        return None


def wayback_latest(url):
    now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    try:
        r = requests.get('http://web.archive.org/web/2/' + url, headers={'User-Agent': UA},
                         allow_redirects=False, timeout=30)
        loc = r.headers.get('location') or ''
        m = TS_RE.search(loc)
        if r.status_code in (301, 302) and m:
            snap = 'https://web.archive.org/web/' + m.group(1) + '/' + url
            return {'status': 'archived', 'snapshot_url': snap, 'timestamp': m.group(1),
                    'timestamp_iso': ts_iso(m.group(1)), 'checked_at': now}
        if r.status_code == 404:
            return {'status': 'none', 'snapshot_url': None, 'timestamp': None, 'checked_at': now}
        err = f'HTTP {r.status_code}'
    except Exception as e:
        err = f'{type(e).__name__}: {str(e)[:120]}'
    try:
        r = requests.get('https://archive.org/wayback/available', params={'url': url, 'timestamp': dt.date.today().strftime('%Y%m%d')},
                         headers={'User-Agent': UA}, timeout=30)
        c = (r.json().get('archived_snapshots') or {}).get('closest')
        if c and c.get('timestamp'):
            return {'status': 'archived', 'snapshot_url': 'https://web.archive.org/web/' + c['timestamp'] + '/' + url,
                    'timestamp': c['timestamp'], 'timestamp_iso': ts_iso(c['timestamp']), 'checked_at': now}
        return {'status': 'none', 'snapshot_url': None, 'timestamp': None, 'checked_at': now, 'note': err}
    except Exception as e2:
        return {'status': 'error', 'error': f'{err}; api: {type(e2).__name__}', 'checked_at': now}


def archive_is_probe(url):
    """Returns dict; status in archived/none/blocked_captcha/error."""
    now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    try:
        r = requests.get('http://archive.ph/newest/' + url, headers={'User-Agent': 'Mozilla/5.0'},
                         allow_redirects=False, timeout=30)
        body = r.text[:60000] if r.status_code != 302 else ''
        if r.status_code == 429 or 'captcha' in body.lower():
            return {'status': 'blocked_captcha', 'http_status': r.status_code, 'checked_at': now}
        loc = r.headers.get('location') or ''
        if r.status_code in (301, 302) and re.search(r'archive\.\w+/\w{4,6}$', loc):
            return {'status': 'archived', 'snapshot_url': loc.replace('http://', 'https://'), 'checked_at': now}
        if r.status_code == 404:
            return {'status': 'none', 'checked_at': now}
        return {'status': 'error', 'http_status': r.status_code, 'location': loc[:200], 'checked_at': now}
    except Exception as e:
        return {'status': 'error', 'error': f'{type(e).__name__}: {str(e)[:120]}', 'checked_at': now}


def main():
    links = sorted(extract_links())
    st = load()
    cur = st.get('links') or {}
    with cf.ThreadPoolExecutor(4) as ex:
        wb = dict(zip(links, ex.map(wayback_latest, links)))
    # archive.today: probe a few; stop on captcha
    ais = {}
    blocked = None
    for u in links[:5]:
        res = archive_is_probe(u)
        ais[u] = res
        if res['status'] in ('blocked_captcha',):
            blocked = res
            break
    now = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    new = {}
    for u in links:
        prev = cur.get(u) or {}
        w = wb[u]
        pw = prev.get('wayback') or {}
        # never downgrade: keep a known (newer) snapshot if the lookup errors / is not indexed yet
        if pw.get('snapshot_url') and (w.get('status') != 'archived' or (pw.get('timestamp') or '') > (w.get('timestamp') or '')):
            w = {**pw, 'checked_at': w['checked_at'], **({'last_error': w['error']} if w.get('error') else {})}
        # keep submission bookkeeping
        for k in ('last_submit', 'last_submit_status'):
            if k in (prev.get('wayback') or {}):
                w[k] = prev['wayback'][k]
        base = {k: v for k, v in (blocked or {}).items() if k not in ('last_submit', 'last_submit_status')}
        a = dict(ais[u]) if u in ais else (dict(base, checked_at=now, note='not probed – archive.ph captcha/429') if blocked
                                           else dict(prev.get('archive_is') or {'status': 'not_checked'}))
        for k in ('last_submit', 'last_submit_status'):
            if k in (prev.get('archive_is') or {}):
                a[k] = prev['archive_is'][k]
        new[u] = {'wayback': w, 'archive_is': a,
                  'wayback_browse': 'https://web.archive.org/web/*/' + u,
                  'archive_is_browse': 'https://archive.ph/newest/' + u}
    summ = {'links': len(new),
            'wayback_archived': sum(1 for v in new.values() if v['wayback'].get('status') == 'archived'),
            'wayback_none': sum(1 for v in new.values() if v['wayback'].get('status') == 'none'),
            'wayback_error': sum(1 for v in new.values() if v['wayback'].get('status') == 'error'),
            'archive_is': {}}
    for v in new.values():
        s = v['archive_is'].get('status')
        summ['archive_is'][s] = summ['archive_is'].get(s, 0) + 1
    st.update(updated_at=now, summary=summ, links=new)
    OUT.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
    print('archive lookup:', json.dumps(summ, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
