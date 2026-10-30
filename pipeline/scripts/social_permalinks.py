#!/usr/bin/env python3
"""Permalinks of concrete social-network posts (X, Facebook, Instagram, YouTube, …).

Goal: every "zdroj"/source link of a social post points to the post itself
(x.com/<handle>/status/<id>, facebook.com/<page>/posts/…, /reel/…, instagram.com/p/<code>/,
youtube.com/watch?v=…), never only to the account.  Nothing is invented: a permalink
is used only when it exists in our own collected data:

  * data/x_posts.json (+ .bak*) – syndication posts with ``id`` / ``url``
  * data/x_tweet_index.json      – persistent cache of tweets parsed from raw
                                   syndication dumps (``id_str`` from __NEXT_DATA__)
  * data/captures.jsonl (+ .bak*) – ``last_post_url`` of social captures
  * data/interesting_comments.jsonl + data/comments_inbox/*.json – ``post_url``

Matching = same channel (archive_updates.channel_key) + same normalized text
(exact, or a ≥25-char prefix of the other text for truncated captures).

CLI:
  python3 scripts/social_permalinks.py --refresh-x-cache [raw files…]
        parse raw syndication dumps (default locations + given files) into data/x_tweet_index.json
  python3 scripts/social_permalinks.py --backfill [--dry-run]
        fill ``post_url`` into data/updates_history.jsonl rows where a permalink is known
"""
from __future__ import annotations

import glob
import gzip
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
X_CACHE = DATA / "x_tweet_index.json"

SOCIAL = ("facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "youtu.be",
          "linkedin.com", "tiktok.com")


def platform(url: str) -> str:
    h = urlparse(url or "").netloc.lower()
    if "facebook.com" in h or h == "fb.watch":
        return "fb"
    if "instagram.com" in h:
        return "ig"
    if h.replace("www.", "").replace("mobile.", "") in ("x.com", "twitter.com"):
        return "x"
    if "youtube.com" in h or "youtu.be" in h:
        return "yt"
    if "linkedin.com" in h:
        return "li"
    if "tiktok.com" in h:
        return "tt"
    return ""


def is_social(url: str) -> bool:
    return bool(platform(url))


def is_post_permalink(url: str) -> bool:
    """True only for a URL of one concrete post/video (not a profile/page/group root)."""
    if not url or not str(url).startswith("http"):
        return False
    p = urlparse(url)
    segs = [s for s in (p.path or "").split("/") if s]
    q = parse_qs(p.query or "")
    pf = platform(url)
    if pf == "x":
        return len(segs) >= 3 and segs[1] == "status" and segs[2].isdigit()
    if pf == "fb":
        if "story_fbid" in q or ("fbid" in q and "photo" in p.path):
            return True
        if segs and segs[0] == "watch" and "v" in q:
            return True
        if segs and segs[0] in ("reel", "videos", "watch") and len(segs) >= 2:
            return True
        return len(segs) >= 3 and segs[1] in ("posts", "videos", "photos", "reel", "permalink")
    if pf == "ig":
        return any(s in ("p", "reel", "reels", "tv") for s in segs[:2]) and len(segs) >= 2
    if pf == "yt":
        if "youtu.be" in p.netloc:
            return len(segs) == 1
        if segs and segs[0] == "watch":
            return "v" in q
        return len(segs) >= 2 and segs[0] in ("shorts", "live", "post")
    if pf == "li":
        return len(segs) >= 2 and segs[0] in ("posts", "feed", "pulse")
    if pf == "tt":
        return "video" in segs
    return False


def x_status_url(handle: str, tid) -> str:
    tid = str(tid or "").strip()
    handle = (handle or "").strip().lstrip("@")
    if not tid.isdigit() or not handle:
        return ""
    return f"https://x.com/{handle}/status/{tid}"


# ---------------------------------------------------------------------------
# helpers shared with archive_updates (imported lazily to avoid cycles)
# ---------------------------------------------------------------------------
def _norm(t: str, strip_rt: bool = True) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip().lower()
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    # X retweets: syndication text "RT @user: …" vs. stored retweeted text – same post on this account
    if strip_rt:
        t = re.sub(r"^rt @\w+:\s*", "", t)
    return t.rstrip("…").rstrip(".").strip()


