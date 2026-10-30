"""Mapování JSON klíčů na sloupce (sdíleno import_data.py a mapa_db.py)."""
T, I, R, B = 'TEXT', 'INTEGER', 'REAL', 'BOOL'

CAPTURE = [('url', T), ('slug', T), ('screenshot_path', T), ('last_post_title', T), ('last_post_author', T),
           ('last_post_date', T), ('status', T), ('notes', T), ('last_post_url', T), ('last_post_date_raw', T),
           ('captured_at', T), ('screenshot', T), ('last_post_text', T), ('status_note', T),
           ('last_post_caption', T), ('source', T), ('alt_platform', T), ('officiality_hint', T)]
CAPTURE_ORDER = ['url', 'slug', 'screenshot_path', 'last_post_title', 'last_post_author', 'last_post_date', 'status',
                 'hidden_links', 'notes', 'last_post_url', 'last_post_date_raw', 'captured_at', 'screenshot',
                 'last_post', 'last_post_text', 'status_note', 'last_post_caption', 'recent_posts', 'source',
                 'alt_platform', 'officiality_hint']
CAPTURE_NESTED = ('hidden_links', 'recent_posts', 'last_post')
LAST_POST = [('title', T), ('author', T), ('date', T)]
HIDDEN_LINK = [('url', T), ('type', T), ('source', T)]
RECENT_POST = [('text', T), ('created_at', T), ('author', T)]

ENRICH = [('http_last_modified', T), ('html_modified', T), ('meta_article_time', T), ('cms', T), ('cms_version', T),
          ('cms_evidence', T), ('last_post_title', T), ('last_post_date', T), ('last_post_author', T),
          ('fetch_status', T), ('final_url', T), ('error', T), ('etag', T), ('http_date', T), ('feed_source', T)]
ENRICH_ORDER = ['http_last_modified', 'html_modified', 'meta_article_time', 'cms', 'cms_version', 'cms_evidence',
                'admin_urls', 'admin_checks', 'news_pages', 'last_post_title', 'last_post_date', 'last_post_author',
                'fetch_status', 'final_url', 'error', 'etag', 'http_date', 'feed_source']
ENRICH_NESTED = ('admin_urls', 'admin_checks', 'news_pages')
ADMIN_CHECK = [('url', T), ('status', T)]          # url -> check_url
NEWS_PAGE = [('url', T), ('title', T), ('last_item', T), ('last_item_date', T), ('last_item_url', T)]  # url -> page_url

UPDATE = [('id', T), ('url', T), ('source_label', T), ('category', T), ('title', T), ('author', T), ('date_raw', T),
          ('date_iso', T), ('captured_at', T), ('kind', T), ('first_seen', T), ('last_seen', T), ('post_url', T)]
UPDATE_ORDER = ['id', 'url', 'source_label', 'category', 'title', 'author', 'date_raw', 'date_iso', 'captured_at',
                'kind', 'urls', 'first_seen', 'last_seen', 'post_url']

COMMENT = [('id', T), ('platform', T), ('channel', T), ('channel_url', T), ('post_url', T),
           ('post_title_or_excerpt', T), ('comment_url', T), ('comment_text', T), ('author_type', T), ('author', T),
           ('author_name', T), ('comment_date_iso', T), ('comment_date_raw', T), ('comment_date_approx', B),
           ('first_seen', T), ('last_seen', T), ('reason_note', T), ('screenshot', T), ('source_method', T),
           ('anonymized', B)]
COMMENT_ORDER = ['id', 'platform', 'channel', 'channel_url', 'post_url', 'post_title_or_excerpt', 'comment_url',
                 'comment_text', 'author_type', 'author', 'author_name', 'comment_date_iso', 'comment_date_raw',
                 'comment_date_approx', 'first_seen', 'last_seen', 'reasons', 'reason_note', 'engagement',
                 'screenshot', 'source_method', 'anonymized', 'verification']
ENGAGEMENT = [('likes', I), ('replies', I)]
VERIF = [('status', T), ('checked_at', T), ('verifier', T), ('summary', T)]
VERIF_ORDER = ['status', 'checked_at', 'verifier', 'summary', 'sources']
VSRC = [('title', T), ('url', T)]

EVENT = [('id', T), ('title', T), ('type', T), ('start', T), ('end', T), ('all_day', B), ('location', T),
         ('organizer', T), ('description', T), ('source_url', T), ('channel', T), ('confidence', T),
         ('date_precision', T), ('first_seen', T), ('updated', T)]

MOTD = [('date', T), ('generated_at', T), ('headline', T)]
MOTD_ITEM = [('text', T), ('category', T), ('importance', T), ('link', T), ('source_url', T)]

HEALTH = [('date', T), ('ts', T), ('url', T), ('host', T), ('up', B), ('http_status', I), ('final_url', T),
          ('response_ms', I), ('error', T)]
TLS = [('ok', B), ('version', T), ('issuer_o', T), ('issuer_cn', T), ('subject_cn', T), ('san_count', I),
       ('not_before', T), ('not_after', T), ('days_to_expiry', I), ('verify_error', T), ('error', T)]

LINK = [('url', T), ('http_status', I), ('final_url', T), ('method', T), ('response_ms', I), ('error', T),
        ('social', B), ('status', T), ('checked_at', T)]
LINK_ORDER = ['url', 'http_status', 'final_url', 'method', 'response_ms', 'error', 'social', 'status', 'sources',
              'checked_at']
LINK_HIST = [('date', T), ('ts', T), ('url', T), ('status', T), ('http_status', I), ('error', T)]

ARCH_LINK = [('wayback_browse', T), ('archive_is_browse', T)]
ARCH_LINK_ORDER = ['wayback', 'archive_is', 'wayback_browse', 'archive_is_browse']
SNAP = [('status', T), ('snapshot_url', T), ('timestamp', T), ('timestamp_iso', T), ('http_status', I),
        ('location', T), ('checked_at', T), ('last_submit', T), ('last_submit_status', T), ('note', T),
        ('last_error', T), ('error', T)]
SUBMIT = [('ts', T), ('service', T), ('url', T), ('http_status', I), ('secs', R), ('status', T), ('timestamp', T),
          ('snapshot_url', T), ('location', T), ('detail', T)]

XFILE = [('captured_at', T), ('method', T), ('note', T)]
XACC = [('handle', T), ('url', T)]
XPOST = [('id', T), ('created_at', T), ('text', T), ('url', T)]
XTWEET = [('created_at', T), ('handle', T), ('id', T), ('src', T), ('text', T), ('url', T)]
ALT = [('platform', T), ('name', T), ('url', T), ('handle', T), ('officiality', T), ('notes', T), ('evidence', T)]
FEED = [('source', T), ('feed_url', T), ('fetched_via', T), ('fetched_at', T)]
FEED_ITEM = [('title', T), ('url', T), ('pubDate', T)]   # pubDate -> pub_date
