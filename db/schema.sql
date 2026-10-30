-- =====================================================================
-- Mapa AČR / Aktivní záloha – normalizované SQLite schéma
-- Generuje: db/import_data.py (idempotentně, celé DB se vždy staví znovu)
-- Konvence:
--   ord        … pořadí řádku ve zdrojovém souboru (zachování pořadí)
--   extra_json … bezeztrátová rekonstrukce: {"x":{nenamapované klíče},
--                "n":[klíče s explicitním null], "o":[pořadí klíčů],
--                "a":[chybějící seznamy]}; NULL = nic navíc
--   časy       … ISO 8601 text (jak je ve zdroji), boolean … 0/1
-- =====================================================================
PRAGMA foreign_keys = ON;

CREATE TABLE meta (                 -- metadata importu + skalární hlavičky souborů
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);

-- ---------------------------------------------------------------- kanály
CREATE TABLE watched_urls (         -- urls.json: seznam sledovaných URL dle skupiny
  group_name TEXT NOT NULL,
  ord        INTEGER NOT NULL,
  url        TEXT NOT NULL,
  PRIMARY KEY (group_name, ord)
);
CREATE INDEX ix_watched_urls_url ON watched_urls(url);

CREATE TABLE channels (             -- odvozený číselník kanálů (dlaždice mapy)
  url               TEXT PRIMARY KEY,
  category          TEXT,           -- klíč kategorie (categorize())
  officiality       TEXT CHECK (officiality IN ('oficialni','neoficialni')),
  officiality_label TEXT,
  label             TEXT,           -- krátký popisek (short_label())
  platform          TEXT            -- web / fb / ig / x / yt / … (odvozeno z hostu)
);

CREATE TABLE captures (             -- data/captures.jsonl – snímky kanálů
  capture_id         INTEGER PRIMARY KEY,   -- = ord
  url                TEXT NOT NULL,
  slug               TEXT,
  screenshot_path    TEXT,
  screenshot         TEXT,
  status             TEXT,
  status_note        TEXT,
  notes              TEXT,
  last_post_title    TEXT,
  last_post_author   TEXT,
  last_post_date     TEXT,
  last_post_date_raw TEXT,
  last_post_url      TEXT,
  last_post_text     TEXT,
  last_post_caption  TEXT,
  captured_at        TEXT,
  source             TEXT,
  alt_platform       TEXT,
  officiality_hint   TEXT,
  extra_json         TEXT
);
CREATE INDEX ix_captures_url ON captures(url);