def _channel_key(url: str) -> str:
    sys.path.insert(0, str(ROOT / "scripts"))
    import archive_updates  # noqa: E402
    return archive_updates.channel_key(url)


def texts_match(a: str, b: str) -> bool:
    a, b = _norm(a)[:200], _norm(b)[:200]
    if not a or not b:
        return False
    if a[:120] == b[:120]:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 25 and long_.startswith(short)


# ---------------------------------------------------------------------------
# X: raw syndication dumps → persistent tweet cache
# ---------------------------------------------------------------------------
RAW_GLOBS = [
    str(ROOT / "screenshots" / "**" / "*syndication*.html"),
    str(ROOT / "screenshots" / "**" / "x-*-dump.html"),
    str(DATA / "x_raw" / "*"),
    "/tmp/x_*.html", "/tmp/synd_*.html", "/tmp/x_synd_*.json", "/tmp/x_synd_*.bin",
]


def _read_raw(path: str) -> str:
    b = Path(path).read_bytes()
    if b[:2] == b"\x1f\x8b":
        try:
            b = gzip.decompress(b)
        except Exception:
            return ""
    return b.decode("utf-8", errors="replace")


def _walk_tweets(obj, out):
    if isinstance(obj, dict):
        if obj.get("id_str") and (obj.get("full_text") or obj.get("text")) and isinstance(obj.get("user"), dict):
            out.append(obj)
        for v in obj.values():
            _walk_tweets(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_tweets(v, out)


def parse_raw_tweets(path: str) -> list:
    s = _read_raw(path)
    if not s:
        return []
    blobs = []
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
    if m:
        blobs.append(m.group(1))
    elif s.lstrip().startswith("{"):
        blobs.append(s)
    out = []
    for blob in blobs:
        try:
            _walk_tweets(json.loads(blob), out)
        except Exception:
            continue
    tweets = []
    for t in out:
        handle = (t.get("user") or {}).get("screen_name") or ""
        tid = str(t.get("id_str") or "")
        url = x_status_url(handle, tid)
        if not url:
            continue
        tweets.append({"id": tid, "handle": handle, "text": t.get("full_text") or t.get("text") or "",
                       "created_at": t.get("created_at") or "", "url": url, "src": Path(path).name})
    return tweets


def load_x_cache() -> dict:
    if X_CACHE.exists():
        try:
            return json.loads(X_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def refresh_x_cache(extra=()) -> tuple[int, int]:
    cache = load_x_cache()
    n0 = len(cache)
    files = set(extra)
    for g in RAW_GLOBS:
        files.update(glob.glob(g, recursive=True))
    for f in sorted(files):
        try:
            for t in parse_raw_tweets(f):
                cache.setdefault(t["id"], t)
        except Exception:
            continue
    # x_posts.json (+ backups): posts carrying id/url
    for xp in [DATA / "x_posts.json"] + sorted(DATA.glob("x_posts.json.bak*")):
        try:
            d = json.loads(xp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for acc in d.get("accounts") or []:
            handle = acc.get("handle") or ""
            for po in acc.get("posts") or []:
                url = po.get("url") if is_post_permalink(po.get("url") or "") else x_status_url(handle, po.get("id"))
                if not url:
                    continue
                tid = url.rstrip("/").split("/")[-1]
                cache.setdefault(tid, {"id": tid, "handle": handle, "text": po.get("text") or po.get("title") or "",
                                       "created_at": po.get("created_at") or "", "url": url, "src": xp.name})
    X_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return n0, len(cache)


# ---------------------------------------------------------------------------
# Permalink index: (channel_key) → [(text, url)]
# ---------------------------------------------------------------------------
class PermalinkIndex:
    def __init__(self):
        self.by_ch: dict = {}
        self._load()

    def _add(self, channel_url_or_key, text, url, is_key=False):
        if not text or not is_post_permalink(url):
            return
        ck = channel_url_or_key if is_key else _channel_key(channel_url_or_key)
        if not ck:
            return
        self.by_ch.setdefault(ck, []).append((text, url))

    def _load(self):
        for t in load_x_cache().values():
            self._add("https://x.com/" + t.get("handle", ""), t.get("text"), t.get("url"))
        for xp in [DATA / "x_posts.json"] + sorted(DATA.glob("x_posts.json.bak*")):
            try:
                d = json.loads(xp.read_text(encoding="utf-8"))
            except Exception:
                continue
            for acc in d.get("accounts") or []:
                base = acc.get("url") or "https://x.com/" + (acc.get("handle") or "")
                for po in acc.get("posts") or []:
                    url = po.get("url") or x_status_url(acc.get("handle"), po.get("id"))
                    for t in (po.get("text"), po.get("title")):
                        self._add(base, t, url)
        for cp in [DATA / "captures.jsonl"] + sorted(DATA.glob("captures.jsonl.bak*"), reverse=True):
            try:
                lines = cp.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for line in lines:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not is_social(r.get("url") or ""):
                    continue
                lp = r.get("last_post") if isinstance(r.get("last_post"), dict) else {}
                # pair a URL only with the title captured together with it (other
                # fields such as last_post / last_post_caption may be stale)
                self._add(r["url"], r.get("last_post_title"), r.get("last_post_url"))
                self._add(r["url"], lp.get("title"), lp.get("url"))
                for rp in r.get("recent_posts") or []:
                    if isinstance(rp, dict):
                        self._add(r["url"], rp.get("title") or rp.get("text"), rp.get("url") or rp.get("post_url"))
        comment_files = [DATA / "interesting_comments.jsonl"]
        for cf in comment_files:
            if not cf.exists():
                continue
            for line in cf.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                self._add(o.get("channel_url") or "", o.get("post_title_or_excerpt"), o.get("post_url"))
        for f in sorted((DATA / "comments_inbox").glob("*.json")):
            try:
                rows = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for o in rows if isinstance(rows, list) else []:
                if isinstance(o, dict):
                    self._add(o.get("channel_url") or "", o.get("post_title_or_excerpt"), o.get("post_url"))

    def lookup(self, channel_url: str, text: str) -> str:
        """Permalink for (channel, text) or '' – first exact match, else unique prefix match."""
        if not text:
            return ""
        ck = _channel_key(channel_url)
        cands = self.by_ch.get(ck) or []
        for strip_rt in (False, True):
            nt = _norm(text, strip_rt)[:120]
            exact = [u for t, u in cands if _norm(t, strip_rt)[:120] == nt]
            if exact:
                # several sources for one post: most frequently seen URL, then the longest
                # (guards against a hand-copied FB pfbid with a dropped character)
                return max(exact, key=lambda u: (exact.count(u), len(u)))
        hits = {u for t, u in cands if texts_match(t, text)}
        # different pfbid encodings of one FB post are fine; ambiguous X ids are not
        if len(hits) == 1 or (hits and all(platform(u) == "fb" for u in hits)):
            return sorted(hits)[0]
        return ""


def fill_post_urls(rows, index: PermalinkIndex | None = None) -> int:
    """Set ``post_url`` on social history rows (dicts) where a permalink is known. Returns #filled."""
    index = index or PermalinkIndex()
    n = 0
    for r in rows:
        url = r.get("url") or ""
        if not is_social(url):
            continue
        if is_post_permalink(r.get("post_url") or ""):
            continue
        cand = next((u for u in [url] + list(r.get("urls") or []) if is_post_permalink(u)), "")
        if not cand:
            cand = index.lookup(url, r.get("title") or "")
        if cand:
            r["post_url"] = cand
            n += 1
    return n


def main(argv):
    if "--refresh-x-cache" in argv:
        extra = [a for a in argv if not a.startswith("--")]
        n0, n1 = refresh_x_cache(extra)
        print(f"x_tweet_index: {n0} -> {n1} tweets ({X_CACHE})")
    if "--backfill" in argv:
        hp = DATA / "updates_history.jsonl"
        rows = [json.loads(l) for l in hp.read_text(encoding="utf-8").splitlines() if l.strip()]
        n = fill_post_urls(rows)
        soc = [r for r in rows if is_social(r.get("url") or "")]
        have = sum(1 for r in soc if is_post_permalink(r.get("post_url") or ""))
        print(f"backfill: +{n} post_url; social rows {len(soc)}, with permalink {have}, account-only {len(soc) - have}")
        if "--dry-run" not in argv:
            hp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    if not any(a in argv for a in ("--refresh-x-cache", "--backfill")):
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
