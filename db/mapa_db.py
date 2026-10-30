"""Čtecí vrstva: rekonstruuje datové struktury mapy z db/mapa.sqlite.

Vrací přesně stejné objekty, jaké dříve builder načítal z JSON/JSONL souborů.
Cesta k DB: env ACR_DB, jinak <repo>/db/mapa.sqlite.
"""
import json, os, sqlite3, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from codec import unpack  # noqa: E402
import model as M  # noqa: E402

_CON = None


def db_path():
    return Path(os.environ.get('ACR_DB') or HERE / 'mapa.sqlite')


def con():
    global _CON
    if _CON is None:
        p = db_path()
        if not p.exists():
            raise FileNotFoundError(f'{p} neexistuje – spusťte python3 db/import_data.py')
        _CON = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        _CON.row_factory = sqlite3.Row
    return _CON


def q(sql, *a):
    return [dict(r) for r in con().execute(sql, a)]


def _group(sql, key):
    out = {}
    for r in q(sql):
        out.setdefault(r.pop(key), []).append(r)
    return out


def _ren(r, a, b):
    r[a] = r.pop(b); return r


def _meta(k, default=None):
    r = q('SELECT value_json FROM meta WHERE key=?', k)
    return json.loads(r[0]['value_json']) if r else default


def captures():
    lp = {r['capture_id']: r for r in q('SELECT * FROM capture_last_post')}
    hl = _group('SELECT * FROM capture_hidden_links ORDER BY capture_id, ord', 'capture_id')
    rp = _group('SELECT * FROM capture_recent_posts ORDER BY capture_id, ord', 'capture_id')
    out = []
    for r in q('SELECT * FROM captures ORDER BY capture_id'):
        i = r['capture_id']
        nested = {'hidden_links': [unpack(x, M.HIDDEN_LINK, x['extra_json']) for x in hl.get(i, [])],
                  'recent_posts': [unpack(x, M.RECENT_POST, x['extra_json']) for x in rp.get(i, [])],
                  'last_post': unpack(lp[i], M.LAST_POST, lp[i]['extra_json']) if i in lp else None}
        out.append(unpack(r, M.CAPTURE, r['extra_json'], nested, M.CAPTURE_ORDER))
    return out


def enrichment():
    au = _group('SELECT url, admin_url FROM enrichment_admin_urls ORDER BY url, ord', 'url')
    ac = _group('SELECT * FROM enrichment_admin_checks ORDER BY url, ord', 'url')
    npg = _group('SELECT * FROM enrichment_news_pages ORDER BY url, ord', 'url')
    out = {}
    for r in q('SELECT * FROM enrichment ORDER BY ord'):
        u = r['url']
        nested = {'admin_urls': [x['admin_url'] for x in au.get(u, [])],
                  'admin_checks': [unpack(_ren(x, 'url', 'check_url'), M.ADMIN_CHECK, x['extra_json']) for x in ac.get(u, [])],
                  'news_pages': [unpack(_ren(x, 'url', 'page_url'), M.NEWS_PAGE, x['extra_json']) for x in npg.get(u, [])]}
        out[u] = unpack(r, M.ENRICH, r['extra_json'], nested, M.ENRICH_ORDER, M.ENRICH_NESTED)
    return out


def updates_history():
    uu = _group('SELECT update_ord, url FROM update_urls ORDER BY update_ord, ord', 'update_ord')
    return [unpack(r, M.UPDATE, r['extra_json'], {'urls': [x['url'] for x in uu.get(r['ord'], [])]},
                   M.UPDATE_ORDER, ('urls',)) for r in q('SELECT * FROM updates ORDER BY ord')]


def comments():
    rs = _group('SELECT comment_id, reason FROM comment_reasons ORDER BY comment_id, ord', 'comment_id')
    eg = {r['comment_id']: r for r in q('SELECT * FROM comment_engagement')}
    vf = {r['comment_id']: r for r in q('SELECT * FROM verifications')}
    vs = _group('SELECT * FROM verification_sources ORDER BY comment_id, ord', 'comment_id')
    out = []
    for r in q('SELECT * FROM comments ORDER BY ord'):
        i = r['id']
        v = None
        if i in vf:
            v = unpack(vf[i], M.VERIF, vf[i]['extra_json'],
                       {'sources': [unpack(x, M.VSRC, x['extra_json']) for x in vs.get(i, [])]}, M.VERIF_ORDER)
        nested = {'reasons': [x['reason'] for x in rs.get(i, [])],
                  'engagement': unpack(eg[i], M.ENGAGEMENT, eg[i]['extra_json']) if i in eg else None,
                  'verification': v}
        out.append(unpack(r, M.COMMENT, r['extra_json'], nested, M.COMMENT_ORDER, ('reasons', 'engagement')))
    return out


def events():
    rid = _group('SELECT event_id, replaced_id FROM event_replaced_ids ORDER BY event_id, ord', 'event_id')
    return [unpack(r, M.EVENT, r['extra_json'], {'replaced_ids': [x['replaced_id'] for x in rid.get(r['id'], [])]})
            for r in q('SELECT * FROM events ORDER BY ord')]


