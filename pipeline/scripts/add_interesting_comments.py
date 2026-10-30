#!/usr/bin/env python3
"""Merge a batch of 'Zajímavé komentáře' candidates into data/interesting_comments.jsonl.

Usage:
  python3 scripts/add_interesting_comments.py data/comments_inbox/2026-09-25.json [more files…]
  python3 scripts/add_interesting_comments.py --check FILE   # validate only, write nothing

Input: JSON list, {"candidates": [...]}, or JSONL (one candidate per line).
Candidate fields (see SKILL.md 'Zajímavé komentáře'):
  platform (fb|ig|x|yt), channel_url, post_url, post_title_or_excerpt,
  comment_url?, comment_text (verbatim), author_type (official_account|public_figure|anon),
  author_name? (only official_account/public_figure), comment_date_iso? | comment_date_raw?,
  reasons [fixed vocabulary], reason_note, engagement {likes, replies}?, screenshot?

Append-only + dedupe: key = channel (same channel_key as archive_updates.py)
+ normalized comment text (first 120 chars). Existing entries keep id,
first_seen and comment_date_iso; last_seen / engagement / reasons are updated.
Private persons are anonymized defensively (name dropped, @mentions masked).

Comments are NOT censored for unproven claims (since 2026-10-21): instead each
comment carries `verification` {status, checked_at, verifier, summary, sources:[{title,url}]}
(status: see VERIFICATION_STATUSES). Only comments with accusations / disputed
factual claims get a verification; all others have none (no label). Apply/replace verifications by id:
  python3 scripts/add_interesting_comments.py --verify data/verifications/YYYY-MM-DD.json
  (JSON list of {"id": "...", "verification": {...}} or {"comment_text": "...", "verification": {...}})
  python3 scripts/add_interesting_comments.py --unverified   # list ids still lacking verification
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "interesting_comments.jsonl"

REASONS = [
    "insider info / informace zevnitř",
    "kritika / stížnost",
    "otázka s odpovědí úřadu",
    "odpověď oficiálního účtu",
    "dezinformace / propaganda",
    "nová informace / fakt",
    "výrazný ohlas",
    "nábor / zkušenost z AZ",
    "bezpečnostní riziko (OPSEC)",
]
PLATFORMS = {"fb", "ig", "x", "yt"}
VERIFICATION_STATUSES = {
    "potvrzeno", "částečně pravdivé", "vyvráceno", "nepodloženo / nelze ověřit",
}
VERIFIER = "Grok Bot"
AUTHOR_TYPES = {"official_account", "public_figure", "anon"}
ANON_LABEL = "uživatel"

# @handles that may stay visible in comment text (official / public accounts).
ALLOWED_HANDLES = {
    "armadacr", "obranatweetuje", "armada_cr", "armadaceskerepubliky", "doarmady", "do_armady",
    "aktivnizalohy", "aktivnizaloha", "ministerstvoobrany", "ministerstvoobranyceskerepubliky", "doarmady.cz", "tvarmycz", "aztvcz", "ngs_acr",
    "vzpravodajstvi", "ovv_mo_cr", "nato", "karlovarskykraj",
}

PLATFORM_HOST = {"fb": "facebook.com", "ig": "instagram.com", "x": "x.com", "yt": "youtube.com"}


def _archive():
    spec = importlib.util.spec_from_file_location("archive_updates", ROOT / "scripts" / "archive_updates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AM = _archive()
channel_key = AM.channel_key


def norm_comment(t: str) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip().lower()
    t = t.rstrip("…").rstrip(".").strip()
    return t[:120]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_REL = [
    (r"(\d+)\s*(?:s|sec|sekund\w*|second\w*)\b", "seconds"),
    (r"(\d+)\s*(?:m|min|mins|minut\w*|minute\w*)\b", "minutes"),
    (r"(\d+)\s*(?:h|hr|hrs|hod\w*|hour\w*)\b", "hours"),
    (r"(\d+)\s*(?:d|dn\w*|den|dny|day\w*)\b", "days"),
    (r"(\d+)\s*(?:w|t|týd\w*|week\w*)\b", "weeks"),
    (r"(\d+)\s*(?:měs\w*|mes\w*|month\w*|mo)\b", "months"),
    (r"(\d+)\s*(?:r|y|rok\w*|let|lety|year\w*)\b", "years"),
]
_REL_ONE = [  # "před hodinou", "před rokem", "a year ago"
    (r"(?:před\s+)?(?:minutou|a minute)", "minutes"), (r"(?:před\s+)?(?:hodinou|an hour)", "hours"),
    (r"(?:před\s+)?(?:dnem|a day)|včera|yesterday", "days"), (r"(?:před\s+)?(?:týdnem|a week)", "weeks"),
    (r"(?:před\s+)?(?:měsícem|a month)", "months"), (r"(?:před\s+)?(?:rokem|a year)", "years"),
]


def resolve_date(raw: str, iso: str, first_seen: str) -> tuple[str, bool]:
    """Return (comment_date_iso, approx). Absolute ISO wins; relative text is
    resolved against first_seen (approx=True); unknown -> first_seen (approx)."""
    if iso:
        return iso, False
    raw = str(raw or "").strip().lower().replace("(upraveno)", "").replace("(edited)", "").strip()
    base = datetime.fromisoformat(first_seen)
    if raw:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
        if m:
            return m.group(1), False
        m = re.match(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", raw)
        if m:
            return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}", False
        n, unit = None, None
        for pat, u in _REL:
            mm = re.search(pat, raw)
            if mm:
                n, unit = int(mm.group(1)), u
                break
        if unit is None:
            for pat, u in _REL_ONE:
                if re.search(pat, raw):
                    n, unit = 1, u
                    break
        if unit:
            if unit == "months":
                d = base - timedelta(days=30 * n)
            elif unit == "years":
                d = base - timedelta(days=365 * n)
            else:
                d = base - timedelta(**{unit: n})
            if unit in ("seconds", "minutes", "hours"):
                return d.isoformat(timespec="minutes"), True
            return d.date().isoformat(), True
    return first_seen, True


def mask_mentions(text: str) -> tuple[str, bool]:
    changed = False

    def rep(m):
        nonlocal changed
        h = m.group(1)
        if h.lower() in ALLOWED_HANDLES:
            return m.group(0)
        changed = True
        return "@" + ANON_LABEL

    out = re.sub(r"(?<![\w.])@([A-Za-z0-9_.\-À-ž]{2,40})", rep, text)
    return out, changed


def validate(c: dict, i: int) -> list[str]:
    errs = []
    if (c.get("platform") or "").lower() not in PLATFORMS:
        errs.append(f"[{i}] platform must be one of {sorted(PLATFORMS)}")
    if not (c.get("comment_text") or "").strip():
        errs.append(f"[{i}] comment_text missing")
    if not (c.get("post_url") or "").startswith("http"):
        errs.append(f"[{i}] post_url missing")
    if not (c.get("channel_url") or c.get("channel")):
        errs.append(f"[{i}] channel_url missing")
    rs = c.get("reasons") or []
    if not rs:
        errs.append(f"[{i}] reasons empty")
    bad = [r for r in rs if r not in REASONS]
    if bad:
        errs.append(f"[{i}] unknown reasons {bad}; allowed: {REASONS}")
    at = c.get("author_type") or "anon"
    if at not in AUTHOR_TYPES:
        errs.append(f"[{i}] author_type must be one of {sorted(AUTHOR_TYPES)}")
    v = c.get("verification")
    if v is not None:
        errs += [f"[{i}] {e}" for e in validate_verification(v)]
    return errs


def validate_verification(v) -> list[str]:
    errs = []
    if not isinstance(v, dict):
        return ["verification must be an object"]
    if v.get("status") not in VERIFICATION_STATUSES:
        errs.append(f"verification.status must be one of {sorted(VERIFICATION_STATUSES)}")
    try:
        datetime.fromisoformat(str(v.get("checked_at")))
    except Exception:
        errs.append("verification.checked_at must be ISO 8601 with offset")
    srcs = v.get("sources") or []
    for s_ in srcs:
        if not (isinstance(s_, dict) and str(s_.get("url", "")).startswith("http") and s_.get("title")):
            errs.append(f"verification.sources item needs title+url: {s_}")
    if v.get("status") in ("potvrzeno", "vyvráceno", "částečně pravdivé") and not srcs:
        errs.append("verification: potvrzeno/vyvráceno/částečně pravdivé requires sources")
    return errs


def norm_verification(v):
    if not v:
        return None
    return {
        "status": v["status"],
        "checked_at": v["checked_at"],
        "verifier": VERIFIER,
        "summary": re.sub(r"\s+", " ", str(v.get("summary") or "")).strip(),
        "sources": [{"title": str(x["title"]).strip(), "url": x["url"]} for x in (v.get("sources") or [])],
    }


def normalize(c: dict, ts: str) -> dict:
    platform = c["platform"].lower()
    ch_url = c.get("channel_url") or ""
    ch = c.get("channel") or channel_key(ch_url)
    if not ch_url and ch:
        ch_url = "https://" + ch
    at = c.get("author_type") or "anon"
    name = (c.get("author_name") or "").strip() or None
    if at not in ("official_account", "public_figure"):
        at, name = "anon", None  # never store private-person names
    text, masked = mask_mentions(str(c["comment_text"]).strip())
    first_seen = c.get("first_seen") or ts
    date_iso, approx = resolve_date(c.get("comment_date_raw"), c.get("comment_date_iso"), first_seen)
    eng = c.get("engagement") or {}
    eng = {k: v for k, v in eng.items() if k in ("likes", "replies", "reactions", "shares") and v not in (None, "")}
    for k, v in list(eng.items()):
        if isinstance(v, str) and re.fullmatch(r"\d+", v.strip()):
            eng[k] = int(v)
    note = re.sub(r"\s+", " ", str(c.get("reason_note") or "")).strip()
    return {
        "id": hashlib.sha1(f"{ch}|{norm_comment(text)}".encode()).hexdigest()[:14],
        "platform": platform,
        "channel": ch,
        "channel_url": ch_url,
        "post_url": c["post_url"],
        "post_title_or_excerpt": re.sub(r"\s+", " ", str(c.get("post_title_or_excerpt") or "")).strip()[:300],
        "comment_url": c.get("comment_url") or None,
        "comment_text": text,
        "author_type": at,
        "author": name if name else ANON_LABEL,
        "author_name": name,
        "comment_date_iso": date_iso,
        "comment_date_raw": c.get("comment_date_raw") or None,
        "comment_date_approx": approx,
        "first_seen": first_seen,
        "last_seen": c.get("last_seen") or ts,
        "reasons": list(dict.fromkeys(c.get("reasons") or [])),
        "reason_note": note,
        "engagement": eng,
        "screenshot": c.get("screenshot") or None,
        "source_method": c.get("source_method") or None,
        "anonymized": at == "anon" or masked,
        "verification": norm_verification(c.get("verification")),
    }


def load_candidates(path: Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8").strip()
    if not txt:
        return []
    try:
        d = json.loads(txt)
        if isinstance(d, dict):
            d = d.get("candidates") or d.get("comments") or []
        return list(d)
    except json.JSONDecodeError:
        return [json.loads(l) for l in txt.splitlines() if l.strip()]


def load_existing() -> list[dict]:
    if not OUT.exists():
        return []
    rows = []
    for l in OUT.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if l:
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    return rows


def merge(rows: list[dict], new: dict) -> str:
    key = (new["channel"], norm_comment(new["comment_text"]))
    for r in rows:
        if (r.get("channel"), norm_comment(r.get("comment_text"))) == key:
            r["last_seen"] = max(r.get("last_seen") or "", new["last_seen"])
            if new["engagement"]:
                r["engagement"] = new["engagement"]
            r["reasons"] = list(dict.fromkeys((r.get("reasons") or []) + new["reasons"]))
            if r.get("comment_date_approx") and not new["comment_date_approx"]:
                r["comment_date_iso"], r["comment_date_approx"] = new["comment_date_iso"], False
            if new.get("verification"):
                r["verification"] = new["verification"]
            for k in ("comment_url", "screenshot", "reason_note", "post_title_or_excerpt"):
                if not r.get(k) and new.get(k):
                    r[k] = new[k]
            return "updated"
    rows.append(new)
    return "added"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--verify", action="store_true", help="files are verification batches (by id / comment_text)")
    ap.add_argument("--unverified", action="store_true", help="list comments without verification")
    a = ap.parse_args(argv)
    ts = now_iso()
    if a.unverified:
        for r in load_existing():
            if not r.get("verification"):
                print(r["id"], "|", r.get("comment_text", "")[:160].replace("\n", " "))
        return 0
    if a.verify:
        rows = load_existing()
        by_id = {r["id"]: r for r in rows}
        by_text = {norm_comment(r.get("comment_text")): r for r in rows}
        n, errs = 0, []
        for f in a.files:
            for i, item in enumerate(load_candidates(f)):
                r = by_id.get(item.get("id")) or by_text.get(norm_comment(item.get("comment_text")))
                e = validate_verification(item.get("verification"))
                if r is None:
                    e.append("no matching comment")
                if e:
                    errs += [f"{f.name}[{i}] {x}" for x in e]
                    continue
                r["verification"] = norm_verification(item["verification"])
                n += 1
        for e in errs:
            print("REJECT", e, file=sys.stderr)
        if not a.check:
            OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(f"verification: applied={n} rejected={len(errs)} still_unverified={sum(1 for r in rows if not r.get('verification'))}")
        return 1 if errs else 0
    cands, errs = [], []
    for f in a.files:
        for c in load_candidates(f):
            i = len(cands)
            e = validate(c, i)
            errs += [f"{f.name}{x}" for x in e]
            if not e:
                cands.append(c)
    for e in errs:
        print("REJECT", e, file=sys.stderr)
    if a.check:
        print(f"valid={len(cands)} rejected={len(errs)}")
        return 1 if errs else 0
    rows = load_existing()
    stats = {"added": 0, "updated": 0}
    for c in cands:
        stats[merge(rows, normalize(c, ts))] += 1
    rows.sort(key=lambda r: (r.get("comment_date_iso") or "", r.get("first_seen") or ""), reverse=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"interesting_comments: total={len(rows)} added={stats['added']} updated={stats['updated']} rejected={len(errs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
