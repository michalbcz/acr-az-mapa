#!/usr/bin/env python3
"""Check ALL outbound links of the built site (site_v2/).

Sources: href="http…" in site_v2/index.html (tiles, details, news items, absence section…),
plus URLs in site_v2/updates_history.json (feed/history, rendered client-side) and
site_v2/interesting_comments.json (comments section).

Method: HEAD (redirects followed) → GET fallback on error/≥400; browser-like UA; timeout 15 s;
8 threads. Classification (status field):
  ok          2xx/3xx
  broken      404/410, DNS failure, connection refused; 5xx on non-social sites
  login_wall  social sites (FB/IG/X/LinkedIn/…) answering 4xx/999/login redirect, or any 401/403/429
  unknown     timeout, TLS handshake error (e.g. czdefence.cz from the box), other errors
Outputs: data/link_status.json (current, keyed by URL) and data/link_history.jsonl
(one row per URL per day, same-day row replaced).
"""
import concurrent.futures as cf
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / 'site_v2'
STATUS = ROOT / 'data' / 'link_status.json'
HIST = ROOT / 'data' / 'link_history.jsonl'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
SOCIAL = ('facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'linkedin.com', 'youtube.com',
          'tiktok.com', 'threads.net', 'fb.com', 'fb.me', 't.me', 'discord.com', 'discord.gg')
SKIP_HOSTS = ('web.archive.org', 'archive.org', 'archive.ph', 'archive.is', 'archive.today',
              'mapa-acr-az.vercel.app', 'vercel.app')


def host_of(u):
    return (urlparse(u).hostname or '').lower()


def is_social(u):
    h = host_of(u)
    return any(h == s or h.endswith('.' + s) for s in SOCIAL)


def _retired():
    try:
        d = json.loads((ROOT / 'data' / 'url_aliases.json').read_text(encoding='utf-8'))
        return {k for k in d if not k.startswith('_')}
    except Exception:
        return set()


RETIRED = _retired()  # nahrazené URL (data/url_aliases.json) – jen v archivu, nekontrolují se


def _skip(u):
    h = host_of(u)
    if u in RETIRED:
        return True
    return (not h) or any(h == s or h.endswith('.' + s) for s in SKIP_HOSTS)


def extract_links(site=SITE):
    """Return {url: [sources]} of outbound links in the built site."""
    out = {}

    def add(u, src):
        if not isinstance(u, str):
            return
        u = u.strip()
        if not u.startswith(('http://', 'https://')) or _skip(u):
            return
        out.setdefault(u, [])
        if src not in out[u]:
            out[u].append(src)

    idx = site / 'index.html'
    if idx.exists():
        import html as H
        for m in re.finditer(r'href="(https?://[^"]+)"', idx.read_text(encoding='utf-8', errors='replace')):
            add(H.unescape(m.group(1)), 'page')
    uh = site / 'updates_history.json'
    if uh.exists():
        for o in json.loads(uh.read_text(encoding='utf-8')):
            add(o.get('url'), 'feed')
            add(o.get('post_url'), 'feed')
            for u in o.get('urls') or []:
                add(u, 'feed')
    ic = site / 'interesting_comments.json'
    if ic.exists():
        for o in json.loads(ic.read_text(encoding='utf-8')):
            for k in ('post_url', 'comment_url', 'channel_url'):
                add(o.get(k), 'comments')
    return out


def classify(url, code, err_kind):
    social = is_social(url)
    if code is not None:
        if code < 400:
            return 'ok'
        if code in (404, 410):
            return 'broken'
        if code in (401, 403, 429, 999) or social:
            return 'login_wall'
        if code >= 500:
            return 'broken'
        return 'unknown'
    if err_kind in ('dns', 'refused'):
        return 'broken'
    return 'unknown'


def check(url):
    r = {'url': url, 'http_status': None, 'final_url': None, 'method': None, 'response_ms': None,
         'error': None, 'social': is_social(url)}
    hdr = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8', 'Accept-Language': 'cs,en;q=0.8'}
    err_kind = None
    for method in ('HEAD', 'GET'):
        t0 = time.monotonic()
        try:
            resp = requests.request(method, url, headers=hdr, timeout=15, allow_redirects=True, stream=True)
            r.update(http_status=resp.status_code, final_url=resp.url, method=method,
                     response_ms=int((time.monotonic() - t0) * 1000), error=None)
            resp.close()
            err_kind = None
            if resp.status_code < 400:
                break
        except requests.exceptions.SSLError as e:
            r.update(error=f'TLS error: {str(e)[:160]}', method=method)
            err_kind = 'tls'
        except requests.exceptions.ConnectionError as e:
            s = str(e)
            if 'NameResolution' in s or 'Name or service not known' in s or 'getaddrinfo' in s:
                err_kind = 'dns'
            elif 'Connection refused' in s:
                err_kind = 'refused'
            else:
                err_kind = 'conn'
            r.update(error=f'{err_kind}: {s[:160]}', method=method)
        except requests.exceptions.Timeout:
            err_kind = 'timeout'
            r.update(error='timeout', method=method)
        except Exception as e:
            err_kind = 'other'
            r.update(error=f'{type(e).__name__}: {str(e)[:160]}', method=method)
    fu = (r.get('final_url') or '').lower()
    r['status'] = classify(url, r['http_status'], err_kind)
    # login redirects of social networks → login_wall, not ok/broken
    if r['social'] and any(k in fu for k in ('/login', 'accounts/login', '/authwall', 'checkpoint')):
        r['status'] = 'login_wall'
    return r


def main():
    links = extract_links()
    now = dt.datetime.now().astimezone()
    today = now.date().isoformat()
    urls = sorted(links)
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(check, urls))
    cur = {}
    for r in results:
        r['sources'] = links[r['url']]
        r['checked_at'] = now.isoformat(timespec='seconds')
        cur[r['url']] = r
    counts = {}
    for r in results:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    STATUS.write_text(json.dumps({'checked_at': now.isoformat(timespec='seconds'), 'counts': counts,
                                  'links': cur}, ensure_ascii=False, indent=1), encoding='utf-8')
    old = []
    if HIST.exists():
        for line in HIST.read_text(encoding='utf-8').splitlines():
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get('date') == today and o.get('url') in cur:
                continue
            old.append(o)
    with HIST.open('w', encoding='utf-8') as f:
        for o in old:
            f.write(json.dumps(o, ensure_ascii=False) + '\n')
        for r in results:
            f.write(json.dumps({'date': today, 'ts': r['checked_at'], 'url': r['url'], 'status': r['status'],
                                'http_status': r['http_status'], 'error': r['error']}, ensure_ascii=False) + '\n')
    print(f'links: {len(results)} checked; ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    for r in results:
        if r['status'] != 'ok':
            print(f"  {r['status']:10s} {r['http_status']} {r['url']} {r['error'] or ''}"[:220])
    return 0


if __name__ == '__main__':
    sys.exit(main())