def motd_files():
    """[(file_date, dict)] vzestupně dle data souboru."""
    it = _group('SELECT * FROM motd_items ORDER BY file_date, ord', 'file_date')
    return [(r['file_date'], unpack(r, M.MOTD, r['extra_json'],
                                    {'items': [unpack(x, M.MOTD_ITEM, x['extra_json']) for x in it.get(r['file_date'], [])]},
                                    None, ('items',)))
            for r in q('SELECT * FROM motd ORDER BY file_date')]


def health_history():
    tl = {r['check_id']: r for r in q('SELECT * FROM tls_certs')}
    return [unpack(r, M.HEALTH, r['extra_json'],
                   {'tls': unpack(tl[r['check_id']], M.TLS, tl[r['check_id']]['extra_json']) if r['check_id'] in tl else None},
                   None, ('tls',))
            for r in q('SELECT * FROM health_checks ORDER BY check_id')]


def link_status():
    src = _group('SELECT url, source FROM link_status_sources ORDER BY url, ord', 'url')
    links = {r['url']: unpack(r, M.LINK, r['extra_json'], {'sources': [x['source'] for x in src.get(r['url'], [])]},
                              M.LINK_ORDER, ('sources',))
             for r in q('SELECT * FROM link_status ORDER BY ord')}
    m = _meta('link_status', {})
    keys = _meta('link_status_keys', list(m) + ['links'])
    if not keys:
        return {}
    return {k: (links if k == 'links' else m[k]) for k in keys}


def link_history():
    return [unpack(r, M.LINK_HIST, r['extra_json']) for r in q('SELECT * FROM link_history ORDER BY id')]


def archive_status():
    sn = {}
    for r in q('SELECT * FROM archive_snapshots'):
        sn[(r['url'], r['service'])] = unpack(r, M.SNAP, r['extra_json'])
    links = {r['url']: unpack(r, M.ARCH_LINK, r['extra_json'],
                              {'wayback': sn.get((r['url'], 'wayback')), 'archive_is': sn.get((r['url'], 'archive_is'))},
                              M.ARCH_LINK_ORDER)
             for r in q('SELECT * FROM archive_links ORDER BY ord')}
    m = _meta('archive_status', {})
    keys = _meta('archive_status_keys', [])
    return {k: (links if k == 'links' else m[k]) for k in keys}


def archive_submit_log():
    return [unpack(r, M.SUBMIT, r['extra_json']) for r in q('SELECT * FROM archive_submit_log ORDER BY id')]


def x_post_files():
    """[(název souboru, dict)] v pořadí builderu (hlavní soubor, pak zálohy)."""
    po = _group('SELECT * FROM x_posts ORDER BY account_id, ord', 'account_id')
    acc = _group('SELECT * FROM x_accounts ORDER BY file, ord', 'file')
    out = []
    for f in q('SELECT * FROM x_post_files ORDER BY ord'):
        accounts = [unpack(a, M.XACC, a['extra_json'],
                           {'posts': [unpack(p, M.XPOST, p['extra_json']) for p in po.get(a['account_id'], [])]},
                           None, ('posts',)) for a in acc.get(f['file'], [])]
        out.append((f['file'], unpack(f, M.XFILE, f['extra_json'], {'accounts': accounts}, None, ('accounts',))))
    return out


def x_tweet_index():
    out = {}
    for r in q('SELECT * FROM x_tweets ORDER BY ord'):
        ex = json.loads(r['extra_json'] or '{}')
        if ex.pop('k', None):
            out[r['id']] = unpack(r, [c for c in M.XTWEET if c[0] != 'id'], json.dumps(ex) if ex else None)
        else:
            out[r['id']] = unpack(r, M.XTWEET, r['extra_json'])
    return out


def permalink_hints():
    return q('SELECT source, channel_url, text, url FROM permalink_hints ORDER BY id')


def alt_platforms():
    return [unpack(r, M.ALT, r['extra_json']) for r in q('SELECT * FROM alt_platforms ORDER BY ord')]


def url_aliases():
    """Celý obsah url_aliases.json včetně klíčů začínajících '_'."""
    m = _meta('url_aliases', {})
    al = {r['old_url']: r['new_url'] for r in q('SELECT * FROM url_aliases ORDER BY ord')}
    keys = _meta('url_aliases_keys', list(m) + list(al))
    return {k: (m[k] if k in m else al[k]) for k in keys}


def watched_urls():
    out = {}
    for g in _meta('urls_groups', []):
        out[g] = [r['url'] for r in q('SELECT url FROM watched_urls WHERE group_name=? ORDER BY ord', g)]
    return out


def feed(name):
    f = q('SELECT * FROM feeds WHERE feed=?', name)
    if not f:
        return None
    items = [unpack(_ren(x, 'pubDate', 'pub_date'), M.FEED_ITEM, x['extra_json'])
             for x in q('SELECT * FROM feed_items WHERE feed=? ORDER BY ord', name)]
    return unpack(f[0], M.FEED, f[0]['extra_json'], {'items': items}, None, ('items',))
