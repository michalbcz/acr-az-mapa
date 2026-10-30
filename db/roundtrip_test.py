#!/usr/bin/env python3
"""Ověří, že každý zdrojový soubor jde z DB zrekonstruovat beze ztráty (i pořadí klíčů)."""
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1]); D = root / 'data'
os.environ.setdefault('ACR_DB', sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).parent / 'mapa.sqlite'))
sys.path.insert(0, str(Path(__file__).parent))
import mapa_db as db
jl = lambda p: [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
jd = lambda p: json.loads(p.read_text(encoding='utf-8'))
S = lambda o: json.dumps(o, ensure_ascii=False)   # citlivé na pořadí klíčů
checks = {
 'captures.jsonl': (jl(D/'captures.jsonl'), db.captures()),
 'enrichment.json': (jd(D/'enrichment.json'), db.enrichment()),
 'updates_history.jsonl': (jl(D/'updates_history.jsonl'), db.updates_history()),
 'interesting_comments.jsonl': (jl(D/'interesting_comments.jsonl'), db.comments()),
 'events.json': (jd(D/'events.json'), db.events()),
 'motd/*.json': ([(p.stem, jd(p)) for p in sorted((D/'motd').glob('????-??-??.json'))], db.motd_files()),
 'health_history.jsonl': (jl(D/'health_history.jsonl'), db.health_history()),
 'link_status.json': (jd(D/'link_status.json'), db.link_status()),
 'link_history.jsonl': (jl(D/'link_history.jsonl'), db.link_history()),
 'archive_status.json': (jd(D/'archive_status.json'), db.archive_status()),
 'archive_submit_log.jsonl': (jl(D/'archive_submit_log.jsonl'), db.archive_submit_log()),
 'x_posts.json': ([('x_posts.json', jd(D/'x_posts.json'))], db.x_post_files()),
 'x_tweet_index.json': (jd(D/'x_tweet_index.json'), db.x_tweet_index()),
 'alt_platforms.json': (jd(D/'alt_platforms.json'), db.alt_platforms()),
 'url_aliases.json': (jd(D/'url_aliases.json'), db.url_aliases()),
 'urls.json': (jd(root/'urls.json'), db.watched_urls()),
 'feed_cache/czdefence.json': (jd(D/'feed_cache'/'czdefence.json'), db.feed('czdefence')),
}
bad = 0
for k, (a, b) in checks.items():
    ok = S(a) == S(b)
    if not ok:
        bad += 1
        if isinstance(a, list):
            for i, (x, y) in enumerate(zip(a, b)):
                if S(x) != S(y):
                    print('  first diff', i, S(x)[:300], '\n          ', S(y)[:300]); break
            if len(a) != len(b): print('  len', len(a), len(b))
        elif isinstance(a, dict):
            for kk in a:
                if S(a.get(kk)) != S(b.get(kk)):
                    print('  first diff', kk, S(a.get(kk))[:300], '\n          ', S(b.get(kk))[:300]); break
            if list(a) != list(b): print('  keys', list(a)[:5], list(b)[:5])
    print(f'{"OK  " if ok else "FAIL"} {k}')
sys.exit(1 if bad else 0)