CREATE TABLE capture_last_post (    -- captures.last_post {title,author,date}
  capture_id INTEGER PRIMARY KEY REFERENCES captures(capture_id) ON DELETE CASCADE,
  title TEXT, author TEXT, date TEXT, extra_json TEXT
);
CREATE TABLE capture_hidden_links (
  capture_id INTEGER NOT NULL REFERENCES captures(capture_id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, url TEXT, type TEXT, source TEXT, extra_json TEXT,
  PRIMARY KEY (capture_id, ord)
);
CREATE TABLE capture_recent_posts (
  capture_id INTEGER NOT NULL REFERENCES captures(capture_id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, text TEXT, created_at TEXT, author TEXT, extra_json TEXT,
  PRIMARY KEY (capture_id, ord)
);

-- ---------------------------------------------------------------- enrichment
CREATE TABLE enrichment (           -- data/enrichment.json (klíč = URL webu)
  url TEXT PRIMARY KEY, ord INTEGER NOT NULL,
  http_last_modified TEXT, html_modified TEXT, meta_article_time TEXT,
  cms TEXT, cms_version TEXT, cms_evidence TEXT,
  last_post_title TEXT, last_post_date TEXT, last_post_author TEXT,
  fetch_status TEXT, final_url TEXT, error TEXT, etag TEXT, http_date TEXT, feed_source TEXT,
  extra_json TEXT
);
CREATE TABLE enrichment_admin_urls (
  url TEXT NOT NULL REFERENCES enrichment(url) ON DELETE CASCADE,
  ord INTEGER NOT NULL, admin_url TEXT NOT NULL, PRIMARY KEY (url, ord)
);
CREATE TABLE enrichment_admin_checks (
  url TEXT NOT NULL REFERENCES enrichment(url) ON DELETE CASCADE,
  ord INTEGER NOT NULL, check_url TEXT, status TEXT, extra_json TEXT, PRIMARY KEY (url, ord)
);
CREATE TABLE enrichment_news_pages (
  url TEXT NOT NULL REFERENCES enrichment(url) ON DELETE CASCADE,
  ord INTEGER NOT NULL, page_url TEXT, title TEXT, last_item TEXT, last_item_date TEXT,
  last_item_url TEXT, extra_json TEXT, PRIMARY KEY (url, ord)
);

-- ---------------------------------------------------------------- historie příspěvků
CREATE TABLE updates (              -- data/updates_history.jsonl
  ord INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  url TEXT NOT NULL, source_label TEXT, category TEXT, title TEXT, author TEXT,
  date_raw TEXT, date_iso TEXT, captured_at TEXT, kind TEXT,
  first_seen TEXT, last_seen TEXT, post_url TEXT, extra_json TEXT
);
CREATE INDEX ix_updates_url ON updates(url);
CREATE INDEX ix_updates_date ON updates(date_iso);
CREATE TABLE update_urls (
  update_ord INTEGER NOT NULL REFERENCES updates(ord) ON DELETE CASCADE,
  ord INTEGER NOT NULL, url TEXT NOT NULL, PRIMARY KEY (update_ord, ord)
);
CREATE INDEX ix_update_urls_url ON update_urls(url);

-- ---------------------------------------------------------------- komentáře
CREATE TABLE reasons (              -- číselník důvodů zařazení komentáře
  reason TEXT PRIMARY KEY
);
CREATE TABLE comments (             -- data/interesting_comments.jsonl
  ord INTEGER NOT NULL UNIQUE,
  id TEXT PRIMARY KEY,
  platform TEXT CHECK (platform IN ('fb','ig','x','yt','web','tiktok','other')),
  channel TEXT, channel_url TEXT, post_url TEXT, post_title_or_excerpt TEXT,
  comment_url TEXT, comment_text TEXT,
  author_type TEXT CHECK (author_type IN ('anon','official_account')),
  author TEXT, author_name TEXT,
  comment_date_iso TEXT, comment_date_raw TEXT, comment_date_approx INTEGER,
  first_seen TEXT, last_seen TEXT, reason_note TEXT, screenshot TEXT,
  source_method TEXT, anonymized INTEGER, extra_json TEXT
);
CREATE INDEX ix_comments_channel ON comments(channel_url);
CREATE TABLE comment_reasons (
  comment_id TEXT NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, reason TEXT NOT NULL REFERENCES reasons(reason),
  PRIMARY KEY (comment_id, ord)
);
CREATE TABLE comment_engagement (
  comment_id TEXT PRIMARY KEY REFERENCES comments(id) ON DELETE CASCADE,
  likes INTEGER, replies INTEGER, extra_json TEXT
);
CREATE TABLE verifications (        -- comments.verification (ověření faktů)
  comment_id TEXT PRIMARY KEY REFERENCES comments(id) ON DELETE CASCADE,
  status TEXT CHECK (status IN ('potvrzeno','částečně pravdivé','nepodloženo / nelze ověřit','vyvráceno','nepravdivé')),
  checked_at TEXT, verifier TEXT, summary TEXT, extra_json TEXT
);
CREATE TABLE verification_sources (
  comment_id TEXT NOT NULL REFERENCES verifications(comment_id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, title TEXT, url TEXT, extra_json TEXT, PRIMARY KEY (comment_id, ord)
);

-- ---------------------------------------------------------------- události
CREATE TABLE events (               -- data/events.json
  ord INTEGER NOT NULL UNIQUE,
  id TEXT PRIMARY KEY, title TEXT, type TEXT, start TEXT, "end" TEXT, all_day INTEGER,
  location TEXT, organizer TEXT, description TEXT, source_url TEXT, channel TEXT,
  confidence TEXT, date_precision TEXT CHECK (date_precision IN ('day','month')),
  first_seen TEXT, updated TEXT, extra_json TEXT
);
CREATE INDEX ix_events_start ON events(start);
CREATE TABLE event_replaced_ids (
  event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, replaced_id TEXT NOT NULL, PRIMARY KEY (event_id, ord)
);

-- ---------------------------------------------------------------- Zpráva dne
CREATE TABLE motd (                 -- data/motd/YYYY-MM-DD.json
  file_date TEXT PRIMARY KEY, date TEXT, generated_at TEXT, headline TEXT, extra_json TEXT
);
CREATE TABLE motd_items (
  file_date TEXT NOT NULL REFERENCES motd(file_date) ON DELETE CASCADE,
  ord INTEGER NOT NULL, text TEXT, category TEXT, importance TEXT, link TEXT,
  source_url TEXT, extra_json TEXT, PRIMARY KEY (file_date, ord)
);
CREATE TABLE motd_stats (           -- data/motd/stats/YYYY-MM-DD.json (klíč → hodnota)
  date TEXT NOT NULL, key TEXT NOT NULL, ord INTEGER NOT NULL, value_json TEXT,
  PRIMARY KEY (date, key)
);

-- ---------------------------------------------------------------- monitoring
CREATE TABLE health_checks (        -- data/health_history.jsonl (web × den)
  check_id INTEGER PRIMARY KEY,     -- = ord
  date TEXT NOT NULL, ts TEXT, url TEXT NOT NULL, host TEXT,
  up INTEGER CHECK (up IN (0,1)), http_status INTEGER, final_url TEXT,
  response_ms INTEGER, error TEXT, extra_json TEXT,
  UNIQUE (url, date)
);
CREATE TABLE tls_certs (            -- health_checks.tls (1:1)
  check_id INTEGER PRIMARY KEY REFERENCES health_checks(check_id) ON DELETE CASCADE,
  ok INTEGER, version TEXT, issuer_o TEXT, issuer_cn TEXT, subject_cn TEXT, san_count INTEGER,
  not_before TEXT, not_after TEXT, days_to_expiry INTEGER, verify_error TEXT, error TEXT,
  extra_json TEXT
);
CREATE TABLE link_status (          -- data/link_status.json .links (poslední stav odkazu)
  url TEXT PRIMARY KEY, ord INTEGER NOT NULL,
  http_status INTEGER, final_url TEXT, method TEXT, response_ms INTEGER, error TEXT,
  social INTEGER, status TEXT CHECK (status IN ('ok','login_wall','broken','unknown')),
  checked_at TEXT, extra_json TEXT
);
CREATE TABLE link_status_sources (
  url TEXT NOT NULL REFERENCES link_status(url) ON DELETE CASCADE,
  ord INTEGER NOT NULL, source TEXT NOT NULL, PRIMARY KEY (url, ord)
);
CREATE TABLE link_history (         -- data/link_history.jsonl (odkaz × běh)
  id INTEGER PRIMARY KEY, date TEXT, ts TEXT, url TEXT NOT NULL,
  status TEXT CHECK (status IN ('ok','login_wall','broken','unknown')),
  http_status INTEGER, error TEXT, extra_json TEXT
);
CREATE INDEX ix_link_history_url ON link_history(url, date);

-- ---------------------------------------------------------------- archivace
CREATE TABLE archive_links (        -- data/archive_status.json .links
  url TEXT PRIMARY KEY, ord INTEGER NOT NULL,
  wayback_browse TEXT, archive_is_browse TEXT, extra_json TEXT
);
CREATE TABLE archive_snapshots (    -- stav u jednotlivých archivačních služeb
  url TEXT NOT NULL REFERENCES archive_links(url) ON DELETE CASCADE,
  service TEXT NOT NULL CHECK (service IN ('wayback','archive_is')),
  status TEXT, snapshot_url TEXT, timestamp TEXT, timestamp_iso TEXT, http_status INTEGER,
  location TEXT, checked_at TEXT, last_submit TEXT, last_submit_status TEXT,
  note TEXT, last_error TEXT, error TEXT, extra_json TEXT,
  PRIMARY KEY (url, service)
);
CREATE INDEX ix_archive_snapshots_status ON archive_snapshots(service, status);
CREATE TABLE archive_submit_log (   -- data/archive_submit_log.jsonl
  id INTEGER PRIMARY KEY, ts TEXT,
  service TEXT CHECK (service IN ('wayback','archive.today')),
  url TEXT, http_status INTEGER, secs REAL, status TEXT, timestamp TEXT,
  snapshot_url TEXT, location TEXT, detail TEXT, extra_json TEXT
);
CREATE INDEX ix_archive_submit_log_url ON archive_submit_log(url);

-- ---------------------------------------------------------------- X / sociální sítě
CREATE TABLE x_post_files (         -- data/x_posts.json (+ případné .bak, ord = priorita)
  file TEXT PRIMARY KEY, ord INTEGER NOT NULL, captured_at TEXT, method TEXT, note TEXT,
  extra_json TEXT
);
CREATE TABLE x_accounts (
  account_id INTEGER PRIMARY KEY,
  file TEXT NOT NULL REFERENCES x_post_files(file) ON DELETE CASCADE,
  ord INTEGER NOT NULL, handle TEXT, url TEXT, extra_json TEXT,
  UNIQUE (file, ord)
);
CREATE TABLE x_posts (
  account_id INTEGER NOT NULL REFERENCES x_accounts(account_id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, id TEXT, created_at TEXT, text TEXT, url TEXT, extra_json TEXT,
  PRIMARY KEY (account_id, ord)
);
CREATE TABLE x_tweets (             -- data/x_tweet_index.json (cache tweetů)
  id TEXT PRIMARY KEY, ord INTEGER NOT NULL,
  handle TEXT, text TEXT, created_at TEXT, url TEXT, src TEXT, extra_json TEXT
);
CREATE INDEX ix_x_tweets_handle ON x_tweets(handle);
CREATE TABLE permalink_hints (      -- další zdroje permalinků (zálohy captures, comments_inbox)
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, channel_url TEXT, text TEXT, url TEXT NOT NULL
);
CREATE TABLE alt_platforms (        -- data/alt_platforms.json
  ord INTEGER PRIMARY KEY, platform TEXT, name TEXT, url TEXT, handle TEXT,
  officiality TEXT, notes TEXT, evidence TEXT, extra_json TEXT
);
CREATE TABLE url_aliases (          -- data/url_aliases.json (stará → nová URL)
  old_url TEXT PRIMARY KEY, ord INTEGER NOT NULL, new_url TEXT NOT NULL
);

-- ---------------------------------------------------------------- ostatní
CREATE TABLE feeds (                -- data/feed_cache/*.json
  feed TEXT PRIMARY KEY, source TEXT, feed_url TEXT, fetched_via TEXT, fetched_at TEXT,
  extra_json TEXT
);
CREATE TABLE feed_items (
  feed TEXT NOT NULL REFERENCES feeds(feed) ON DELETE CASCADE,
  ord INTEGER NOT NULL, title TEXT, url TEXT, pub_date TEXT, extra_json TEXT,
  PRIMARY KEY (feed, ord)
);
CREATE TABLE daily_runs (           -- data/daily_run_*.json (protokol běhu, uložen jako JSON)
  date TEXT PRIMARY KEY, data_json TEXT NOT NULL
);
CREATE TABLE screenshots (          -- metadata souborů v screenshots/ (bez obsahu)
  path TEXT PRIMARY KEY, kind TEXT CHECK (kind IN ('tile','comment','archive','other')),
  bytes INTEGER, mtime TEXT
);
