#!/usr/bin/env python3
"""Build categorized, navigable ACR/AZ channel map report."""
from pathlib import Path
from PIL import Image
import html as H, json, shutil, re, sys, importlib.util
from datetime import datetime
from urllib.parse import urlparse

import os
ROOT = Path(os.environ.get('ACR_ROOT') or Path(__file__).resolve().parent)
OUT = ROOT / 'site_v2'

CATS = [
    ('oficialni', 'Oficiální weby MO / AČR', 'Centrální weby ministerstva a armády'),
    ('kvv', 'KVV — krajská velitelství', 'Aktivní záloha u KVV'),
    ('jednotky', 'Jednotky AZ / útvarové weby', 'Vlastní weby praporů a rot'),
    ('dobrovolnici', 'Dobrovolníci a partneři', 'ARDOS, POKOS, SVZ, ČsOL, RESCO…'),
    ('facebook', 'Facebook stránky', 'Veřejné stránky AČR / AZ'),
    ('fb_groups', 'FB skupiny', 'Veřejné i soukromé (jen evidence)'),
    ('instagram', 'Instagram', 'Oficiální a AZ účty'),
    ('youtube', 'YouTube', 'TV Armáda, AZTV'),
    ('x', 'X (Twitter)', 'ArmadaCR, ObranaTweetuje'),
    ('linkedin', 'LinkedIn', 'Oficiální firemní stránky MO / AČR'),
    ('fora', 'Fóra a diskuze', 'Neoficiální vojenská fóra'),
    ('odbornici', 'Odborná média a analytici', 'Defence média, analytici, specialisté'),
    ('alt', 'Jiné platformy', 'Discord milsim, WhatsApp (jen evidence), …'),
]


