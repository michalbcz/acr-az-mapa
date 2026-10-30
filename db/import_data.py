#!/usr/bin/env python3
"""Ingest: JSON/JSONL soubory pipeline -> db/mapa.sqlite (idempotentní).

Databáze se vždy postaví celá znovu do dočasného souboru a atomicky
přejmenuje, takže opakované spuštění nad stejnými daty dá stejný obsah.

  python3 db/import_data.py [--root pipeline] [--db db/mapa.sqlite] [--shots DIR]
"""
import argparse, datetime as dt, glob, json, os, re, sqlite3, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from codec import pack  # noqa: E402
import model as M  # noqa: E402


def jl(p):
    out = []
    if not p.exists():
        return out
    for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def jd(p, default=None):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return default


def ins(cur, table, row):
    cols = list(row)
    cur.execute(f'INSERT INTO {table} ({",".join(chr(34)+c+chr(34) for c in cols)}) VALUES ({",".join("?"*len(cols))})',
                [row[c] for c in cols])


def P(obj, spec, rename=None, **kw):
    vals, extra = pack(obj, spec, **kw)
    for a, b in (rename or {}).items():
        vals[b] = vals.pop(a)
    vals['extra_json'] = extra
    return vals


def build(root: Path, db: Path, shots: Path | None):
    D = root / 'data'
    tmp = db.with_suffix('.sqlite.tmp')
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript((HERE / 'schema.sql').read_text(encoding='utf-8'))
    cur = con.cursor()
    meta = {}

    # urls.json
    urls = jd(root / 'urls.json', {}) or {}
    for g, lst in urls.items():
        for i, u in enumerate(lst):
            ins(cur, 'watched_urls', {'group_name': g, 'ord': i, 'url': u})
    meta['urls_groups'] = list(urls)

    # captures
    caps = jl(D / 'captures.jsonl')
    for i, r in enumerate(caps):
        v = P(r, M.CAPTURE, nested=M.CAPTURE_NESTED)
        v['capture_id'] = i
        ins(cur, 'captures', v)
        lp = r.get('last_post')
        if isinstance(lp, dict):
            ins(cur, 'capture_last_post', {'capture_id': i, **P(lp, M.LAST_POST)})
        for j, h in enumerate(r.get('hidden_links') or [] if isinstance(r.get('hidden_links'), list) else []):
            ins(cur, 'capture_hidden_links', {'capture_id': i, 'ord': j, **P(h, M.HIDDEN_LINK)})
        for j, h in enumerate(r.get('recent_posts') or [] if isinstance(r.get('recent_posts'), list) else []):
            ins(cur, 'capture_recent_posts', {'capture_id': i, 'ord': j, **P(h, M.RECENT_POST)})

    # channels (odvozené funkcemi builderu)
    sys.path.insert(0, str(root))
    try:
        import importlib
        b = importlib.import_module('build_site_v2')
        seen = set()
        for r in caps:
            u = r.get('url')
            if not u or u in seen:
                continue
            seen.add(u)
            ok, ol = b.officiality(u)
            host = re.sub(r'^www\.', '', (re.match(r'https?://([^/]+)', u) or [None, ''])[1].lower())
            plat = ('fb' if 'facebook.com' in host else 'ig' if 'instagram.com' in host else
                    'x' if host in ('x.com', 'twitter.com') else 'yt' if 'youtube.com' in host else
                    'li' if 'linkedin.com' in host else 'tiktok' if 'tiktok.com' in host else 'web')
            ins(cur, 'channels', {'url': u, 'category': b.categorize(u), 'officiality': ok,
                                  'officiality_label': ol, 'label': b.short_label(u), 'platform': plat})
    except Exception as e:  # kanály jsou jen odvozený pohled – import nesmí spadnout
        print(f'warn: channels: {e}')

    # enrichment
    en = jd(D / 'enrichment.json', {}) or {}
    for i, (u, e) in enumerate(en.items()):
        ins(cur, 'enrichment', {'url': u, 'ord': i, **P(e, M.ENRICH, nested=M.ENRICH_NESTED,
                                                        default_present=M.ENRICH_NESTED)})
        for j, a in enumerate(e.get('admin_urls') or []):
            ins(cur, 'enrichment_admin_urls', {'url': u, 'ord': j, 'admin_url': a})
        for j, a in enumerate(e.get('admin_checks') or []):
            ins(cur, 'enrichment_admin_checks', {'url': u, 'ord': j, **P(a, M.ADMIN_CHECK, {'url': 'check_url'})})
        for j, a in enumerate(e.get('news_pages') or []):
            ins(cur, 'enrichment_news_pages', {'url': u, 'ord': j, **P(a, M.NEWS_PAGE, {'url': 'page_url'})})

    # updates
    for i, r in enumerate(jl(D / 'updates_history.jsonl')):
        ins(cur, 'updates', {'ord': i, **P(r, M.UPDATE, nested=('urls',), default_present=('urls',))})
        for j, u in enumerate(r.get('urls') or []):
            ins(cur, 'update_urls', {'update_ord': i, 'ord': j, 'url': u})

    # comments
    reasons = ['nová informace / fakt', 'insider info / informace zevnitř', 'kritika / stížnost',
               'dezinformace / propaganda', 'bezpečnostní riziko (OPSEC)', 'otázka s odpovědí úřadu',
               'odpověď oficiálního účtu', 'výrazný ohlas', 'nábor / zkušenost z AZ']
    cms = jl(D / 'interesting_comments.jsonl')
    for c in cms:
        for x in c.get('reasons') or []:
            if x not in reasons:
                reasons.append(x)
    for x in reasons:
        ins(cur, 'reasons', {'reason': x})
    for i, c in enumerate(cms):
        ins(cur, 'comments', {'ord': i, **P(c, M.COMMENT, nested=('reasons', 'engagement', 'verification'),
                                            default_present=('reasons', 'engagement'))})
        for j, x in enumerate(c.get('reasons') or []):
            ins(cur, 'comment_reasons', {'comment_id': c['id'], 'ord': j, 'reason': x})
        if isinstance(c.get('engagement'), dict):
            ins(cur, 'comment_engagement', {'comment_id': c['id'], **P(c['engagement'], M.ENGAGEMENT)})
        vf = c.get('verification')
        if isinstance(vf, dict):
            ins(cur, 'verifications', {'comment_id': c['id'], **P(vf, M.VERIF, nested=('sources',))})
            for j, s in enumerate(vf.get('sources') or []):
                ins(cur, 'verification_sources', {'comment_id': c['id'], 'ord': j, **P(s, M.VSRC)})

    # events
    for i, e in enumerate(jd(D / 'events.json', []) or []):
        ins(cur, 'events', {'ord': i, **P(e, M.EVENT, nested=('replaced_ids',))})
        for j, x in enumerate(e.get('replaced_ids') or []):
            ins(cur, 'event_replaced_ids', {'event_id': e['id'], 'ord': j, 'replaced_id': x})

    # MOTD
    for p in sorted((D / 'motd').glob('????-??-??.json')):
        m = jd(p)
        if not isinstance(m, dict):
            continue
        ins(cur, 'motd', {'file_date': p.stem, **P(m, M.MOTD, nested=('items',), default_present=('items',))})
        for j, it in enumerate(m.get('items') or []):
            ins(cur, 'motd_items', {'file_date': p.stem, 'ord': j, **P(it, M.MOTD_ITEM)})
    for p in sorted((D / 'motd' / 'stats').glob('*.json')):
        for j, (k, v) in enumerate((jd(p, {}) or {}).items()):
            ins(cur, 'motd_stats', {'date': p.stem, 'key': k, 'ord': j, 'value_json': json.dumps(v, ensure_ascii=False)})

    # health
    for i, h in enumerate(jl(D / 'health_history.jsonl')):
        ins(cur, 'health_checks', {'check_id': i, **P(h, M.HEALTH, nested=('tls',), default_present=('tls',))})
        if isinstance(h.get('tls'), dict):
            ins(cur, 'tls_certs', {'check_id': i, **P(h['tls'], M.TLS)})

    # link status
    ls = jd(D / 'link_status.json', {}) or {}
    meta['link_status'] = {k: v for k, v in ls.items() if k != 'links'}
    meta['link_status_keys'] = list(ls)
    for i, (u, l) in enumerate((ls.get('links') or {}).items()):
        v = P(l, M.LINK, nested=('sources',), default_present=('sources',))
        v['url_key'] = u
        ins(cur, 'link_status', {'ord': i, **{k: v[k] for k in v if k != 'url_key'}} if v['url'] == u else
            {'ord': i, **{k: v[k] for k in v if k != 'url_key'}})
        for j, s in enumerate(l.get('sources') or []):
            ins(cur, 'link_status_sources', {'url': l.get('url') or u, 'ord': j, 'source': s})
    for i, h in enumerate(jl(D / 'link_history.jsonl')):
        ins(cur, 'link_history', {'id': i, **P(h, M.LINK_HIST)})

    # archive
    a = jd(D / 'archive_status.json', {}) or {}
    meta['archive_status'] = {k: v for k, v in a.items() if k != 'links'}
    meta['archive_status_keys'] = list(a)
    for i, (u, l) in enumerate((a.get('links') or {}).items()):
        ins(cur, 'archive_links', {'url': u, 'ord': i, **P(l, M.ARCH_LINK, nested=('wayback', 'archive_is'))})
        for svc in ('wayback', 'archive_is'):
            if isinstance(l.get(svc), dict):
                ins(cur, 'archive_snapshots', {'url': u, 'service': svc, **P(l[svc], M.SNAP)})
    for i, s in enumerate(jl(D / 'archive_submit_log.jsonl')):
        ins(cur, 'archive_submit_log', {'id': i, **P(s, M.SUBMIT)})

    # X
    files = [D / 'x_posts.json'] + sorted(D.glob('x_posts.json.bak*'), reverse=True)
    acc_id = 0
    for fi, f in enumerate(f for f in files if f.exists()):
        d = jd(f)
        if not isinstance(d, dict):
            continue
        ins(cur, 'x_post_files', {'file': f.name, 'ord': fi, **P(d, M.XFILE, nested=('accounts',),
                                                                  default_present=('accounts',))})
        for j, ac in enumerate(d.get('accounts') or []):
            ins(cur, 'x_accounts', {'account_id': acc_id, 'file': f.name, 'ord': j,
                                    **P(ac, M.XACC, nested=('posts',), default_present=('posts',))})
            for k, po in enumerate(ac.get('posts') or []):
                ins(cur, 'x_posts', {'account_id': acc_id, 'ord': k, **P(po, M.XPOST)})
            acc_id += 1
    xt = jd(D / 'x_tweet_index.json', {}) or {}
    for i, (tid, t) in enumerate(xt.items()):
        v = P(t, M.XTWEET)
        if v['id'] != tid:   # klíč slovníku se liší od id – uložit klíč, id do extra
            v = P(t, [c for c in M.XTWEET if c[0] != 'id'])
            v['id'] = tid
            ex = json.loads(v['extra_json'] or '{}'); ex.setdefault('x', {})
            ex['k'] = 1
            if 'id' in t: ex['x']['id'] = t['id']
            v['extra_json'] = json.dumps(ex, ensure_ascii=False)
        ins(cur, 'x_tweets', {'ord': i, **v})

    # další zdroje permalinků (zálohy captures, comments_inbox)
    hid = 0
    def hint(src, ch, text, url):
        nonlocal hid
        if text and url:
            ins(cur, 'permalink_hints', {'id': hid, 'source': src, 'channel_url': ch, 'text': text, 'url': url})
            hid += 1
    for cp in sorted(D.glob('captures.jsonl.bak*'), reverse=True):
        for r in jl(cp):
            if not r.get('url'):
                continue
            lp = r.get('last_post') if isinstance(r.get('last_post'), dict) else {}
            hint(cp.name, r['url'], r.get('last_post_title'), r.get('last_post_url'))
            hint(cp.name, r['url'], lp.get('title'), lp.get('url'))
            for rp in r.get('recent_posts') or []:
                if isinstance(rp, dict):
                    hint(cp.name, r['url'], rp.get('title') or rp.get('text'), rp.get('url') or rp.get('post_url'))
    for f in sorted((D / 'comments_inbox').glob('*.json')):
        rows = jd(f, [])
        rows = rows if isinstance(rows, list) else []
        for o in rows:
            if isinstance(o, dict):
                hint('comments_inbox/' + f.name, o.get('channel_url') or '', o.get('post_title_or_excerpt'), o.get('post_url'))

    for i, x in enumerate(jd(D / 'alt_platforms.json', []) or []):
        ins(cur, 'alt_platforms', {'ord': i, **P(x, M.ALT)})
    ua = jd(D / 'url_aliases.json', {}) or {}
    meta['url_aliases'] = {k: v for k, v in ua.items() if k.startswith('_')}
    meta['url_aliases_keys'] = list(ua)
    for i, (k, v) in enumerate((k, v) for k, v in ua.items() if not k.startswith('_')):
        ins(cur, 'url_aliases', {'old_url': k, 'ord': i, 'new_url': v})

    for p in sorted((D / 'feed_cache').glob('*.json')):
        f = jd(p, {}) or {}
        ins(cur, 'feeds', {'feed': p.stem, **P(f, M.FEED, nested=('items',), default_present=('items',))})
        for j, it in enumerate(f.get('items') or []):
            ins(cur, 'feed_items', {'feed': p.stem, 'ord': j, **P(it, M.FEED_ITEM, {'pubDate': 'pub_date'})})
    for p in sorted(D.glob('daily_run_*.json')):
        ins(cur, 'daily_runs', {'date': p.stem[len('daily_run_'):], 'data_json': p.read_text(encoding='utf-8')})

    sd = shots or (root / 'screenshots')
    if sd.exists():
        for p in sorted(sd.rglob('*')):
            if not p.is_file():
                continue
            rel = 'screenshots/' + p.relative_to(sd).as_posix()
            kind = ('comment' if rel.startswith('screenshots/comments/') else
                    'archive' if rel.startswith('screenshots/archive/') else
                    'tile' if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') else 'other')
            st = p.stat()
            ins(cur, 'screenshots', {'path': rel, 'kind': kind, 'bytes': st.st_size,
                                     'mtime': dt.datetime.fromtimestamp(int(st.st_mtime)).isoformat()})

    for k, v in meta.items():
        ins(cur, 'meta', {'key': k, 'value_json': json.dumps(v, ensure_ascii=False)})
    con.commit()
    con.execute('VACUUM')
    con.close()
    os.replace(tmp, db)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=os.environ.get('ACR_ROOT') or str(HERE.parent / 'pipeline'))
    ap.add_argument('--db', default=os.environ.get('ACR_DB') or str(HERE / 'mapa.sqlite'))
    ap.add_argument('--shots', default=os.environ.get('ACR_SHOTS'))
    a = ap.parse_args()
    build(Path(a.root).resolve(), Path(a.db).resolve(), Path(a.shots) if a.shots else None)
    con = sqlite3.connect(a.db)
    tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tabs:
        print(f'{t:26s} {con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]}')
    print('size', os.path.getsize(a.db))


if __name__ == '__main__':
    main()
