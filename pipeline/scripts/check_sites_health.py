#!/usr/bin/env python3
"""Daily health + TLS check of every website (non-social) URL in the map.

Source URLs: data/captures.jsonl (= tiles in the map) + urls.json.
Output: data/health_history.jsonl — one row per URL per day (same-day row replaced).

Row fields: date, ts, url, host, up, http_status, final_url, response_ms, error,
  tls: {ok, version, issuer_o, issuer_cn, subject_cn, san_count, not_before, not_after,
        days_to_expiry, verify_error, error}
Honest errors: TLS handshake failures (e.g. czdefence.cz from the box) are recorded as such.
"""
import concurrent.futures as cf
import datetime as dt
import json
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtensionOID

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / 'data' / 'health_history.jsonl'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 acr-az-mapa-monitor/1.0'
SOCIAL = ('facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'youtube.com', 'youtu.be',
          'linkedin.com', 'tiktok.com', 'discord.com', 'discord.gg', 't.me', 'threads.net')


def is_social(url):
    h = urlparse(url).netloc.lower()
    return any(h == s or h.endswith('.' + s) for s in SOCIAL)


def site_urls():
    urls = []
    cp = ROOT / 'data' / 'captures.jsonl'
    for line in cp.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if line:
            try:
                urls.append(json.loads(line).get('url') or '')
            except Exception:
                pass
    try:
        d = json.loads((ROOT / 'urls.json').read_text())
        for v in d.values():
            urls.extend(v)
    except Exception:
        pass
    seen, out = set(), []
    for u in urls:
        if u and u not in seen and not is_social(u) and u.startswith('http'):
            seen.add(u)
            out.append(u)
    return out


def _name_attr(name, oid):
    try:
        a = name.get_attributes_for_oid(oid)
        return a[0].value if a else None
    except Exception:
        return None


def tls_info(host, port=443, timeout=12):
    info = {'ok': False, 'version': None, 'issuer_o': None, 'issuer_cn': None, 'subject_cn': None,
            'san_count': None, 'not_before': None, 'not_after': None, 'days_to_expiry': None,
            'verify_error': None, 'error': None}
    der = None
    for verify in (True, False):
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ss:
                    info['version'] = ss.version()
                    der = ss.getpeercert(binary_form=True)
            if verify:
                info['ok'] = True
            break
        except ssl.SSLCertVerificationError as e:
            info['verify_error'] = (getattr(e, 'verify_message', None) or str(e))[:200]
            continue
        except Exception as e:
            info['error'] = f'{type(e).__name__}: {e}'[:200]
            break
    if der:
        try:
            c = x509.load_der_x509_certificate(der)
            info['issuer_o'] = _name_attr(c.issuer, NameOID.ORGANIZATION_NAME)
            info['issuer_cn'] = _name_attr(c.issuer, NameOID.COMMON_NAME)
            info['subject_cn'] = _name_attr(c.subject, NameOID.COMMON_NAME)
            try:
                san = c.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
                info['san_count'] = len(san.get_values_for_type(x509.DNSName))
            except Exception:
                info['san_count'] = 0
            nb = c.not_valid_before_utc
            na = c.not_valid_after_utc
            info['not_before'] = nb.isoformat()
            info['not_after'] = na.isoformat()
            info['days_to_expiry'] = (na - dt.datetime.now(dt.timezone.utc)).days
        except Exception as e:
            info['error'] = (info['error'] or '') + f' parse: {e}'[:120]
    return info


def http_check(url, timeout=20):
    r = {'up': False, 'http_status': None, 'final_url': None, 'response_ms': None, 'error': None}
    t0 = time.monotonic()
    try:
        resp = requests.get(url, headers={'User-Agent': UA, 'Accept-Language': 'cs,en;q=0.8'},
                            timeout=timeout, allow_redirects=True, stream=True)
        r['response_ms'] = int((time.monotonic() - t0) * 1000)
        r['http_status'] = resp.status_code
        r['final_url'] = resp.url
        r['up'] = resp.status_code < 500 and resp.status_code not in (404, 410)
        resp.close()
    except requests.exceptions.SSLError as e:
        r['error'] = f'TLS error: {str(e)[:180]}'
    except Exception as e:
        r['error'] = f'{type(e).__name__}: {str(e)[:180]}'
    return r


def main():
    urls = site_urls()
    now = dt.datetime.now().astimezone()
    today = now.date().isoformat()
    hosts = sorted({urlparse(u).hostname for u in urls if urlparse(u).scheme == 'https'})
    with cf.ThreadPoolExecutor(8) as ex:
        tls = dict(zip(hosts, ex.map(tls_info, hosts)))
        https = list(ex.map(http_check, urls))
    rows = []
    for u, h in zip(urls, https):
        p = urlparse(u)
        row = {'date': today, 'ts': now.isoformat(timespec='seconds'), 'url': u, 'host': p.hostname, **h}
        row['tls'] = tls.get(p.hostname) if p.scheme == 'https' else {'ok': False, 'error': 'plain http (no TLS)'}
        rows.append(row)
    old = []
    if HIST.exists():
        for line in HIST.read_text(encoding='utf-8').splitlines():
            if line.strip():
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get('date') == today and o.get('url') in set(urls):
                    continue  # idempotent per day
                old.append(o)
    with HIST.open('w', encoding='utf-8') as f:
        for o in old + rows:
            f.write(json.dumps(o, ensure_ascii=False) + '\n')
    up = sum(1 for r in rows if r['up'])
    print(f'health: {len(rows)} sites ({len(hosts)} TLS hosts), up={up} down={len(rows) - up}')
    for r in rows:
        t = r['tls'] or {}
        print(f"  {'UP ' if r['up'] else 'DOWN'} {r['http_status']} {r['response_ms']}ms {r['url']} "
              f"| TLS {t.get('version')} {t.get('issuer_o')} exp {t.get('days_to_expiry')}d "
              f"{t.get('verify_error') or ''} {t.get('error') or ''} {r['error'] or ''}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