def parse_sort_date(s):
    """Best-effort parse of last_post_date into YYYY-MM-DD for sorting."""
    if not s:
        return ''
    from datetime import datetime, timedelta
    from email.utils import parsedate_to_datetime
    s = str(s).strip()
    # ISO prefix (also inside longer strings)
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return m.group(0)
    # Czech d.m.yyyy
    m = re.search(r'(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})', s)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    # Twitter / email date: Fri Sep 11 13:03:39 +0000 2026
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    # relative hours: "1h", "9h", "14h ago", "2 hours ago"
    m = re.search(r'(\d+)\s*h(?:ours?)?(?:\s*ago)?\b', s, re.I)
    if m and re.search(r'\d+\s*h', s, re.I):
        return (datetime.now() - timedelta(hours=int(m.group(1)))).strftime('%Y-%m-%d')
    m = re.search(r'(\d+)\s*m(?:in(?:ute)?s?)?(?:\s*ago)?\b', s, re.I)
    if m and re.match(r'^\d+\s*m', s, re.I):
        return (datetime.now() - timedelta(minutes=int(m.group(1)))).strftime('%Y-%m-%d')
    m = re.search(r'(\d+)\s*days?\s*ago', s, re.I)
    if m:
        return (datetime.now() - timedelta(days=int(m.group(1)))).strftime('%Y-%m-%d')
    # English Month D, YYYY or Month D
    months = {n: i for i, n in enumerate(
        ['january','february','march','april','may','june','july','august','september','october','november','december'], 1)}
    m = re.search(
        r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:,?\s*(\d{4}))?',
        s, re.I)
    if m:
        mon = months[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        try:
            return datetime(year, mon, day).strftime('%Y-%m-%d')
        except Exception:
            return ''
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(
                s[:10] if fmt == '%Y-%m-%d' else s, fmt
            ).strftime('%Y-%m-%d')
        except Exception:
            continue
    try:
        if 'T' in s:
            return datetime.fromisoformat(s.replace('Z', '+00:00')).strftime('%Y-%m-%d')
    except Exception:
        pass
    return ''


def _cz_plural(n, one, few, many):
    n = abs(int(n))
    if n == 1:
        return f'{n} {one}'
    if 2 <= n <= 4:
        return f'{n} {few}'
    return f'{n} {many}'


_REL_RAW_RE = re.compile(
    r'\bpřed\s+(\d|chvíl|minut|hodin|dnem|týdnem|měsícem|rokem)|\bago\b|^\s*\d+\s*(h|m|d|w|min|hod|t)\.?\s*$'
    r'|\b(včera|dnes|právě teď)\b|\bvor\s+\d', re.I)


def is_rel_raw(raw):
    """True for capture-time relative strings ('před 2 týdny', '9h', '3 days ago')."""
    return bool(_REL_RAW_RE.search(str(raw or '')))


def strip_rel_raw(raw):
    """Never publish a stale relative string: relative raw → ''."""
    return '' if is_rel_raw(raw) else (raw or '')


def abs_cz(iso):
    """Absolute Czech date/time in Europe/Prague: '11. 10. 2026 8:34' (date-only → '11. 10. 2026').
    Relative ('před …') text is rendered ONLY client-side from data-iso (refreshRelTimes)."""
    from datetime import timezone
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('Europe/Prague')
    except Exception:
        tz = None
    iso = str(iso or '').strip()
    if not iso:
        return ''
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', iso)
    if m:
        return f'{int(m.group(3))}. {int(m.group(2))}. {m.group(1)}'
    try:
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except Exception:
        return iso[:16]
    if dt.tzinfo is not None and tz is not None:
        dt = dt.astimezone(tz)
    return f'{dt.day}. {dt.month}. {dt.year} {dt.hour}:{dt.minute:02d}'


def reltime_tag(iso, text=None, title=None, mode=''):
    """<time class="reltime" data-iso=…>ABSOLUTE</time>; client JS swaps the text for relative."""
    iso = str(iso or '')
    absv = text if text is not None else (abs_cz(iso) or '—')
    t = title or absv
    md = f' data-rel="{H.escape(mode, quote=True)}"' if mode else ''
    ie = H.escape(iso, quote=True)
    return (f'<time class="reltime" datetime="{ie}" data-iso="{ie}"{md} '
            f'title="{H.escape(t, quote=True)}">{H.escape(absv)}</time>')


def display_date(raw, sort_key):
    """Static calendar/sort date only — relative 'ago' is filled in by client JS."""
    base = sort_key or (str(raw)[:32] if raw else '')
    return base or '—'


def iso_for_client(raw, sort_key):
    """Best ISO-ish timestamp for data-iso (client pretty-time). Prefer full raw."""
    from datetime import datetime, timedelta
    from email.utils import parsedate_to_datetime
    raw = str(raw or '').strip()
    # Twitter / RFC-ish
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt.isoformat()
    except Exception:
        pass
    # relative hours/minutes alone → approximate ISO now
    m = re.search(r'^(\d+)\s*h(?:ours?)?(?:\s*ago)?$', raw, re.I)
    if m:
        return (datetime.now().astimezone() - timedelta(hours=int(m.group(1)))).isoformat()
    m = re.search(r'^(\d+)\s*m(?:in(?:ute)?s?)?(?:\s*ago)?$', raw, re.I)
    if m:
        return (datetime.now().astimezone() - timedelta(minutes=int(m.group(1)))).isoformat()
    # Explicit ISO date optionally with time (do not treat "UTC" as ISO T)
    m = re.search(
        r'(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:?\d{2})?)?',
        raw,
    )
    if m:
        if m.group(2):
            return f'{m.group(1)}T{m.group(2)}{m.group(3) or ""}'
        return m.group(1)
    # Czech d.m.yyyy
    m = re.search(r'(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})', raw)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    # relative hours inside longer strings already handled via parse_sort_date day
    sk = parse_sort_date(raw) or (sort_key or '')
    return sk


def reltime_html(raw, sort_key, prefix=''):
    iso = iso_for_client(raw, sort_key)
    if not iso:
        base = display_date(raw, sort_key)
        return f'{prefix}{H.escape(base)}' if base != '—' else (f'{prefix}—' if prefix else '—')
    absv = abs_cz(iso) or display_date(raw, sort_key)
    title = absv
    if raw and not is_rel_raw(raw) and str(raw).strip() not in (iso, absv):
        title = f'{absv} · zdroj: {raw}'
    return prefix + reltime_tag(iso, absv, title)


SOURCE_NOTE = {
    'html_scan': 'nalezeno v HTML',
    'aria_label_hidden': 'skrytý odkaz (aria-label)',
    'nav_link': 'navigace',
    'og': 'Open Graph',
    'syndication': 'Twitter syndication API',
    'legacy': 'legacy odkaz',
}


def categorize(url: str) -> str:
    u = (url or '').lower()
    host = urlparse(url or '').netloc.lower().replace('www.', '')
    path = urlparse(url or '').path.lower().strip('/')

    # Expert / defence media & analysts (webs + their social) → odbornici
    # Checked before generic social buckets so FB/IG/X/LI expert accounts land here.
    EXPERT_WEB_HOSTS = {
        'czdefence.cz', 'armadninoviny.cz', 'natoaktual.cz', 'securityoutlines.cz',
        'valka.cz', 'jirivojacek.cz', 'lvisingr.czweb.org', 'iir.cz', 'aobp.cz',
        'magnetpress.online',
    }
    EXPERT_FB = {
        '91skupina', 'czdefence', 'armadninoviny', 'natoaktual',
        'securityoutlines', 'casopisatm',
    }
    EXPERT_IG = {'91skupina', 'czdefence', 'security_outlines', 'ivo.zelinka'}
    EXPERT_X = {'ivozelinka', 'carramba66', 'czdefence', 'natoaktual', 'danny_elmayor'}
    EXPERT_LI = {'ivo-zelinka-5240611a'}

    if host in EXPERT_WEB_HOSTS or any(host.endswith('.' + h) for h in EXPERT_WEB_HOSTS):
        return 'odbornici'
    if 'facebook.com' in host:
        if '/groups/' in u:
            return 'fb_groups'
        slug = path.split('/')[0] if path else ''
        if slug in EXPERT_FB:
            return 'odbornici'
        return 'facebook'
    if 'instagram.com' in host:
        slug = path.split('/')[0] if path else ''
        if slug in EXPERT_IG:
            return 'odbornici'
        return 'instagram'
    if 'youtube.com' in host or 'youtu.be' in host:
        return 'youtube'
    if host in ('x.com', 'twitter.com') or host.endswith('.x.com'):
        slug = path.split('/')[0] if path else ''
        if slug in EXPERT_X:
            return 'odbornici'
        return 'x'
    if 'linkedin.com' in host:
        slug = path.rstrip('/').split('/')[-1] if path else ''
        if slug in EXPERT_LI:
            return 'odbornici'
        return 'linkedin'
    if host in ('portal.ardos.org', 'groups.io', 'resco-cr.cz', 'svz-cr.cz', 'csol.cz') or 'rkacr' in host or 'radioklub' in host or 'pokos' in host:
        return 'dobrovolnici'
    if host.startswith('kvv') or 'kvvusti' in host or 'kvvcb' in host:
        return 'kvv'
    if 'mpsfar.cz' in host and 'kontakt' in u:
        return 'alt'
    if 'discord' in host or 'disdex.io' in host:
        return 'alt'
    if host in ('forum.vojsko.net', 'rentaacr.cz', 'zbranekvalitne.cz'):
        return 'fora'
    unit_hosts = (
        '111prapor.cz', 'petatricatnici.eu', 'mpsfar.cz', 'az44lmopr.cz',
        '43vysadkovypluk.mo.gov.cz', '14plogp.mo.gov.cz', 'akis.mo.gov.cz', '7mb.mo.gov.cz',
    )
    if host in unit_hosts or any(host.endswith(h) for h in unit_hosts):
        return 'jednotky'
    # central official (incl. doarmady 91 skupina — keep oficialni)
    if host in (
        'aktivnizaloha.mo.gov.cz', 'doarmady.mo.gov.cz', 'veters.mo.gov.cz',
        'acr.mo.gov.cz', 'mocr.mo.gov.cz', 'mo.gov.cz',
    ):
        return 'oficialni'
    # leftover mo.gov.cz unit-ish
    if host.endswith('.mo.gov.cz'):
        return 'jednotky'
    if '/forum' in u and host.endswith('.mo.gov.cz') is False and 'petatricatnici' not in host:
        if host in ('forum.vojsko.net', 'rentaacr.cz', 'zbranekvalitne.cz') or 'forum' in host:
            return 'fora'
    return 'dobrovolnici'



# Explicit official social handles (path segment / @handle, lowercased)
OFFICIAL_FB = {
    'armadaceskerepubliky', 'ministerstvoobranyceskerepubliky', 'doarmady.cz',
    'aktivnizalohy', '43vysadkovypluk',
}
OFFICIAL_IG = {'armadaceskerepubliky', 'do_armady'}
OFFICIAL_YT = {'tvarmycz', '@tvarmycz'}
OFFICIAL_X = {'armadacr', 'obranatweetuje'}


def officiality(url: str):
    """Return (key, label): oficiální armádní/MO zdroj vs neoficiální."""
    u = (url or '').lower()
    host = urlparse(url or '').netloc.lower().replace('www.', '')
    path = urlparse(url or '').path.lower().strip('/')

    # All MO / AČR websites
    if host.endswith('.mo.gov.cz') or host == 'mo.gov.cz':
        return 'oficialni', 'Oficiální'

    if 'facebook.com' in host:
        if '/groups/' in u:
            return 'neoficialni', 'Neoficiální'
        if 'profile.php' in path:
            # numeric profiles are community unless proven otherwise
            return 'neoficialni', 'Neoficiální'
        slug = path.split('/')[0] if path else ''
        if slug in OFFICIAL_FB:
            return 'oficialni', 'Oficiální'
        return 'neoficialni', 'Neoficiální'

    if 'instagram.com' in host:
        slug = path.split('/')[0] if path else ''
        if slug in OFFICIAL_IG:
            return 'oficialni', 'Oficiální'
        return 'neoficialni', 'Neoficiální'

    if 'youtube.com' in host or 'youtu.be' in host:
        # @handle or /channel/
        m = re.search(r'/@([^/?#]+)', u) or re.search(r'youtube\.com/([^/?#]+)', u)
        handle = (m.group(1) if m else path).lower()
        if handle in OFFICIAL_YT or handle.lstrip('@') in {x.lstrip('@') for x in OFFICIAL_YT}:
            return 'oficialni', 'Oficiální'
        return 'neoficialni', 'Neoficiální'

    if 'linkedin.com' in host:
        slug = path.rstrip('/').split('/')[-1] if path else ''
        if slug in ('ministerstvoobrany', 'czecharmy', 'czech-army'):
            return 'oficialni', 'Oficiální'
        return 'neoficialni', 'Neoficiální'

    if host in ('x.com', 'twitter.com') or host.endswith('.x.com'):
        slug = path.split('/')[0] if path else ''
        if slug in OFFICIAL_X:
            return 'oficialni', 'Oficiální'
        return 'neoficialni', 'Neoficiální'

    # Independent unit / volunteer sites
    return 'neoficialni', 'Neoficiální'


def short_label(url: str) -> str:
    p = urlparse(url or '')
    path = (p.path or '/').rstrip('/') or '/'
    host = p.netloc.replace('www.', '')
    if 'facebook.com' in host:
        if '/groups/' in path:
            gid = path.strip('/').split('/')[-1][:20]
            return 'FB skupina ' + gid
        if 'profile.php' in path:
            return 'FB ' + (p.query.split('id=')[-1][:12] if 'id=' in p.query else 'profil')
        return 'FB /' + path.strip('/').split('/')[0][:40]
    if 'instagram.com' in host:
        return 'IG @' + path.strip('/').split('/')[0]
    if 'youtube.com' in host:
        return 'YT ' + path.strip('/')
    if host in ('x.com', 'twitter.com'):
        return '@' + path.strip('/').split('/')[0]
    if 'linkedin.com' in host:
        segs = [s for s in path.split('/') if s]
        return 'LI /' + (segs[-1][:40] if segs else host)
    if path in ('', '/'):
        return host
    # last meaningful segment
    segs = [s for s in path.split('/') if s]
    return f'{host}/…/{segs[-1][:36]}' if len(segs) > 1 else f'{host}/{segs[0][:40]}'


def verified_admin_checks(checks):
    """Admin endpoints verified by an HTTP response usable for the detail row/filter."""
    return [
        c for c in (checks or [])
        if str(c.get('status')).strip() in {'200', '401', '403'} and c.get('url')
    ]


def normalize_link(h):
    if isinstance(h, str):
        if h.startswith('{'):
            try:
                import ast
                h = ast.literal_eval(h)
            except Exception:
                return {'url': h, 'type': 'link', 'source': 'raw'}
        else:
            return {'url': h, 'type': 'link', 'source': 'raw'}
    return h if isinstance(h, dict) else {'url': str(h), 'type': 'link', 'source': 'raw'}


def find_shot(stem, url=''):
    c = list((ROOT / 'screenshots').glob(f'{stem}*'))
    if not c and 'x.com/ArmadaCR' in (url or ''):
        c = list((ROOT / 'screenshots').glob('x-ArmadaCR*'))
    if not c and 'ObranaTweetuje' in (url or ''):
        c = list((ROOT / 'screenshots').glob('x-ObranaTweetuje*'))
    if not c:
        c = list((ROOT / 'screenshots').glob(f'*{stem[:20]}*'))
    pngs = [x for x in c if x.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')]
    return (pngs or c)[0] if c else None



_SP = None


def _social_permalinks():
    """scripts/social_permalinks.py (permalinks of concrete social posts) or None."""
    global _SP
    if _SP is None:
        try:
            sys.path.insert(0, str(ROOT / 'scripts'))
            import social_permalinks as sp  # noqa: E402
            _SP = sp
        except Exception as ex:
            print(f'warn: social_permalinks unavailable: {ex}')
            _SP = False
    return _SP or None


def _is_social_permalink(u):
    sp = _social_permalinks()
    return bool(sp and sp.is_post_permalink(u or ''))


def _archive_module():
    script = ROOT / 'scripts' / 'archive_updates.py'
    if not script.exists():
        return None
    spec = importlib.util.spec_from_file_location('archive_updates', script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_url_aliases():
    ap = ROOT / 'data' / 'url_aliases.json'
    try:
        d = json.loads(ap.read_text(encoding='utf-8'))
        return {k: v for k, v in d.items() if not k.startswith('_')}
    except Exception:
        return {}


def _apply_url_aliases(rows):
    """Display-only: replace retired URLs (data/url_aliases.json) in history rows."""
    al = _load_url_aliases()
    if not al:
        return rows
    out = []
    for o in rows:
        o = dict(o)
        for k in ('url', 'post_url'):
            if o.get(k) in al:
                o[k] = al[o[k]]
        if isinstance(o.get('urls'), list):
            seen, nu = set(), []
            for u in o['urls']:
                u = al.get(u, u)
                if u not in seen:
                    seen.add(u); nu.append(u)
            o['urls'] = nu
        sl = o.get('source_label') or ''
        for a, b in al.items():
            if sl == a.split('://', 1)[-1]:
                o['source_label'] = b.split('://', 1)[-1]
        out.append(o)
    return out


def load_updates_history():
    """Load archived updates (newest first) for feed/histogram embed.

    Safety net: duplicates (same channel + normalized text) are collapsed here
    too, so feed + histogram + header count only unique posts.
    """
    hp = ROOT / 'data' / 'updates_history.jsonl'
    rows = []
    if not hp.exists():
        return rows
    raw = []
    for line in hp.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw.append(json.loads(line))
        except Exception:
            continue
    raw = _apply_url_aliases(raw)
    mod = _archive_module()
    if mod is not None and hasattr(mod, 'dedupe_rows'):
        n0 = len(raw)
        raw = mod.dedupe_rows(raw)
        if n0 != len(raw):
            print(f'history dedupe (build): {n0} -> {len(raw)}')
    for o in raw:
        url = o.get('url') or ''
        off_key, off_lab = officiality(url)
        rows.append({
            'id': o.get('id') or '',
            'url': url,
            'source_label': o.get('source_label') or short_label(url),
            'category': o.get('category') or categorize(url),
            'title': o.get('title') or '',
            'author': o.get('author') or '',
            'date_raw': strip_rel_raw(o.get('date_raw')),
            'date_iso': o.get('date_iso') or '',
            'captured_at': o.get('captured_at') or '',
            'first_seen': o.get('first_seen') or o.get('captured_at') or '',
            'last_seen': o.get('last_seen') or o.get('captured_at') or '',
            'urls': o.get('urls') or ([url] if url else []),
            'kind': o.get('kind') or 'last_post',
            'post_url': o.get('post_url') if _is_social_permalink(o.get('post_url')) else history_post_url(o),
            'officiality': off_key,
            'officiality_label': off_lab,
        })
    rows.sort(key=lambda x: x.get('date_iso') or '', reverse=True)
    return rows



# ---------------------------------------------------------------------------
# Original article / post URL for the "last update" of a channel.
# Only URLs that already exist in our data are used (captures last_post_url,
# enrichment news_pages items / last_item_url, updates_history, x_posts.json).
# Nothing is invented: no match → the tile/detail falls back to the channel URL.
# ---------------------------------------------------------------------------
def _url_norm(u):
    p = urlparse((u or '').strip())
    host = p.netloc.lower().replace('www.', '').replace('m.facebook.com', 'facebook.com')
    q = ('?' + p.query) if p.query else ''
    return host + (p.path or '').rstrip('/') + q


def _is_article_url(u, channel_url='', listing_urls=()):
    """True for a concrete post/article URL (not the channel/home/listing page)."""
    if not u or not str(u).startswith('http'):
        return False
    n = _url_norm(u)
    p = urlparse(u)
    if not (p.path or '').strip('/') and not p.query and not p.fragment:
        return False  # bare homepage
    if channel_url and n == _url_norm(channel_url):
        return False
    if any(n == _url_norm(x) for x in listing_urls if x):
        return False
    if p.path.lower().endswith(('/rss', '/rss2', '/feed', '.xml')):
        return False
    host = p.netloc.lower()
    segs = [x for x in (p.path or '').split('/') if x]
    if _is_social_permalink(u):  # x.com/…/status/<id>, FB/IG post or reel, YT watch?v=…
        return True
    if any(h in host for h in ('facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'youtube.com', 'tiktok.com', 'linkedin.com')):
        # social profile / page roots are channels, not posts
        if 'profile.php' in p.path or len(segs) <= 1:
            return False
        if segs[0] in ('groups', 'company', 'channel', 'c', 'school') and len(segs) <= 2:
            return False
    if n in _tracked_channel_urls():
        return False
    return True


_TRACKED = None


def _tracked_channel_urls():
    """Normalized URLs of all monitored channels + their news listing pages."""
    global _TRACKED
    if _TRACKED is None:
        _TRACKED = set()
        cp = ROOT / 'data' / 'captures.jsonl'
        if cp.exists():
            for line in cp.read_text(encoding='utf-8', errors='replace').splitlines():
                try:
                    u = json.loads(line).get('url')
                except Exception:
                    continue
                if u:
                    _TRACKED.add(_url_norm(u))
        ep = ROOT / 'data' / 'enrichment.json'
        if ep.exists():
            try:
                for u, e in json.loads(ep.read_text(encoding='utf-8')).items():
                    _TRACKED.add(_url_norm(u))
                    for n in e.get('news_pages') or []:
                        if n.get('url'):
                            _TRACKED.add(_url_norm(n['url']))
            except Exception:
                pass
    return _TRACKED


def _title_key(t):
    t = re.sub(r'\s+', ' ', str(t or '')).strip().lower()
    t = t.rstrip('…').rstrip('.').strip()
    return t[:120]


def _titles_match(a, b):
    a, b = _title_key(a), _title_key(b)
    if not a or not b:
        return False
    if a == b:
        return True
    # truncated captures ("… prefix") – require a reasonably long common prefix
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 25 and long_.startswith(short)


def load_x_post_index(mod):
    """(channel_key, title_key) → post URL from data/x_posts.json."""
    idx = {}
    if mod is None:
        return idx
    files = [ROOT / 'data' / 'x_posts.json'] + sorted((ROOT / 'data').glob('x_posts.json.bak*'), reverse=True)
    accounts = []
    for xp in files:
        if not xp.exists():
            continue
        try:
            accounts.extend(json.loads(xp.read_text(encoding='utf-8')).get('accounts') or [])
        except Exception:
            continue
    for acc in accounts:
        base = acc.get('url') or ('https://x.com/' + (acc.get('handle') or ''))
        ck = mod.channel_key(base)
        for po in acc.get('posts') or []:
            u = po.get('url')
            if not u:
                continue
            for t in (po.get('title'), po.get('text')):
                if t:
                    idx.setdefault((ck, _title_key(t)), u)
    return idx


def resolve_post_url(url, title, r, e, hist_index, x_index, mod):
    """Best known URL of the original article/post for a channel's last update.

    Returns (post_url or '', source tag)."""
    listing = [n.get('url') for n in (e.get('news_pages') or [])]

    def ok(u):
        return _is_article_url(u, url, listing)

    # 1) explicit capture field
    for k in ('last_post_url',):
        u = r.get(k) or e.get(k)
        if ok(u):
            return u, 'capture'
    lp = r.get('last_post') if isinstance(r.get('last_post'), dict) else {}
    if ok(lp.get('url')):
        return lp['url'], 'capture'
    if not title:
        return '', ''
    # 2) enrichment news pages (RSS / news listing items)
    for n in e.get('news_pages') or []:
        for itm in n.get('items') or []:
            if ok(itm.get('url')) and _titles_match(itm.get('title'), title):
                return itm['url'], 'feed'
        if ok(n.get('last_item_url')) and _titles_match(n.get('last_item'), title):
            return n['last_item_url'], 'news_page'
    if mod is None:
        return '', ''
    ck = mod.channel_key(url)
    # 3) updates history (news_item / x_post rows carry the article URL)
    for h in hist_index.get((ck, mod.norm_text(title))) or []:
        for u in [h.get('url')] + list(h.get('urls') or []):
            if ok(u):
                return u, 'history'
    if ck:
        for (hck, ht), hs in hist_index.items():
            if hck != ck or not _titles_match(ht, title):
                continue
            for h in hs:
                for u in [h.get('url')] + list(h.get('urls') or []):
                    if ok(u):
                        return u, 'history'
    # 4) X syndication cache
    u = x_index.get((ck, _title_key(title)))
    if not u:
        for (xck, xt), xu in x_index.items():
            if xck == ck and _titles_match(xt, title):
                u = xu
                break
    if ok(u):
        return u, 'x_posts'
    # 5) permalink index (X tweet cache, social captures, comment post URLs)
    sp = _social_permalinks()
    if sp and sp.is_social(url):
        u = _permalink_index().lookup(url, title)
        if ok(u):
            return u, 'permalinks'
    return '', ''


_PIDX = None


def _permalink_index():
    global _PIDX
    if _PIDX is None:
        _PIDX = _social_permalinks().PermalinkIndex()
    return _PIDX


def history_post_url(o):
    """Article URL for an updates_history row (prefers url, then urls[])."""
    cands = [o.get('url')] + list(o.get('urls') or [])
    cands = [u for u in cands if u]
    arts = [u for u in cands if _is_article_url(u)]
    if not arts:
        return ''
    # longest path = most specific (homepage / channel URLs drop out above)
    return o.get('url') if o.get('url') in arts else max(arts, key=lambda u: len(urlparse(u).path or ''))


SOCIAL_HOSTS = ('facebook.com', 'instagram.com', 'x.com', 'twitter.com')


def _history_index(updates_history, mod):
    idx = {}
    if mod is None:
        return idx
    for h in updates_history:
        for u in (h.get('urls') or [h.get('url')]):
            if not u:
                continue
            k = (mod.channel_key(u), mod.norm_text(h.get('title')))
            idx.setdefault(k, []).append(h)
    return idx


def _fix_relative_social_date(it, hist_index, mod):
    """Social tile with only a relative date (1h, 9h, 2d…): use the matching
    history entry's date (real published date or first-seen day) instead of
    resolving it against today. No match → keep current behaviour."""
    if mod is None:
        return
    host = urlparse(it.get('url') or '').netloc.lower()
    if not any(h in host for h in SOCIAL_HOSTS):
        return
    raw = it.get('date') or ''
    if not raw or not mod.is_relative_date(raw) or not it.get('title'):
        return
    cands = hist_index.get((mod.channel_key(it['url']), mod.norm_text(it['title']))) or []
    cands = [c for c in cands if c.get('date_iso')]
    if not cands:
        return
    absolute = [c for c in cands if not mod.is_relative_date(c.get('date_raw'))]
    best = min(absolute or cands, key=lambda c: c['date_iso'])
    it['date_relative_raw'] = raw
    it['date'] = best['date_iso']
    it['sort_date'] = best['date_iso'][:10]


COMMENT_REASONS = [
    'insider info / informace zevnitř',
    'kritika / stížnost',
    'otázka s odpovědí úřadu',
    'odpověď oficiálního účtu',
    'dezinformace / propaganda',
    'nová informace / fakt',
    'výrazný ohlas',
    'nábor / zkušenost z AZ',
    'bezpečnostní riziko (OPSEC)',
]
PLATFORM_LABEL = {'fb': 'Facebook', 'ig': 'Instagram', 'x': 'X', 'yt': 'YouTube'}


def load_interesting_comments(mod):
    """data/interesting_comments.jsonl (see scripts/add_interesting_comments.py), newest first."""
    cp = ROOT / 'data' / 'interesting_comments.jsonl'
    rows = []
    if not cp.exists():
        return rows
    for line in cp.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not (o.get('comment_text') or '').strip():
            continue
        # defensive: never show private-person names
        if o.get('author_type') not in ('official_account', 'public_figure'):
            o['author_type'] = 'anon'
            o['author_name'] = None
            o['author'] = 'uživatel'
        if not o.get('channel') and mod is not None:
            o['channel'] = mod.channel_key(o.get('channel_url') or o.get('post_url') or '')
        rows.append(o)
    rows.sort(key=lambda o: (o.get('comment_date_iso') or o.get('first_seen') or '', o.get('first_seen') or ''), reverse=True)
    return rows


def comments_label(n):
    if n == 1:
        return '1 zajímavý komentář'
    if 2 <= n <= 4:
        return f'{n} zajímavé komentáře'
    return f'{n} zajímavých komentářů'


VERIF_CLASS = {'potvrzeno': 'ok', 'částečně pravdivé': 'part', 'vyvráceno': 'bad', 'nepodloženo / nelze ověřit': 'unk'}
VERIF_ICON = {'ok': '✔', 'part': '◐', 'bad': '✖', 'unk': '?'}


def render_verification(v):
    """Ověření faktických tvrzení komentáře (jen u obvinění / sporných tvrzení)."""
    if not v or not v.get('status'):
        return ''
    st = v['status']
    cls = VERIF_CLASS.get(st, 'unk')
    iso = str(v.get('checked_at') or '')
    absv = abs_cz(iso) or iso
    when = reltime_tag(iso, absv, absv) if iso else ''
    who = H.escape(v.get('verifier') or 'Grok Bot')
    srcs = v.get('sources') or []
    src_html = ''.join(
        f'<li><a href="{H.escape(s.get("url") or "#", quote=True)}" target="_blank" rel="noopener">{H.escape(s.get("title") or s.get("url") or "")}</a></li>'
        for s in srcs)
    body = (f'<p>{H.escape(v.get("summary") or "")}</p>' if v.get('summary') else '') + (
        f'<p class="verif-src-h">Zdroje ({len(srcs)}):</p><ul class="verif-src">{src_html}</ul>' if srcs else '<p class="muted">Bez zdroje, který by tvrzení potvrdil či vyvrátil.</p>')
    return (
        f'<details class="verif verif-{cls}"><summary><span class="verif-badge">{VERIF_ICON[cls]} Ověření: {H.escape(st)}</span>'
        f' <span class="verif-meta">· {when} · ověřil {who}{" · " + str(len(srcs)) + " zdr." if srcs else ""}</span></summary>'
        f'{body}</details>'
    )


def render_comment_card(o):
    ch_url = o.get('channel_url') or ''
    off_key, off_lab = officiality(ch_url)
    plat = (o.get('platform') or '').lower()
    iso = str(o.get('comment_date_iso') or o.get('first_seen') or '')
    approx = bool(o.get('comment_date_approx'))
    iso_e = H.escape(iso)
    absv = abs_cz(iso) or '—'
    title = absv + (' · datum přibližné (dopočteno k prvnímu zachycení)' if approx else '')
    date_html = ('≈ ' if approx else '') + reltime_tag(iso, absv, title)
    reasons = o.get('reasons') or []
    chips = ''.join(f'<span class="rchip">{H.escape(r)}</span>' for r in reasons)
    if o.get('author_type') == 'official_account':
        _al = 'oficiální účet' if off_key == 'oficialni' else 'účet kanálu'
        auth = f'<span class="comm-auth off">{H.escape(o.get("author_name") or _al)} · {_al}</span>'
    elif o.get('author_type') == 'public_figure':
        auth = f'<span class="comm-auth pub">{H.escape(o.get("author_name") or "veřejná osoba")} · veřejná osoba</span>'
    else:
        auth = '<span class="comm-auth">uživatel (anonymizováno)</span>'
    text = str(o.get('comment_text') or '')
    long_ = len(text) > 420
    text_html = (
        f'<div class="comm-text{" collapsed" if long_ else ""}">{H.escape(text)}</div>'
        + ('<button type="button" class="comm-more" data-more>Zobrazit celý komentář</button>' if long_ else '')
    )
    eng = o.get('engagement') or {}
    eng_bits = []
    if eng.get('likes') not in (None, '', 0):
        eng_bits.append(f'👍 {H.escape(str(eng["likes"]))}')
    if eng.get('reactions') not in (None, '', 0):
        eng_bits.append(f'reakce {H.escape(str(eng["reactions"]))}')
    if eng.get('replies') not in (None, '', 0):
        eng_bits.append(f'💬 {H.escape(str(eng["replies"]))} odp.')
    eng_html = f'<span class="comm-eng">{" · ".join(eng_bits)}</span>' if eng_bits else ''
    post_t = o.get('post_title_or_excerpt') or 'příspěvek'
    links = [
        f'<a href="{H.escape(ch_url, quote=True)}" target="_blank" rel="noopener">{H.escape(short_label(ch_url) if ch_url else o.get("channel") or "")}</a>',
        f'Příspěvek: <a href="{H.escape(o.get("post_url") or "#", quote=True)}" target="_blank" rel="noopener">{H.escape(post_t[:140])}</a>',
    ]
    if o.get('comment_url'):
        links.append(f'<a href="{H.escape(o["comment_url"], quote=True)}" target="_blank" rel="noopener">Odkaz na komentář ↗</a>')
    shot = ''
    if o.get('screenshot'):
        shot = f'<a class="comm-shot" href="{H.escape(o["screenshot"], quote=True)}" target="_blank" rel="noopener">screenshot</a>'
    note = f'<p class="comm-note">{H.escape(o.get("reason_note") or "")}</p>' if o.get('reason_note') else ''
    return (
        f'<article class="comm-card" data-reasons="{H.escape("|".join(reasons), quote=True)}" '
        f'data-platform="{H.escape(plat)}" data-channel="{H.escape(o.get("channel") or "", quote=True)}">'
        f'<div class="feed-meta">{date_html}'
        f'<span class="badge badge-{off_key}">{H.escape(off_lab)}</span>'
        f'<span class="feed-kind k-komentar">komentář</span>'
        f'<span class="feed-kind">{H.escape(PLATFORM_LABEL.get(plat, plat.upper()))}</span>{auth}{eng_html}</div>'
        f'<div class="rchips">{chips}</div>'
        f'{text_html}{note}{render_verification(o.get("verification"))}'
        f'<div class="comm-links">{" · ".join(links)} {shot}</div>'
        f'</article>'
    )


def load_events_ui():
    """events_ui (Události: #cat-events, ICS feed) – data/events.json via scripts/update_events.py."""
    ep = ROOT / 'events_ui.py'
    spec = importlib.util.spec_from_file_location('events_ui', ep)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_monitoring():
    """monitoring_ui.Monitoring (health/SSL, link check, archive snapshots) – see scripts/check_sites_health.py etc."""
    mp = ROOT / 'monitoring_ui.py'
    spec = importlib.util.spec_from_file_location('monitoring_ui', mp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, mod.Monitoring(ROOT)


SOCIAL_HOST_KEYS = ('facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'youtube.com', 'youtu.be', 'linkedin.com')


def is_social_url(url):
    h = urlparse(url or '').netloc.lower()
    return any(h == k or h.endswith('.' + k) for k in SOCIAL_HOST_KEYS)


def run_archive_updates():
    """Seed/append updates_history.jsonl before building the site."""
    script = ROOT / 'scripts' / 'archive_updates.py'
    if not script.exists():
        print('warn: archive_updates.py missing')
        return
    spec = importlib.util.spec_from_file_location('archive_updates', script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stats = mod.run()
    print(f'archive: total={stats.get("total")} added={stats.get("added")}')


# ---------------------------------------------------------------- Zpráva dne (MOTD)
MOTD_CAT_CLASS = {'příspěvek': 'prispevek', 'komentář': 'komentar', 'dostupnost': 'dostupnost',
                  'SSL': 'ssl', 'odkazy': 'odkazy', 'archiv': 'archiv', 'jiné': 'jine'}
CZ_MONTHS = ['ledna', 'února', 'března', 'dubna', 'května', 'června', 'července', 'srpna',
             'září', 'října', 'listopadu', 'prosince']


def load_motd():
    """data/motd/YYYY-MM-DD.json (written via scripts/set_motd.py) → newest first."""
    out = []
    d = ROOT / 'data' / 'motd'
    if not d.exists():
        return out
    for p in sorted(d.glob('????-??-??.json'), reverse=True):
        try:
            m = json.loads(p.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'warn: motd {p.name}: {e}')
            continue
        if m.get('headline') and m.get('items'):
            m.setdefault('date', p.stem)
            out.append(m)
    return out


def _motd_date_label(date):
    try:
        y, mo, da = (int(x) for x in date.split('-'))
        return f'{da}. {CZ_MONTHS[mo - 1]} {y}'
    except Exception:
        return date or ''


def _motd_resolve(link, url_to_id, ch_to_id, chkey):
    """'#anchor' | 'card:<channel URL>' | http(s) URL → (href, external?)."""
    link = (link or '').strip()
    if link.startswith('card:'):
        u = link[5:]
        cid = url_to_id.get(u) or url_to_id.get(u.rstrip('/')) or url_to_id.get(u.rstrip('/') + '/')
        if not cid and chkey:
            cid = ch_to_id.get(chkey(u))
        return (f'#{cid}', False) if cid else (u, True)
    if link.startswith('#'):
        return link, False
    return link, link.startswith('http')


def render_motd_items(m, url_to_id, ch_to_id, chkey):
    lis = []
    for it in m.get('items') or []:
        cat = it.get('category') or 'jiné'
        imp = it.get('importance') or 'střední'
        href, ext = _motd_resolve(it.get('link'), url_to_id, ch_to_id, chkey)
        tgt = ' target="_blank" rel="noopener"' if ext else ''
        hi = imp == 'vysoká'
        mark = '<span class="motd-imp" title="Vysoká důležitost">❗ důležité</span>' if hi else ''
        src = it.get('source_url')
        src_html = (f' <a class="motd-src" href="{H.escape(src, quote=True)}" target="_blank" rel="noopener">zdroj ↗</a>'
                    if src and src != href else '')
        lis.append(
            f'<li class="motd-item{" hi" if hi else ""}">'
            f'<span class="mchip mc-{MOTD_CAT_CLASS.get(cat, "jine")}">{H.escape(cat)}</span>{mark} '
            f'<a class="motd-link" href="{H.escape(href, quote=True)}"{tgt}>{H.escape(it.get("text") or "")}</a>'
            f'{src_html}</li>'
        )
    return '<ul class="motd-items">' + ''.join(lis) + '</ul>'


def motd_time(m):
    iso = m.get('generated_at') or m.get('date') or ''
    return reltime_tag(iso, abs_cz(iso) or _motd_date_label(m.get('date')),
                       _motd_date_label(m.get('date')))


def render_motd(motds, items, chkey):
    """(box_html, archive_html) – empty strings when there is no MOTD."""
    if not motds:
        return '', ''
    url_to_id, ch_to_id = {}, {}
    for it in items:
        url_to_id.setdefault(it['url'], it['id'])
        if chkey and it.get('url'):
            ch_to_id.setdefault(chkey(it['url']), it['id'])
    m = motds[0]
    older = motds[1:]
    box = (
        '<aside class="motd" id="motd" aria-label="Zpráva dne">'
        '<details class="motd-d" id="motdDetails" open>'
        '<summary class="motd-sum"><span class="motd-kicker">📰 Zpráva dne</span> '
        f'<span class="motd-date">{motd_time(m)}</span>'
        f'<span class="motd-headline">{H.escape(m.get("headline") or "")}</span></summary>'
        f'{render_motd_items(m, url_to_id, ch_to_id, chkey)}'
        '<p class="motd-foot"><a href="#cat-motd" class="motd-archlink">Starší zprávy dne'
        f'{f" ({len(older)})" if older else ""} →</a></p>'
        '</details></aside>'
    )
    days = []
    for d in motds:
        days.append(
            f'<article class="motd-day" id="motd-{H.escape(d.get("date") or "", quote=True)}">'
            f'<h3>{motd_time(d)}</h3><p class="motd-headline">{H.escape(d.get("headline") or "")}</p>'
            f'{render_motd_items(d, url_to_id, ch_to_id, chkey)}</article>'
        )
    arch = (
        '<section class="motd-archive" id="cat-motd" hidden>'
        '<header class="cathead"><h2>Zprávy dne – archiv</h2>'
        f'<p class="muted">Denní shrnutí důležitých událostí na sledovaných kanálech · nejnovější nahoře · {len(motds)} dní</p>'
        '<p><button type="button" class="back" data-motd-back>← Zpět na přehled</button></p></header>'
        + (''.join(days) if days else '<p class="empty">Zatím žádné starší zprávy.</p>')
        + '</section>'
    )
    return box, arch


def main():
    run_archive_updates()

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / 'thumbs').mkdir(parents=True)
    (OUT / 'full').mkdir(parents=True)

    updates_history = load_updates_history()
    _amod = _archive_module()
    hist_index = _history_index(updates_history, _amod)
    x_index = load_x_post_index(_amod)
    # X history rows only store the profile URL → fill the status URL from the X cache
    for h in updates_history:
        if not h.get('post_url') and _amod is not None and h.get('title'):
            ck = _amod.channel_key(h.get('url') or '')
            xu = x_index.get((ck, _title_key(h['title'])))
            if not xu:
                xu = next((v for (k, t), v in x_index.items() if k == ck and _titles_match(t, h['title'])), '')
            if xu:
                h['post_url'] = xu
    # any social row still without a post permalink: known permalinks (never invented)
    _sp = _social_permalinks()
    if _sp:
        _sp.fill_post_urls([h for h in updates_history if not h.get('post_url')], _permalink_index())
    comments = load_interesting_comments(_amod)
    MUI, mon = load_monitoring()
    comments_by_channel = {}
    for o in comments:
        comments_by_channel[o.get('channel') or ''] = comments_by_channel.get(o.get('channel') or '', 0) + 1
    (OUT / 'interesting_comments.json').write_text(json.dumps([{**c, 'comment_date_raw': strip_rel_raw(c.get('comment_date_raw'))} for c in comments], ensure_ascii=False), encoding='utf-8')
    (OUT / 'updates_history.json').write_text(
        json.dumps(updates_history, ensure_ascii=False), encoding='utf-8'
    )
    # comment screenshots referenced by comments -> site_v2/screenshots/comments/ (blurred viewer identity; since 2026-10-27)
    _n_cs = 0
    for c in comments:
        rel = (c.get('screenshot') or '').strip().lstrip('/')
        if not rel.startswith('screenshots/comments/') or '..' in rel:
            continue
        src = ROOT / rel
        if src.is_file():
            dst = OUT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            _n_cs += 1
        else:
            print(f'WARN: comment screenshot missing: {rel}')
    print(f'comment screenshots copied: {_n_cs}')

    rows = []
    for line in (ROOT / 'data' / 'captures.jsonl').read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    by = {}
    for r in rows:
        by[r.get('url')] = r
    rows = list(by.values())

    enrich = {}
    ep = ROOT / 'data' / 'enrichment.json'
    if ep.exists():
        enrich = json.loads(ep.read_text())

    # prepare images + card data
    items = []
    for i, r in enumerate(rows, 1):
        url = r.get('url') or ''
        stem = Path(r.get('screenshot_path') or r.get('slug') or 'x').stem
        shot = find_shot(stem, url)
        thumb_rel = full_rel = None
        if shot and shot.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
            im = Image.open(shot).convert('RGB')
            w, h = im.size
            full = im.copy()
            if w > 1400:
                full = full.resize((1400, max(1, int(h * 1400 / w))), Image.Resampling.LANCZOS)
            name = f'{i:03d}.jpg'
            full.save(OUT / 'full' / name, 'JPEG', quality=82, optimize=True)
            full_rel = f'full/{name}'
            th = im.resize((420, max(1, int(h * 420 / w))), Image.Resampling.LANCZOS)
            th.save(OUT / 'thumbs' / name, 'JPEG', quality=52, optimize=True)
            thumb_rel = f'thumbs/{name}'

        e = enrich.get(url, {})
        cat = categorize(url)
        off_key, off_lab = officiality(url)
        items.append({
            'id': f'c{i}',
            'cat': cat,
            'url': url,
            'label': short_label(url),
            'host': urlparse(url).netloc.replace('www.', ''),
            'status': str(r.get('status') or ''),
            'title': r.get('last_post_title') or e.get('last_post_title') or '',
            'post_text': (r.get('last_post_text') or r.get('last_post_body') or r.get('last_post_content')
                         or e.get('last_post_text') or e.get('last_post_body') or e.get('last_post_content')
                         or r.get('last_post_title') or e.get('last_post_title') or ''),
            'author': r.get('last_post_author') or e.get('last_post_author') or '',
            'date': r.get('last_post_date') or e.get('last_post_date') or '',
            'sort_date': parse_sort_date(r.get('last_post_date') or e.get('last_post_date') or ''),
            'officiality': off_key,
            'officiality_label': off_lab,
            'notes': str(r.get('notes') or ''),
            'hidden_links': [normalize_link(x) for x in (r.get('hidden_links') or [])],
            'news_pages': e.get('news_pages') or [],
            'http_lm': e.get('http_last_modified') or '',
            'html_lm': e.get('html_modified') or e.get('meta_article_time') or '',
            'cms': e.get('cms') or '',
            'cms_version': e.get('cms_version') or '',
            'admin_urls': e.get('admin_urls') or [],
            'admin_checks': e.get('admin_checks') or [],
            'has_admin': bool(verified_admin_checks(e.get('admin_checks') or [])),
            'thumb': thumb_rel,
            'full': full_rel,
        })
        _ck = _amod.channel_key(url) if _amod is not None else ''
        items[-1]['channel'] = _ck
        items[-1]['comments_n'] = comments_by_channel.get(_ck, 0) if _ck else 0
        _fix_relative_social_date(items[-1], hist_index, _amod)
        _pu, _psrc = resolve_post_url(url, items[-1]['title'], r, e, hist_index, x_index, _amod)
        items[-1]['post_url'] = _pu
        items[-1]['post_url_src'] = _psrc

    motds = load_motd()
    motd_box, motd_archive = render_motd(motds, items, _amod.channel_key if _amod is not None else None)
    (OUT / 'motd.json').write_text(json.dumps(motds, ensure_ascii=False), encoding='utf-8')
    EV = load_events_ui().build(ROOT, OUT, items)

    # nav
    counts = {cid: sum(1 for it in items if it['cat'] == cid) for cid, *_ in CATS}
    admin_items = [it for it in items if it.get('has_admin')]
    admin_count = len(admin_items)
    admin_source_urls = sorted({it['url'] for it in admin_items if it.get('url')})
    nav = ['<button type="button" class="navbtn active" data-cat="all">Vše <span class="n">%d</span></button>' % len(items)]
    for cid, title, _ in CATS:
        nav.append(
            f'<button type="button" class="navbtn" data-cat="{cid}">{H.escape(title)} '
            f'<span class="n">{counts.get(cid, 0)}</span></button>'
        )
    nav.append(
        f'<button type="button" class="navbtn" data-cat="admin">Admin URL '
        f'<span class="n">{admin_count}</span></button>'
    )
    nav.append(
        f'<button type="button" class="navbtn navbtn-komentare" data-cat="komentare">Zajímavé komentáře '
        f'<span class="n">{len(comments)}</span></button>'
    )
    nav.append(EV.NAV)

    def comm_tile(it):
        n = it.get('comments_n') or 0
        return f'<div class="tcomm">💬 {comments_labeln(n)}</div>' if n else ''

    def mon_tile(it):
        h = '' if is_social_url(it['url']) else mon.tile_html(it['url'])
        return f'{h}<div class="tarch">{mon.arch_links(it["url"], in_anchor=True)}</div>'

    def comments_labeln(n):
        return comments_label(n)

    def tile_post(it):
        """Last-update title inside a tile. Tiles are <a> (open detail), so the
        title is a role=link span with data-href (opened by JS in a new tab)."""
        t = (it['title'] or '')[:120]
        if not t:
            return '<div class="tpost">—</div>'
        href = it.get('post_url') or it.get('url') or ''
        if not href:
            return f'<div class="tpost">{H.escape(t)}</div>'
        art = bool(it.get('post_url'))
        tip = 'Otevřít původní článek / příspěvek' if art else 'Otevřít web zdroje (odkaz na konkrétní článek není k dispozici)'
        return (
            f'<div class="tpost"><span class="tpost-link{" is-article" if art else " is-site"}" role="link" tabindex="0" '
            f'data-href="{H.escape(href, quote=True)}" title="{H.escape(tip, quote=True)}">{H.escape(t)}</span>'
            f'<span class="tpost-ext" aria-hidden="true"> ↗</span></div>'
        )

    # overview tiles per category
    sections = []
    for cid, title, desc in CATS:
        cat_items = [it for it in items if it['cat'] == cid]
        if not cat_items:
            continue
        tiles = []
        for it in cat_items:
            st = H.escape(it['status'])
            st_class = 'ok' if it['status'].startswith('ok') else ('wall' if ('login' in it['status'] or 'private' in it['status'] or it['status'] == 'blocked') else 'bad')
            thumb = (
                f'<img src="{it["thumb"]}" alt="" loading="lazy">'
                if it['thumb'] else '<div class="ph">bez náhledu</div>'
            )
            dhtml = reltime_html(it.get('date'), it.get('sort_date'), prefix='Aktualizace: ')
            off_key = it.get('officiality') or 'neoficialni'
            off_lab = H.escape(it.get('officiality_label') or 'Neoficiální')
            badge = f'<span class="badge badge-{off_key}">{off_lab}</span>'
            tiles.append(
                f'<a class="tile" href="#{it["id"]}" data-id="{it["id"]}" data-cat="{cid}" data-admin="{1 if it.get("has_admin") else 0}" data-date="{H.escape(it.get("sort_date") or "")}" data-off="{off_key}">'
                f'<div class="timg">{thumb}</div>'
                f'<div class="tbody">'
                f'<div class="tlabel{" lk-broken" if mon.is_broken(it["url"]) else ""}">{H.escape(it["label"])}</div>{mon.broken_mark(it["url"])}'
                f'<div class="tmeta">{badge}<span class="tstatus {st_class}">{st}</span>'
                f'<span class="tdate">{dhtml}</span></div>'
                f'{tile_post(it)}' f'{comm_tile(it)}' f'{mon_tile(it)}'
                f'</div></a>'
            )
        sections.append(
            f'<section class="catsec" id="cat-{cid}" data-cat="{cid}">'
            f'<header class="cathead"><h2>{H.escape(title)}</h2>'
            f'<p class="muted">{H.escape(desc)} · {len(cat_items)} položek</p></header>'
            f'<div class="grid">{"".join(tiles)}</div></section>'
        )


    # chronological flat view (all tiles sorted by sort_date desc)
    chrono_items = sorted(items, key=lambda x: x.get('sort_date') or '', reverse=True)
    chrono_tiles = []
    for it in chrono_items:
        st = H.escape(it['status'])
        st_class = 'ok' if it['status'].startswith('ok') else ('wall' if ('login' in it['status'] or 'private' in it['status'] or it['status'] == 'blocked') else 'bad')
        thumb = (
            f'<img src="{it["thumb"]}" alt="" loading="lazy">'
            if it['thumb'] else '<div class="ph">bez náhledu</div>'
        )
        dhtml = reltime_html(it.get('date'), it.get('sort_date'), prefix='Aktualizace: ')
        cat_title = next((t for c,t,_ in CATS if c == it['cat']), it['cat'])
        off_key = it.get('officiality') or 'neoficialni'
        off_lab = H.escape(it.get('officiality_label') or 'Neoficiální')
        badge = f'<span class="badge badge-{off_key}">{off_lab}</span>'
        chrono_tiles.append(
            f'<a class="tile" href="#{it["id"]}" data-id="{it["id"]}" data-cat="{it["cat"]}" data-admin="{1 if it.get("has_admin") else 0}" data-date="{H.escape(it.get("sort_date") or "")}" data-off="{off_key}">'
            f'<div class="timg">{thumb}</div>'
            f'<div class="tbody">'
            f'<div class="tlabel{" lk-broken" if mon.is_broken(it["url"]) else ""}">{H.escape(it["label"])}</div>{mon.broken_mark(it["url"])}'
            f'<div class="tmeta">{badge}<span class="tstatus {st_class}">{st}</span>'
            f'<span class="tdate">{dhtml}</span></div>'
            f'<div class="tcat">{H.escape(cat_title)}</div>'
            f'{tile_post(it)}' f'{comm_tile(it)}' f'{mon_tile(it)}'
            f'</div></a>'
        )
    chrono_section = (
        f'<section class="catsec" id="cat-chrono" data-cat="chrono">'
        f'<header class="cathead"><h2>Podle data aktualizace</h2>'
        f'<p class="muted">Bez kategorií · nejnovější nahoře · položky bez data na konci</p></header>'
        f'<div class="grid">{"".join(chrono_tiles)}</div></section>'
    )

    feed_section = (
        '<section class="catsec" id="cat-feed" data-cat="feed" hidden>'
        '<header class="cathead"><h2>Feed aktualizací</h2>'
        '<p class="muted">Chronologický tok ze všech zdrojů · historie se ukládá mezi denními refreshi</p></header>'
        '<div class="feed-filter" id="feedFilter" hidden></div>'
        '<div class="feed" id="feedList"></div>'
        '</section>'
    )
    reason_counts = {r: sum(1 for o in comments if r in (o.get('reasons') or [])) for r in COMMENT_REASONS}
    plat_counts = {}
    for o in comments:
        pk = (o.get('platform') or '').lower()
        plat_counts[pk] = plat_counts.get(pk, 0) + 1
    rchips = ['<button type="button" class="fchip active" data-reason="">Všechny důvody</button>'] + [
        f'<button type="button" class="fchip" data-reason="{H.escape(r, quote=True)}">{H.escape(r)} <span class="n">{n}</span></button>'
        for r, n in reason_counts.items() if n
    ]
    pchips = ['<button type="button" class="fchip active" data-platform="">Všechny sítě</button>'] + [
        f'<button type="button" class="fchip" data-platform="{H.escape(pk)}">{H.escape(PLATFORM_LABEL.get(pk, pk.upper()))} <span class="n">{n}</span></button>'
        for pk, n in sorted(plat_counts.items())
    ]
    if comments:
        comm_body = (
            f'<div class="comm-filters"><div class="fchips" id="commReasons"><span class="flbl">Důvod:</span>{"".join(rchips)}</div>'
            f'<div class="fchips" id="commPlatforms"><span class="flbl">Síť:</span>{"".join(pchips)}</div></div>'
            '<div class="feed-filter" id="commChannelFilter" hidden></div>'
            f'<div class="feed" id="commList">{"".join(render_comment_card(o) for o in comments)}</div>'
            '<p class="empty-feed" id="commEmpty" hidden>Žádné komentáře pro zvolený filtr.</p>'
        )
    else:
        comm_body = (
            '<p class="empty-feed">Zatím žádné zajímavé komentáře. Sbírají se při denním průchodu '
            'sociálními sítěmi (Facebook, Instagram, X, YouTube) – jména soukromých osob se neukládají.</p>'
        )
    komentare_section = (
        '<section class="catsec" id="cat-komentare" data-cat="komentare" hidden>'
        '<header class="cathead"><h2>Zajímavé komentáře</h2>'
        f'<p class="muted">Vybrané komentáře a odpovědi pod příspěvky sledovaných účtů · nejnovější nahoře · {len(comments)} celkem · '
        'soukromé osoby anonymizovány</p></header>'
        f'{comm_body}</section>'
    )
    comment_feed = []
    for o in comments:
        ch_url = o.get('channel_url') or ''
        ok_, ol_ = officiality(ch_url)
        txt = re.sub(r'\s+', ' ', o.get('comment_text') or '')
        comment_feed.append({
            'id': 'k-' + (o.get('id') or ''),
            'url': o.get('comment_url') or o.get('post_url') or ch_url,
            'urls': [u for u in (ch_url, o.get('post_url'), o.get('comment_url')) if u],
            'source_label': (short_label(ch_url) if ch_url else o.get('channel') or '') + ' · komentář',
            'category': categorize(ch_url) if ch_url else '',
            'title': '💬 „' + (txt[:180] + ('…' if len(txt) > 180 else '')) + '“',
            'date_raw': strip_rel_raw(o.get('comment_date_raw')),
            'date_iso': o.get('comment_date_iso') or o.get('first_seen') or '',
            'kind': 'komentář',
            'post_url': o.get('comment_url') or o.get('post_url') or '',
            'officiality': ok_,
            'officiality_label': ol_,
        })

    health_section = mon.health_section({it['url']: it for it in items})
    mon_link_state, mon_arch_wb = mon.js_data()

    hist_section = (
        '<section class="catsec" id="cat-histogram" data-cat="histogram" hidden>'
        '<header class="cathead"><h2>Histogram aktualizací</h2>'
        '<p class="muted">Počet archivovaných aktualizací v čase · filtr podle kategorie v navigaci</p></header>'
        '<div class="hist-wrap" id="histWrap"><div class="hist" id="histChart" role="img" aria-label="Histogram"></div></div>'
        '<p class="muted" id="histSummary"></p>'
        '</section>'
    )

    # detail panels
    details = []

    def detail_post_link(it):
        t = it.get('title') or ''
        pu = it.get('post_url') or ''
        if pu:
            return (
                f'<p class="post-title"><a href="{H.escape(pu, quote=True)}" target="_blank" rel="noopener"{mon.link_cls(pu)}>'
                f'{H.escape(t[:200] or pu)}</a> <a class="post-open" href="{H.escape(pu, quote=True)}" target="_blank" rel="noopener">'
                f'Otevřít původní článek ↗</a><br><code class="url">{H.escape(pu)}</code></p>'
            )
        if t and it.get('url'):
            return (
                f'<p class="post-title"><a href="{H.escape(it["url"], quote=True)}" target="_blank" rel="noopener">{H.escape(t[:200])}</a> '
                f'<span class="muted post-nolink">· odkaz na konkrétní článek není k dispozici – vede na web zdroje ↗</span></p>'
            )
        return ''

    for it in items:
        links_html = []
        for L in it['hidden_links']:
            lu = L.get('url') or ''
            lt = L.get('type') or 'link'
            src = L.get('source') or ''
            note = SOURCE_NOTE.get(src, src or '')
            if 'facebook.com' in lu:
                label = 'Facebook'
            elif 'instagram.com' in lu:
                label = 'Instagram'
            elif 'x.com' in lu or 'twitter.com' in lu:
                label = 'X'
            elif 'youtube.com' in lu:
                label = 'YouTube'
            else:
                label = lu[:60]
            links_html.append(
                f'<li><a href="{H.escape(lu)}" target="_blank" rel="noopener"{mon.link_cls(lu)}>{H.escape(label)}</a>{mon.broken_mark(lu)}{mon.arch_links(lu)}'
                f' <span class="tag">{H.escape(lt)}</span> <span class="muted">{H.escape(note)}</span>'
                f'<br><code class="url">{H.escape(lu)}</code></li>'
            )
        if not links_html:
            links_html.append('<li class="muted">—</li>')

        news_html = ''.join(
            f'<li><a href="{H.escape(n.get("url",""))}" target="_blank" rel="noopener"{mon.link_cls(n.get("url",""))}>'
            f'{H.escape(n.get("title") or n.get("url") or "")}</a>{mon.broken_mark(n.get("url",""))}{mon.arch_links(n.get("url",""))} '
            f'<span class="muted">{H.escape(str(n.get("last_item") or ""))}</span></li>'
            for n in it['news_pages']
        ) or '<li class="muted">nenalezeno / neprohledáno</li>'

        verified_admin = verified_admin_checks(it.get('admin_checks', []))
        admin_html = ''.join(
            f'<li><a href="{H.escape(str(c.get("url")), quote=True)}" target="_blank" rel="noopener">'
            f'{H.escape(str(c.get("url")))}</a> '
            f'<span class="badge badge-admin">HTTP {H.escape(str(c.get("status")))}</span></li>'
            for c in verified_admin
        )
        admin_row = f'<dt>Admin URL</dt><dd><ul>{admin_html}</ul></dd>' if admin_html else ''
        cms = f'{it["cms"]} {it["cms_version"]}'.strip() or '—'

        if it['thumb'] and it['full']:
            shot = (
                f'<a class="shot" href="{it["full"]}" data-full="{it["full"]}">'
                f'<img src="{it["thumb"]}" alt="" loading="lazy">'
                f'<span class="hint">Plné rozlišení</span></a>'
            )
        else:
            shot = '<p class="muted">Screenshot chybí</p>'

        details.append(
            f'<article class="detail" id="{it["id"]}" data-cat="{it["cat"]}" data-admin="{1 if it.get("has_admin") else 0}" hidden>'
            f'<button type="button" class="back">← Zpět na přehled</button>'
            f'<h2><a href="{H.escape(it["url"])}" target="_blank" rel="noopener"{mon.link_cls(it["url"])}>{H.escape(it["url"])}</a>{mon.broken_mark(it["url"])} {mon.arch_links(it["url"])}</h2>'
            f'<p class="meta"><span class="badge badge-{it.get("officiality") or "neoficialni"}">{H.escape(it.get("officiality_label") or "Neoficiální")}</span> · {H.escape(it["host"])} · Stav: <strong>{H.escape(it["status"])}</strong></p>'
            f'{shot}'
            f'<section><h3>Poslední příspěvek / aktualita</h3>'
            f'{detail_post_link(it)}'
            f'<textarea readonly rows="8" class="post-body">{H.escape(it["post_text"] or "—")}</textarea>'
            f'<p class="post-history"><a href="#cat-feed" class="history-link" data-source-url="{H.escape(it["url"], quote=True)}">Historie aktualit →</a>'
            + (f' · <a href="#cat-komentare" class="komentare-link" data-channel="{H.escape(it["channel"], quote=True)}">💬 {comments_label(it["comments_n"])} →</a>' if it.get('comments_n') else '')
            + f'</p>'
            f'<dl>'
            f'<dt>Autor</dt><dd>{H.escape(it["author"] or "—")}</dd>'
            f'<dt>Datum</dt><dd>{reltime_html(it.get("date"), it.get("sort_date"))}</dd>'
            f'<dt>HTTP Last-Modified</dt><dd>{H.escape(it["http_lm"] or "—")}</dd>'
            f'<dt>HTML / meta čas</dt><dd>{H.escape(it["html_lm"] or "—")}</dd>'
            f'</dl></section>'
            f'{"" if is_social_url(it["url"]) else mon.detail_html(it["url"])}'
            f'<section><h3>Nalezené odkazy</h3><ul class="links">{"".join(links_html)}</ul></section>'
            f'<section><h3>Aktuality / Novinky</h3><ul>{news_html}</ul></section>'
            f'<section><h3>Technologie</h3><dl><dt>CMS</dt><dd>{H.escape(cms)}</dd>{admin_row}</dl></section>'
            f'<p class="notes">{H.escape(it["notes"])}</p>'
            f'</article>'
        )


    # Absence / policy note from alt platform research
    alt_path = ROOT / 'data' / 'alt_platforms.json'
    absence_items = []
    if alt_path.exists():
        for a in json.loads(alt_path.read_text(encoding='utf-8')):
            if a.get('officiality') == 'absence' or a.get('platform') == 'policy':
                plat = (a.get('platform') or '').upper()
                if a.get('platform') == 'policy':
                    plat = 'Politika'
                note = a.get('notes') or ''
                absence_items.append(f'<li><strong>{H.escape(plat)}</strong> — {H.escape(note[:280])}</li>')
    absence_html = (
        '<section class="absence" id="alt-platforms">'
        '<header class="cathead"><h2>Jiné platformy — co (ne)existuje</h2>'
        '<p class="muted">Telegram, WhatsApp, TikTok, Discord, fóra · stav k 2026-09-06</p></header>'
        f'<ul class="absence-list">{"".join(absence_items)}</ul>'
        '<p class="muted">Detailní podklady: data/alt_platforms_2026-09-06.md</p>'
        '</section>'
    )

    now = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')
    page = f'''<!DOCTYPE html>
<html lang="cs"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mapa kanálů AČR / AZ</title>
<style>
:root{{--bg:#0b1017;--panel:#121a24;--card:#1a2332;--text:#e8eef6;--muted:#93a0b3;--accent:#4b9fff;--ok:#3dd68c;--wall:#f5a524;--bad:#f07178;--border:#2a3545}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);line-height:1.45}}
a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
.wrap{{max-width:1100px;margin:0 auto;padding:1rem 1.1rem 3rem}}
.top{{position:sticky;top:0;z-index:20;background:rgba(11,16,23,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--border);padding:.75rem 0}}
.top h1{{font-size:1.15rem;margin:0 0 .35rem}}
.sub{{color:var(--muted);font-size:.85rem;margin:0 0 .65rem}}
.nav{{display:flex;flex-wrap:wrap;gap:.4rem}}
.navbtn{{background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:999px;padding:.35rem .7rem;font-size:.8rem;cursor:pointer}}
.navbtn .n{{color:var(--muted);margin-left:.2rem}}
.navbtn.active,.navbtn:hover{{border-color:var(--accent);background:#1c2a3d}}
.catsec{{margin:1.4rem 0 2rem}}
.catsec[hidden],.detail[hidden]{{display:none !important}}
.cathead h2{{margin:0 0 .2rem;font-size:1.05rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.75rem;margin-top:.75rem}}
.tile{{display:flex;flex-direction:column;background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;color:inherit;text-decoration:none;min-height:100%}}
.tile:hover{{border-color:var(--accent);text-decoration:none}}
.timg{{aspect-ratio:16/10;background:#0a0f16;overflow:hidden}}
.timg img{{width:100%;height:100%;object-fit:cover;display:block}}
.ph{{display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:.8rem}}
.tbody{{padding:.65rem .7rem .8rem}}
.tlabel{{font-size:.82rem;font-weight:600;word-break:break-word}}
.tstatus{{display:inline-block;margin:.3rem 0;font-size:.7rem;padding:.1rem .4rem;border-radius:4px;background:#243044}}
.tstatus.ok{{color:var(--ok)}}.tstatus.wall{{color:var(--wall)}}.tstatus.bad{{color:var(--bad)}}
.tpost{{font-size:.78rem;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.tpost-link{{cursor:pointer;color:var(--text);text-decoration:underline;text-decoration-color:rgba(127,160,200,.45);text-underline-offset:2px}}
.tpost-link.is-site{{color:inherit;text-decoration-style:dotted}}
.tpost-link:hover,.tpost-link:focus{{color:var(--accent);text-decoration-color:var(--accent);outline:none}}
.tpost-ext{{color:var(--accent);font-size:.72rem}}
.post-title{{margin:.2rem 0 .5rem;font-weight:600;line-height:1.4}}
.post-title .post-open{{font-weight:500;font-size:.8rem;white-space:nowrap;margin-left:.3rem}}
.post-title .post-nolink{{font-weight:400;font-size:.78rem}}
.post-title code.url{{font-weight:400;font-size:.72rem}}
.feed-title a{{color:var(--text)}}
.feed-title a:hover{{color:var(--accent)}}
.detail{{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:1rem;margin-top:1rem}}
.detail h2{{font-size:.95rem;word-break:break-all;margin:.5rem 0}}
.detail h3{{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:1rem 0 .4rem}}
.back{{background:transparent;border:1px solid var(--border);color:var(--text);border-radius:8px;padding:.35rem .7rem;cursor:pointer}}
.back:hover{{border-color:var(--accent)}}
.shot{{display:block;position:relative;margin:.7rem 0}}
.shot img{{width:100%;border-radius:8px;border:1px solid var(--border)}}
.shot .hint{{position:absolute;right:.6rem;bottom:.6rem;background:rgba(0,0,0,.7);color:#fff;font-size:.75rem;padding:.25rem .5rem;border-radius:6px}}
dl{{display:grid;grid-template-columns:9rem 1fr;gap:.25rem .6rem;font-size:.92rem}}
dt{{color:var(--muted)}}dd{{margin:0;overflow-wrap:anywhere}}
.muted,.notes,.meta{{color:var(--muted);font-size:.85rem}}
.tag{{display:inline-block;background:#243044;border-radius:4px;padding:0 .35rem;font-size:.72rem}}
code.url{{font-size:.72rem;color:var(--muted);word-break:break-all}}
.links li{{margin:.4rem 0}}
.post-body{{display:block;width:100%;min-height:9rem;padding:.75rem;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);font:0.95rem/1.5 system-ui,-apple-system,sans-serif;user-select:text;resize:vertical;white-space:pre-wrap}}
.post-history{{margin:.5rem 0 0;font-size:.85rem}}
.feed-filter{{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-top:.65rem;padding:.5rem .65rem;background:var(--card);border:1px solid var(--border);border-radius:8px;font-size:.8rem}}
.feed-filter[hidden]{{display:none !important}}
.feed-filter button{{background:transparent;border:1px solid var(--border);border-radius:6px;color:var(--text);padding:.2rem .45rem;cursor:pointer}}
#lb{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:99;align-items:center;justify-content:center;padding:1rem;cursor:zoom-out}}
#lb.open{{display:flex}}#lb img{{max-width:100%;max-height:100%;object-fit:contain}}
#lb .close{{position:fixed;top:.8rem;right:1rem;color:#fff;font-size:1.5rem;background:transparent;border:0;cursor:pointer}}
.empty{{color:var(--muted);padding:2rem 0}}
.tmeta{{display:flex;flex-wrap:wrap;gap:.35rem .6rem;align-items:center;margin:.3rem 0}}
.badge{{display:inline-block;font-size:.65rem;font-weight:700;letter-spacing:.02em;padding:.12rem .4rem;border-radius:999px;border:1px solid var(--border);vertical-align:middle}}
.badge-oficialni{{background:#143528;color:#3dd68c;border-color:#1f5a3d}}
.absence{{margin:1.2rem 0;padding:1rem 1.1rem;background:var(--panel);border:1px solid var(--border);border-radius:12px}}
.absence-list{{margin:.4rem 0 0;padding-left:1.1rem;color:var(--text);font-size:.88rem;line-height:1.45}}
.absence-list li{{margin:.35rem 0}}

.badge-neoficialni{{background:#2a2430;color:#c9b8ff;border-color:#4a3f66}}
.tdate{{font-size:.72rem;color:var(--muted)}}
.tcat{{font-size:.7rem;color:var(--accent);margin:.15rem 0}}
.views{{display:flex;gap:.4rem;margin:.5rem 0 .35rem;flex-wrap:wrap}}
.viewbtn{{background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:.35rem .7rem;font-size:.8rem;cursor:pointer}}
.viewbtn.active{{border-color:var(--accent);background:#1c2a3d}}
#overview.mode-chrono .catsec:not(#cat-chrono){{display:none !important}}
#overview.mode-cats #cat-chrono,#overview.mode-cats #cat-feed,#overview.mode-cats #cat-histogram{{display:none !important}}
#overview.mode-chrono #cat-chrono{{display:block !important}}
#overview.mode-chrono #cat-feed,#overview.mode-chrono #cat-histogram{{display:none !important}}
#overview.mode-feed .catsec:not(#cat-feed){{display:none !important}}
#overview.mode-feed #cat-feed{{display:block !important}}
#overview.mode-histogram .catsec:not(#cat-histogram){{display:none !important}}
#overview.mode-histogram #cat-histogram{{display:block !important}}

body.hide-shots .timg,body.hide-shots .shot,body.hide-shots .tile .timg{{display:none !important}}
body.hide-shots .tile{{min-height:auto}}
.tog{{display:inline-flex;align-items:center;gap:.4rem;margin:.35rem 0;font-size:.8rem;color:var(--muted);cursor:pointer;user-select:none}}
.tog input{{accent-color:var(--accent)}}
.filters-toggle{{background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:.45rem .85rem;font-size:.85rem;cursor:pointer;min-height:40px}}
.filters-toggle:hover,.filters-toggle[aria-expanded="true"]{{border-color:var(--accent);background:#1c2a3d}}
.filters-panel{{margin-top:.5rem}}
.filters-panel[hidden]{{display:none !important}}
.topbar-row{{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;justify-content:space-between}}
.feed{{display:flex;flex-direction:column;gap:.65rem;margin-top:.75rem}}
.feed-card{{display:block;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:.75rem .85rem;color:inherit;text-decoration:none}}
.feed-card:hover{{border-color:var(--accent);text-decoration:none}}
.feed-meta{{display:flex;flex-wrap:wrap;gap:.35rem .6rem;align-items:center;font-size:.75rem;color:var(--muted);margin-bottom:.35rem}}
.feed-title{{font-size:.92rem;font-weight:600;margin:0 0 .25rem;line-height:1.35}}
.feed-src{{font-size:.8rem;color:var(--accent)}}
.feed-kind{{font-size:.65rem;background:#243044;border-radius:4px;padding:.08rem .35rem}}
.hist-wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:.75rem;padding-bottom:.35rem}}
.hist{{display:flex;align-items:flex-end;gap:3px;min-height:160px;min-width:min(100%,480px);padding:.5rem .25rem 1.6rem;border:1px solid var(--border);border-radius:12px;background:var(--panel);position:relative}}
.hist-bar{{flex:1 1 0;min-width:8px;max-width:28px;background:linear-gradient(180deg,#4b9fff,#2a6fbf);border-radius:4px 4px 0 0;position:relative;cursor:default}}
.hist-bar:hover,.hist-bar:focus{{outline:none;filter:brightness(1.15)}}
.hist-bar .tip{{display:none;position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);background:#0a0f16;border:1px solid var(--border);color:var(--text);font-size:.7rem;padding:.25rem .4rem;border-radius:6px;white-space:nowrap;z-index:5;pointer-events:none}}
.hist-bar:hover .tip,.hist-bar:focus .tip{{display:block}}
.hist-bar .lbl{{position:absolute;top:calc(100% + 4px);left:50%;transform:translateX(-50%) rotate(-45deg);transform-origin:top center;font-size:.58rem;color:var(--muted);white-space:nowrap}}
.empty-feed{{color:var(--muted);padding:1.2rem 0}}
#overview.mode-cats #cat-komentare,#overview.mode-chrono #cat-komentare,#overview.mode-feed #cat-komentare,#overview.mode-histogram #cat-komentare{{display:none !important}}
#overview.mode-komentare .catsec:not(#cat-komentare){{display:none !important}}
#overview.mode-komentare #cat-komentare{{display:block !important}}
#overview.mode-komentare .absence{{display:none}}
.tcomm{{font-size:.72rem;color:#f5c26b;margin-top:.3rem}}
.feed-kind.k-komentar{{background:#3a2c12;color:#f5c26b}}
.comm-card{{background:var(--card);border:1px solid var(--border);border-left:3px solid #f5c26b;border-radius:12px;padding:.75rem .85rem}}
.comm-card[hidden]{{display:none !important}}
.rchips{{display:flex;flex-wrap:wrap;gap:.3rem;margin:.1rem 0 .45rem}}
.rchip{{font-size:.68rem;background:#1c2a3d;border:1px solid #2f4a6b;color:#9cc7ff;border-radius:999px;padding:.08rem .45rem}}
.comm-text{{font-size:.9rem;white-space:pre-wrap;overflow-wrap:anywhere;border-left:2px solid var(--border);padding:.1rem 0 .1rem .65rem;margin:.2rem 0}}
.comm-text.collapsed{{max-height:9.5rem;overflow:hidden;-webkit-mask-image:linear-gradient(180deg,#000 70%,transparent)}}
.comm-more{{background:transparent;border:0;color:var(--accent);cursor:pointer;padding:.2rem 0;font-size:.8rem}}
.verif{{margin:.4rem 0;font-size:.8rem;border-left:3px solid #999;padding:.2rem .5rem;background:rgba(127,127,127,.07);border-radius:4px}}.verif summary{{cursor:pointer}}.verif-badge{{font-weight:600}}.verif-meta{{color:var(--muted)}}.verif-ok{{border-color:#2e7d32}}.verif-ok .verif-badge{{color:#2e7d32}}.verif-part{{border-color:#ef8f00}}.verif-part .verif-badge{{color:#c77700}}.verif-bad{{border-color:#c62828}}.verif-bad .verif-badge{{color:#c62828}}.verif-unk{{border-color:#777}}.verif-src{{margin:.2rem 0 .2rem 1.1rem;padding:0}}.verif-src-h{{margin:.3rem 0 0;font-weight:600}}
.comm-note{{font-size:.8rem;color:var(--muted);margin:.35rem 0 .2rem;font-style:italic}}
.comm-links{{font-size:.8rem;color:var(--muted);overflow-wrap:anywhere}}
.comm-auth{{font-size:.7rem;color:var(--muted)}} .comm-auth.off{{color:var(--ok)}} .comm-auth.pub{{color:#c9b8ff}}
.comm-eng{{font-size:.72rem}}
.comm-filters{{display:flex;flex-direction:column;gap:.4rem;margin-top:.6rem}}
.fchips{{display:flex;flex-wrap:wrap;gap:.35rem;align-items:center}}
.flbl{{font-size:.75rem;color:var(--muted);margin-right:.2rem}}
.fchip{{background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:999px;padding:.25rem .6rem;font-size:.75rem;cursor:pointer}}
.fchip .n{{color:var(--muted)}}
.fchip.active,.fchip:hover{{border-color:#f5c26b;background:#2a2412}}
.navbtn-komentare{{border-color:#5a4520}}
.motd{{margin:.9rem 0 .4rem;background:linear-gradient(180deg,#16233a,#111a28);border:1px solid #2f4a6b;border-left:4px solid var(--accent);border-radius:12px;padding:.7rem .9rem}}
.motd[hidden]{{display:none !important}}
.motd-sum{{list-style:none;cursor:default;display:flex;flex-wrap:wrap;align-items:baseline;gap:.25rem .6rem}}
.motd-sum::-webkit-details-marker{{display:none}}
.motd-kicker{{font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--accent)}}
.motd-date{{font-size:.75rem;color:var(--muted)}}
.motd-headline{{flex-basis:100%;font-size:1rem;font-weight:650;line-height:1.35;margin:.15rem 0 0}}
.motd-items{{list-style:none;margin:.55rem 0 0;padding:0;display:flex;flex-direction:column;gap:.4rem}}
.motd-item{{font-size:.88rem;line-height:1.45;padding:.1rem 0 .1rem .1rem}}
.motd-item.hi{{padding-left:.5rem;border-left:2px solid #e5484d}}
.motd-link{{color:var(--text)}}
.motd-link:hover{{color:var(--accent)}}
.motd-src{{font-size:.72rem;color:var(--muted);white-space:nowrap}}
.mchip{{display:inline-block;font-size:.66rem;font-weight:600;border-radius:999px;padding:.05rem .45rem;margin-right:.15rem;border:1px solid #2f4a6b;background:#1c2a3d;color:#9cc7ff;vertical-align:1px}}
.mc-komentar{{background:#3a2c12;border-color:#5a4520;color:#f5c26b}}
.mc-dostupnost,.mc-odkazy{{background:#3a1618;border-color:#6b2a2e;color:#ff9a9e}}
.mc-ssl{{background:#3a2a12;border-color:#6b4a20;color:#ffb86b}}
.mc-archiv{{background:#1f2430;border-color:#3a4458;color:#c3cad8}}
.mc-prispevek{{background:#143528;border-color:#1f5a3d;color:#3dd68c}}
.motd-imp{{font-size:.66rem;font-weight:700;color:#ff8a8f;margin-right:.2rem;white-space:nowrap}}
.motd-foot{{margin:.55rem 0 0;font-size:.8rem}}
.motd-archive{{margin:1.2rem 0 2rem}}
.motd-archive[hidden]{{display:none !important}}
.motd-day{{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:.75rem .9rem;margin-top:.75rem}}
.motd-day h3{{margin:0;font-size:.8rem;color:var(--muted);font-weight:600}}
.motd-day .motd-headline{{margin:.2rem 0 0}}
{MUI.CSS}
{EV.CSS}

@media (max-width:720px){{
  .wrap{{padding:.65rem .7rem 2.5rem}}
  .top{{padding:.55rem 0}}
  .top h1{{font-size:1rem}}
  .sub{{font-size:.78rem;margin:0 0 .45rem}}
  .navbtn,.viewbtn,.filters-toggle{{min-height:40px;padding:.45rem .75rem;font-size:.82rem}}
  .grid{{grid-template-columns:1fr;gap:.6rem}}
  .feed-card{{width:100%}}
  .detail{{padding:.7rem;border-radius:10px}}
  .detail h2{{font-size:.85rem}}
  dl{{grid-template-columns:1fr;gap:.1rem .4rem}}
  dt{{margin-top:.35rem}}
  .hist{{min-height:140px;gap:2px}}
  .hist-bar{{min-width:6px;max-width:18px}}
  .topbar-row{{gap:.35rem}}
  .legend{{display:none}}
  .fchip{{min-height:36px}}
  .motd{{margin:.6rem 0 .3rem;padding:.6rem .7rem}}
  .motd-sum{{cursor:pointer;padding-right:1.4rem;position:relative}}
  .motd-sum::after{{content:'▾';position:absolute;right:0;top:0;color:var(--muted);transition:transform .15s}}
  .motd-d:not([open]) .motd-sum::after{{transform:rotate(-90deg)}}
  .motd-headline{{font-size:.93rem}}
  .motd-item{{font-size:.85rem}}
}}
</style></head><body>
<div class="top"><div class="wrap">
<div class="topbar-row">
<div>
<h1>Mapa kanálů Armády ČR / Aktivní zálohy</h1>
<p class="sub">{H.escape(now)} · {len(items)} položek · archiv {len(updates_history)} · klikni na dlaždici pro detail</p>
</div>
<button type="button" class="filters-toggle" id="filtersToggle" aria-expanded="true" aria-controls="filtersPanel">Filtry</button>
</div>
<p class="sub legend"><span class="badge badge-oficialni">Oficiální</span> = MO / AČR (weby *.mo.gov.cz + oficiální sociální účty)
&nbsp;·&nbsp;
<span class="badge badge-neoficialni">Neoficiální</span> = jednotkové / dobrovolnické / komunitní kanály</p>
<div class="filters-panel" id="filtersPanel">
<nav class="nav" id="nav">{''.join(nav)}</nav>
<div class="views">
<button type="button" class="viewbtn active" id="viewCats" data-view="cats">Podle kategorií</button>
<button type="button" class="viewbtn" id="viewChrono" data-view="chrono">Podle data aktualizace</button>
<button type="button" class="viewbtn" id="viewFeed" data-view="feed">Feed</button>
<button type="button" class="viewbtn" id="viewHistogram" data-view="histogram">Histogram</button>
<button type="button" class="viewbtn" id="viewKomentare" data-view="komentare">Zajímavé komentáře</button>
<button type="button" class="viewbtn" id="viewHealth" data-view="health">Dostupnost a SSL</button>
{EV.VIEWBTN}
</div>
<label class="tog"><input type="checkbox" id="hideShots"> Skrýt screenshoty</label>
</div>
</div></div>
<div class="wrap">
{motd_box}
{EV.STRIP}
<div id="overview">{''.join(sections)}{chrono_section}{feed_section}{hist_section}{komentare_section}{health_section}{absence_html}</div>
{motd_archive}
{EV.SECTION}
<div id="details">{''.join(details)}</div>
</div>
<div id="lb" role="dialog" aria-modal="true"><button class="close" aria-label="Zavřít">×</button><img alt="plný screenshot"></div>
<script>
window.UPDATES_HISTORY = {json.dumps(updates_history, ensure_ascii=False)};
window.ADMIN_SOURCE_URLS = {json.dumps(admin_source_urls, ensure_ascii=False)};
window.COMMENT_FEED = {json.dumps(comment_feed, ensure_ascii=False)};
window.LINK_STATE = {json.dumps(mon_link_state, ensure_ascii=False)};
window.ARCH_WB = {json.dumps(mon_arch_wb, ensure_ascii=False)};
const nav=document.getElementById('nav');
const overview=document.getElementById('overview');
const details=document.getElementById('details');
const secs=[...document.querySelectorAll('.catsec')];
const dets=[...document.querySelectorAll('.detail')];
const lb=document.getElementById('lb'); const lbImg=lb.querySelector('img');
const overviewEl=document.getElementById('overview');
const viewCats=document.getElementById('viewCats');
const viewChrono=document.getElementById('viewChrono');
const viewFeed=document.getElementById('viewFeed');
const viewHistogram=document.getElementById('viewHistogram');
const viewKomentare=document.getElementById('viewKomentare');
const viewHealth=document.getElementById('viewHealth');
let commReason='', commPlatform='', commChannel='';
const filtersToggle=document.getElementById('filtersToggle');
const filtersPanel=document.getElementById('filtersPanel');
let activeCat='all';
let activeView=localStorage.getItem('acrViewMode')||'cats';
let feedSourceFilter='';

function isMobile(){{ return window.matchMedia('(max-width:720px)').matches; }}

function setFiltersOpen(open){{
  if(!filtersPanel||!filtersToggle) return;
  filtersPanel.hidden=!open;
  filtersToggle.setAttribute('aria-expanded', open?'true':'false');
  filtersToggle.textContent=open?'Skrýt filtry':'Filtry';
  localStorage.setItem('acr-az-filters-open', open?'1':'0');
}}
(function initFilters(){{
  const saved=localStorage.getItem('acr-az-filters-open');
  let open;
  if(saved===null) open=!isMobile();
  else open=saved==='1';
  setFiltersOpen(open);
  if(filtersToggle) filtersToggle.addEventListener('click',()=>{{
    setFiltersOpen(filtersPanel.hidden);
  }});
}})();

function dayKey(iso){{
  if(!iso) return '';
  const m=String(iso).match(/(\\d{{4}}-\\d{{2}}-\\d{{2}})/);
  return m?m[1]:'';
}}
function esc(s){{
  return String(s||'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
}}
function filteredHistory(list){{
  let all=list||window.UPDATES_HISTORY||[];
  if(feedSourceFilter) all=all.filter(u=>u.url===feedSourceFilter||(u.urls||[]).includes(feedSourceFilter));
  if(!activeCat||activeCat==='all') return all;
  if(activeCat==='admin') return all.filter(u=>(window.ADMIN_SOURCE_URLS||[]).includes(u.url));
  return all.filter(u=>u.category===activeCat);
}}
function renderFeed(){{
  const el=document.getElementById('feedList');
  if(!el) return;
  const filterEl=document.getElementById('feedFilter');
  if(filterEl){{
    filterEl.hidden=!feedSourceFilter;
    filterEl.innerHTML=feedSourceFilter
      ? `<span>Zdroj: <code class="url">${{esc(feedSourceFilter)}}</code></span><button type="button" data-clear-feed>✕ Zrušit filtr</button>`
      : '';
  }}
  const items=filteredHistory().concat(filteredHistory(window.COMMENT_FEED||[]))
    .sort((a,b)=>String(b.date_iso||'').localeCompare(String(a.date_iso||'')));
  if(!items.length){{
    el.innerHTML='<p class="empty-feed">Žádné aktualizace pro zvolený filtr.</p>';
    return;
  }}
  el.innerHTML=items.map(u=>{{
    const iso=esc(u.date_iso||'');
    const off=u.officiality||'neoficialni';
    const offLab=esc(u.officiality_label||'');
    const kind=esc(u.kind||'');
    const title=esc(u.title||'');
    const src=esc(u.source_label||'');
    const srcUrl=u.post_url||u.url||'';
    const url=esc(srcUrl||'#');
    return `<article class="feed-card" data-cat="${{esc(u.category||'')}}">
      <div class="feed-meta">
        <time class="reltime" datetime="${{iso}}" data-iso="${{iso}}" title="${{esc(absCz(u.date_iso||''))}}">${{esc(absCz(u.date_iso||'')||'—')}}</time>
        <span class="badge badge-${{off}}">${{offLab}}</span>
        <span class="feed-kind${{u.kind==='komentář'?' k-komentar':''}}">${{kind}}</span>
      </div>
      <h3 class="feed-title">${{(u.post_url||u.url)?`<a href="${{esc(u.post_url||u.url)}}" target="_blank" rel="noopener" title="${{u.post_url?'Otevřít původní článek / příspěvek':'Otevřít web zdroje'}}">${{title}}</a>`:title}}</h3>
      <div class="feed-src"><a href="${{url}}" target="_blank" rel="noopener" title="${{u.post_url?'Zdroj: konkrétní příspěvek / článek':'Zdroj: kanál (odkaz na konkrétní příspěvek není k dispozici)'}}"${{(window.LINK_STATE||{{}})[srcUrl]==='broken'?' class="lk-broken"':''}}>${{src}}</a>${{brokenMarkHtml(srcUrl)}}${{archLinksHtml(srcUrl)}}</div>
    </article>`;
  }}).join('');
  refreshRelTimes();
}}
function renderHistogram(){{
  const chart=document.getElementById('histChart');
  const summary=document.getElementById('histSummary');
  if(!chart) return;
  const items=filteredHistory();
  const counts={{}};
  items.forEach(u=>{{
    const d=dayKey(u.date_iso);
    if(!d) return;
    counts[d]=(counts[d]||0)+1;
  }});
  let days=Object.keys(counts).sort();
  // if sparse or many days, aggregate by week (ISO week start Monday)
  let mode='day';
  if(days.length>60 || (days.length>1 && days.length<8 && items.length<15)){{
    /* keep days if few buckets */
  }}
  if(days.length>90){{
    mode='week';
    const weeks={{}};
    days.forEach(d=>{{
      const dt=new Date(d+'T12:00:00');
      const day=(dt.getDay()+6)%7;
      const mon=new Date(dt); mon.setDate(dt.getDate()-day);
      const key=mon.toISOString().slice(0,10);
      weeks[key]=(weeks[key]||0)+counts[d];
    }});
    Object.keys(counts).forEach(k=>delete counts[k]);
    Object.assign(counts, weeks);
    days=Object.keys(counts).sort();
  }}
  const max=Math.max(1,...days.map(d=>counts[d]||0));
  if(!days.length){{
    chart.innerHTML='';
    if(summary) summary.textContent='Žádná data pro histogram.';
    return;
  }}
  // show last N bars for readability
  const maxBars=isMobile()?40:80;
  if(days.length>maxBars) days=days.slice(-maxBars);
  chart.innerHTML=days.map(d=>{{
    const n=counts[d]||0;
    const h=Math.max(2, Math.round((n/max)*140));
    const tip=mode==='week'?`Týden od ${{d}}: ${{n}}`:`${{d}}: ${{n}}`;
    const short=d.slice(5);
    return `<div class="hist-bar" style="height:${{h}}px" tabindex="0" aria-label="${{esc(tip)}}"><span class="tip">${{esc(tip)}}</span><span class="lbl">${{esc(short)}}</span></div>`;
  }}).join('');
  if(summary){{
    const total=items.length;
    summary.textContent=`${{total}} aktualizací · ${{days.length}} ${{mode==='week'?'týdnů':'dnů'}} v grafu · max ${{max}} / ${{mode==='week'?'týden':'den'}}`;
  }}
}}

const motdBox=document.getElementById('motd');
const motdArchive=document.getElementById('cat-motd');
function hideMotdArchive(){{ if(motdArchive) motdArchive.hidden=true; if(motdBox) motdBox.hidden=false; if(window.EV_hide) window.EV_hide(); }}
function showMotdArchive(){{
  if(!motdArchive) return showOverview('all');
  overview.hidden=true; dets.forEach(d=>d.hidden=true);
  motdArchive.hidden=false; if(motdBox) motdBox.hidden=true;
  history.replaceState(null,'','#cat-motd');
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
(function initMotd(){{
  const d=document.getElementById('motdDetails'); if(!d) return;
  d.querySelector('summary').addEventListener('click',e=>{{ if(!isMobile()) e.preventDefault(); }});
  window.matchMedia('(max-width:720px)').addEventListener('change',()=>{{ if(!isMobile()) d.open=true; }});
  if(motdArchive) motdArchive.addEventListener('click',e=>{{
    if(e.target.closest('[data-motd-back]')){{ hideMotdArchive(); setView(activeView==='cats'?'cats':activeView); }}
  }});
}})();
function hideSpecialSecs(){{
  ['cat-chrono','cat-feed','cat-histogram','cat-komentare','cat-health'].forEach(id=>{{
    const el=document.getElementById(id); if(el) el.hidden=true;
  }});
}}
function tileMatches(t){{
  if(activeCat==='admin') return t.dataset.admin==='1';
  return activeCat==='all'||t.dataset.cat===activeCat;
}}
function applyTileFilter(container){{
  if(!container) return;
  container.querySelectorAll('.tile').forEach(t=>{{ t.style.display=tileMatches(t)?'':'none'; }});
}}

function showOverview(cat){{
  hideMotdArchive();
  activeCat=cat||'all';
  if(activeView==='feed'||activeView==='histogram'||activeView==='chrono'||activeView==='komentare'||activeView==='health'){{
    setView(activeView, true);
    return;
  }}
  if(overviewEl){{
    overviewEl.classList.add('mode-cats');
    overviewEl.classList.remove('mode-chrono','mode-feed','mode-histogram','mode-komentare','mode-health');
  }}
  if(viewCats) viewCats.classList.add('active');
  [viewChrono,viewFeed,viewHistogram,viewKomentare,viewHealth].forEach(b=>b&&b.classList.remove('active'));
  hideSpecialSecs();
  dets.forEach(d=>d.hidden=true);
  overview.hidden=false;
  secs.forEach(s=>{{
    if(s.id==='cat-chrono'||s.id==='cat-feed'||s.id==='cat-histogram'||s.id==='cat-komentare'||s.id==='cat-health') {{ s.hidden=true; return; }}
    const tiles=[...s.querySelectorAll('.tile')];
    const visible=tiles.filter(tileMatches);
    tiles.forEach(t=>{{ t.style.display=tileMatches(t)?'':'none'; }});
    s.hidden = activeCat==='admin' ? visible.length===0 : !(activeCat==='all' || s.dataset.cat===activeCat);
  }});
  nav.querySelectorAll('.navbtn').forEach(b=>b.classList.toggle('active', b.dataset.cat===activeCat));
  history.replaceState(null,'', activeCat==='all' ? '#' : '#cat-'+activeCat);
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
function showDetail(id){{
  hideMotdArchive();
  overview.hidden=true;
  dets.forEach(d=>d.hidden = d.id!==id);
  const el=document.getElementById(id);
  if(el){{
    nav.querySelectorAll('.navbtn').forEach(b=>b.classList.toggle('active', b.dataset.cat===el.dataset.cat));
  }}
  history.replaceState(null,'','#'+id);
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
nav.addEventListener('click',e=>{{
  const b=e.target.closest('.navbtn'); if(!b) return;
  if(b.dataset.cat==='komentare'){{ commChannel=''; setView('komentare'); return; }}
  if(b.dataset.cat==='events'){{ if(window.EV_show) window.EV_show(); return; }}
  if(activeView==='komentare'||activeView==='health') activeView='cats';
  activeCat=b.dataset.cat;
  nav.querySelectorAll('.navbtn').forEach(x=>x.classList.toggle('active', x===b));
  if(activeView==='feed'){{ renderFeed(); history.replaceState(null,'','#cat-feed'); return; }}
  if(activeView==='histogram'){{ renderHistogram(); history.replaceState(null,'','#cat-histogram'); return; }}
  if(activeView==='chrono'){{
    // filter chrono tiles by category
    applyTileFilter(document.getElementById('cat-chrono'));
    return;
  }}
  showOverview(activeCat);
}});
overview.addEventListener('click',e=>{{
  const clear=e.target.closest('[data-clear-feed]');
  if(clear){{ feedSourceFilter=''; renderFeed(); return; }}
  if(e.target.closest('[data-clear-comm]')){{ commChannel=''; renderComments(); return; }}
  const more=e.target.closest('[data-more]');
  if(more){{ const t=more.previousElementSibling; const c=t.classList.toggle('collapsed'); more.textContent=c?'Zobrazit celý komentář':'Sbalit'; return; }}
  const fc=e.target.closest('.fchip');
  if(fc){{
    if('reason' in fc.dataset) commReason=fc.dataset.reason; else commPlatform=fc.dataset.platform||'';
    renderComments(); return;
  }}
  const pl=e.target.closest('.tpost-link[data-href]');
  if(pl){{ e.preventDefault(); e.stopPropagation(); window.open(pl.dataset.href,'_blank','noopener'); return; }}
  const t=e.target.closest('.tile'); if(!t) return;
  e.preventDefault(); showDetail(t.dataset.id);
}});
overview.addEventListener('keydown',e=>{{
  const pl=e.target.closest&&e.target.closest('.tpost-link[data-href]');
  if(pl&&(e.key==='Enter'||e.key===' ')){{ e.preventDefault(); e.stopPropagation(); window.open(pl.dataset.href,'_blank','noopener'); }}
}});
details.addEventListener('click',e=>{{
  const kl=e.target.closest('.komentare-link');
  if(kl){{ e.preventDefault(); commChannel=kl.dataset.channel||''; setView('komentare'); return; }}
  const historyLink=e.target.closest('.history-link');
  if(historyLink){{
    e.preventDefault();
    feedSourceFilter=historyLink.dataset.sourceUrl||'';
    activeCat='all';
    setView('feed', true);
    renderFeed();
    history.replaceState(null,'','#cat-feed');
    document.getElementById('cat-feed')?.scrollIntoView({{behavior:'smooth',block:'start'}});
    return;
  }}
  if(e.target.closest('.back')){{
    const d=e.target.closest('.detail');
    if(activeView==='cats') showOverview(d?d.dataset.cat:'all');
    else setView(activeView);
  }}
  const shot=e.target.closest('a.shot');
  if(shot){{e.preventDefault(); lbImg.src=shot.dataset.full||shot.href; lb.classList.add('open');}}
}});
lb.addEventListener('click',()=>{{lb.classList.remove('open'); lbImg.src='';}});
document.addEventListener('keydown',e=>{{if(e.key==='Escape'){{lb.classList.remove('open'); lbImg.src='';}}}});

function setView(mode, keepCat){{
  if(!overviewEl) return;
  hideMotdArchive();
  activeView=mode;
  overviewEl.classList.remove('mode-cats','mode-chrono','mode-feed','mode-histogram','mode-komentare','mode-health');
  overviewEl.classList.add('mode-'+mode);
  overview.hidden=false;
  dets.forEach(d=>d.hidden=true);
  [[viewCats,'cats'],[viewChrono,'chrono'],[viewFeed,'feed'],[viewHistogram,'histogram'],[viewKomentare,'komentare'],[viewHealth,'health']].forEach(([b,m])=>{{
    if(b) b.classList.toggle('active', mode===m);
  }});
  localStorage.setItem('acrViewMode', mode);
  hideSpecialSecs();
  if(mode==='chrono'){{
    const ch=document.getElementById('cat-chrono'); if(ch) ch.hidden=false;
    secs.forEach(s=>{{ if(s.id!=='cat-chrono') s.hidden=true; }});
    applyTileFilter(document.getElementById('cat-chrono'));
    history.replaceState(null,'','#cat-chrono');
  }} else if(mode==='feed'){{
    const f=document.getElementById('cat-feed'); if(f) f.hidden=false;
    secs.forEach(s=>{{ if(s.id!=='cat-feed') s.hidden=true; }});
    renderFeed();
    history.replaceState(null,'','#cat-feed');
  }} else if(mode==='komentare'){{
    const k=document.getElementById('cat-komentare'); if(k) k.hidden=false;
    secs.forEach(s=>{{ if(s.id!=='cat-komentare') s.hidden=true; }});
    nav.querySelectorAll('.navbtn').forEach(b=>b.classList.toggle('active', b.dataset.cat==='komentare'));
    renderComments();
    history.replaceState(null,'','#cat-komentare');
  }} else if(mode==='health'){{
    const hs=document.getElementById('cat-health'); if(hs) hs.hidden=false;
    secs.forEach(s=>{{ if(s.id!=='cat-health') s.hidden=true; }});
    history.replaceState(null,'','#cat-health');
  }} else if(mode==='histogram'){{
    const h=document.getElementById('cat-histogram'); if(h) h.hidden=false;
    secs.forEach(s=>{{ if(s.id!=='cat-histogram') s.hidden=true; }});
    renderHistogram();
    history.replaceState(null,'','#cat-histogram');
  }} else {{
    showOverview(keepCat?activeCat:'all');
    return;
  }}
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
if(viewCats) viewCats.addEventListener('click',()=>setView('cats'));
if(viewChrono) viewChrono.addEventListener('click',()=>setView('chrono'));
if(viewFeed) viewFeed.addEventListener('click',()=>setView('feed'));
if(viewHistogram) viewHistogram.addEventListener('click',()=>setView('histogram'));
if(viewKomentare) viewKomentare.addEventListener('click',()=>{{ commChannel=''; setView('komentare'); }});
if(viewHealth) viewHealth.addEventListener('click',()=>setView('health'));
function renderComments(){{
  const list=document.getElementById('commList'); if(!list) return;
  let shown=0;
  list.querySelectorAll('.comm-card').forEach(c=>{{
    const ok=(!commReason || (c.dataset.reasons||'').split('|').includes(commReason))
      && (!commPlatform || c.dataset.platform===commPlatform)
      && (!commChannel || c.dataset.channel===commChannel);
    c.hidden=!ok; if(ok) shown++;
  }});
  document.querySelectorAll('#commReasons .fchip').forEach(b=>b.classList.toggle('active', b.dataset.reason===commReason));
  document.querySelectorAll('#commPlatforms .fchip').forEach(b=>b.classList.toggle('active', (b.dataset.platform||'')===commPlatform));
  const cf=document.getElementById('commChannelFilter');
  if(cf){{ cf.hidden=!commChannel; cf.innerHTML=commChannel?`<span>Kanál: <code class="url">${{esc(commChannel)}}</code></span><button type="button" data-clear-comm>✕ Zrušit filtr</button>`:''; }}
  const em=document.getElementById('commEmpty'); if(em) em.hidden=shown>0;
  refreshRelTimes();
}}

const hideShots=document.getElementById('hideShots');
if(hideShots){{
  const saved=localStorage.getItem('acrHideShots')==='1';
  hideShots.checked=saved; document.body.classList.toggle('hide-shots', saved);
  hideShots.addEventListener('change',()=>{{
    document.body.classList.toggle('hide-shots', hideShots.checked);
    localStorage.setItem('acrHideShots', hideShots.checked?'1':'0');
  }});
}}
function route(){{
  const h=(location.hash||'').slice(1);
  if(h==='cat-motd'||h.startsWith('motd-')) return showMotdArchive();
  if((h==='cat-events'||h.startsWith('ev-')) && window.EV_show) return window.EV_show(h);
  if(h==='cat-chrono') return setView('chrono');
  if(h==='cat-feed') return setView('feed');
  if(h==='cat-histogram') return setView('histogram');
  if(h==='cat-komentare') return setView('komentare');
  if(h==='cat-health'||h==='kontrola-odkazu') return setView('health');
  if(!h || h==='all') {{ activeView='cats'; return showOverview('all'); }}
  if(h.startsWith('cat-')) {{ activeView='cats'; return showOverview(h.slice(4)); }}
  if(document.getElementById(h)?.classList.contains('detail')) return showDetail(h);
  showOverview('all');
}}
window.addEventListener('hashchange',route);

function czPlural(n, one, few, many){{
  n=Math.abs(n|0);
  if(n===1) return n+' '+one;
  if(n>=2 && n<=4) return n+' '+few;
  return n+' '+many;
}}
function parseIsoLocal(iso){{
  if(!iso) return null;
  let dt=null;
  try{{
    if(/^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(iso)){{ const [y,m,d]=iso.split('-').map(Number); dt=new Date(y,m-1,d); dt._dateOnly=true; }}
    else dt=new Date(/[zZ]$|[+-]\\d{{2}}:?\\d{{2}}$/.test(iso)||iso.includes('T') ? iso : iso.replace(' ','T'));
  }}catch(e){{ return null; }}
  return (dt && !isNaN(dt.getTime())) ? dt : null;
}}
/* absolute fallback, same format as the server: "11. 10. 2026 8:34" (Europe/Prague) */
function absCz(iso){{
  const dt=parseIsoLocal(iso); if(!dt) return iso||'';
  if(dt._dateOnly) return dt.getDate()+'. '+(dt.getMonth()+1)+'. '+dt.getFullYear();
  try{{
    const p={{}}; new Intl.DateTimeFormat('cs-CZ',{{timeZone:'Europe/Prague',year:'numeric',month:'numeric',day:'numeric',hour:'numeric',minute:'2-digit',hour12:false}})
      .formatToParts(dt).forEach(x=>p[x.type]=x.value);
    return p.day+'. '+p.month+'. '+p.year+' '+(+p.hour)+':'+p.minute;
  }}catch(e){{ return dt.toLocaleString('cs-CZ'); }}
}}
function calDays(dt){{ const a=new Date(); a.setHours(0,0,0,0); const b=new Date(dt); b.setHours(0,0,0,0); return Math.round((a-b)/86400000); }}
function prettyAgoFromIso(iso){{
  const dt=parseIsoLocal(iso); if(!dt) return '';
  if(dt._dateOnly){{
    const d=calDays(dt);
    if(d<0) return 'za '+czPlural(-d,'den','dny','dní');
    if(d===0) return 'dnes'; if(d===1) return 'včera';
    if(d<7) return 'před '+czPlural(d,'dnem','dny','dny');
    if(d<30) return 'před '+czPlural(Math.floor(d/7),'týdnem','týdny','týdny');
    if(d<365) return 'před '+czPlural(Math.floor(d/30),'měsícem','měsíci','měsíci');
    return 'před '+czPlural(Math.floor(d/365),'rokem','roky','lety');
  }}
  const secs=Math.floor((Date.now()-dt.getTime())/1000);
  if(secs<0) return 'právě teď';
  if(secs<60) return 'před chvílí';
  const mins=Math.floor(secs/60);
  if(mins<60) return 'před '+czPlural(mins,'minutou','minutami','minutami');
  const hours=Math.floor(mins/60);
  if(hours<24) return 'před '+czPlural(hours,'hodinou','hodinami','hodinami');
  const days=Math.floor(hours/24);
  if(days<7) return 'před '+czPlural(days,'dnem','dny','dny');
  const weeks=Math.floor(days/7);
  if(days<30) return 'před '+czPlural(weeks,'týdnem','týdny','týdny');
  const months=Math.floor(days/30);
  if(days<365) return 'před '+czPlural(months,'měsícem','měsíci','měsíci');
  const years=Math.floor(days/365);
  return 'před '+czPlural(years,'rokem','roky','lety');
}}
/* expiry (SSL notAfter): "za 61 dní" / "dnes" / "vypršel před 3 dny"; data-rel="days" → bare number */
function untilDays(iso){{ const dt=parseIsoLocal(iso); if(!dt) return null; return Math.floor((dt.getTime()-Date.now())/86400000); }}
function expClass(d){{ return d==null?'exp-na':(d<=14?'exp-red':(d<=30?'exp-orange':'exp-ok')); }}
/* All relative time is client-side only: server text inside time.reltime is absolute (Europe/Prague). */
function refreshRelTimes(){{
  document.querySelectorAll('time.reltime[data-iso]').forEach(el=>{{
    if(el.dataset.abs===undefined){{
      el.querySelectorAll('.ago').forEach(x=>x.remove());
      el.dataset.abs=el.textContent.trim();
      if(!el.title) el.title=el.dataset.abs;
    }}
    const iso=el.dataset.iso||el.getAttribute('datetime')||'';
    const mode=el.dataset.rel||'';
    let txt='';
    if(mode==='expiry'||mode==='days'){{
      const d=untilDays(iso);
      if(d!=null){{
        txt = mode==='days' ? String(d) : (d>0 ? 'za '+czPlural(d,'den','dny','dní') : (d===0?'dnes':'vypršel '+prettyAgoFromIso(iso)));
        const host=el.closest('.exp-ok,.exp-orange,.exp-red,.exp-na');
        if(host){{ host.classList.remove('exp-ok','exp-orange','exp-red','exp-na'); host.classList.add(expClass(d)); }}
      }}
    }} else txt=prettyAgoFromIso(iso);
    el.textContent = txt || el.dataset.abs;
  }});
}}
refreshRelTimes();
setInterval(refreshRelTimes, 60*1000);
document.addEventListener('visibilitychange',()=>{{ if(!document.hidden) refreshRelTimes(); }});
{MUI.JS}
{EV.JS}

overviewEl && overviewEl.classList.add('mode-cats');
route();
if(activeView==='chrono'||activeView==='feed'||activeView==='histogram'||activeView==='komentare'||activeView==='health'){{
  /* route() already handled hash; apply saved view if no special hash */
  const h=(location.hash||'').slice(1);
  if(!h || h==='all' || (h.startsWith('cat-') && !['cat-chrono','cat-feed','cat-histogram','cat-komentare','cat-health'].includes(h) && !h.match(/^cat-(chrono|feed|histogram)$/))){{
    if(!h.startsWith('cat-') || h==='all'){{
      const saved=localStorage.getItem('acrViewMode');
      if(saved==='chrono'||saved==='feed'||saved==='histogram'||saved==='komentare'||saved==='health') setView(saved);
    }}
  }}
}}

</script></script>
</body></html>'''
    (OUT / 'index.html').write_text(page, encoding='utf-8')
    print(f'ok items={len(items)} admin={admin_count} komentare={len(comments)} html={(OUT/"index.html").stat().st_size} cats={dict(counts)}')


if __name__ == '__main__':
    main()
