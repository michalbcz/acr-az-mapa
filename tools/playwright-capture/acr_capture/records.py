"""captures.jsonl compatibility layer.

* Same record schema as ``report/data/captures.jsonl`` (url, slug,
  screenshot_path, last_post_title/author/date, status, hidden_links, notes …).
* ``build_site_v2.py`` de-duplicates by URL with *last line wins*, so merging
  = appending a new line per refreshed URL (after a timestamped backup).
* Existing URLs keep their screenshot filename (``find_shot`` globs by stem),
  so the site picks up the new image without any change to the build.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

GOOD_STATUSES = {"ok"}
# results that say "we couldn't see the page" — they never replace a capture that did see something
FAILED_STATUSES = {"login_wall", "blocked", "error"}


def slug_for(url: str) -> str:
    """Slug in the style of the newer captures (``facebook-com-casopisatm``)."""
    p = urlparse(url)
    host = p.netloc.lower().replace("www.", "").replace(".mo.gov.cz", "").replace(".", "-")
    path = p.path.strip("/").replace("/", "-").replace("@", "")
    q = "-" + re.sub(r"[^a-zA-Z0-9]+", "-", p.query).strip("-") if p.query else ""
    slug = (f"{host}-{path}" if path else host) + q
    parts = slug.split("-")
    if len(parts) > 12:
        slug = "-".join(parts[:12])
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-").lower()[:90]


def norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/").lower().replace("://www.", "://").replace("http://", "https://")


def load_captures(path: Path | None) -> dict[str, dict]:
    """url-normalised → last record (same semantics as build_site_v2)."""
    out: dict[str, dict] = {}
    if not path or not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("url"):
            out[norm_url(r["url"])] = r
    return out


def target_filename(url: str, existing: dict | None, social_ext: str = ".png") -> tuple[str, str]:
    """Return (slug, filename) — reusing the existing screenshot name if any."""
    if existing:
        sp = existing.get("screenshot_path") or existing.get("screenshot") or ""
        if sp:
            name = Path(sp).name
            if Path(name).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                return existing.get("slug") or Path(name).stem, name
        if existing.get("slug"):
            return existing["slug"], existing["slug"] + social_ext
    s = slug_for(url)
    return s, s + social_ext


def merge_record(old: dict | None, new: dict, keep_good: bool = True) -> tuple[dict, bool]:
    """Merge a fresh capture into the previous record.

    Returns (record, accepted_screenshot). With ``keep_good`` a failed refresh
    (login wall / blocked / error) never overwrites a previous capture that did
    show content (ok, unavailable, not_found, …): status + screenshot stay, only
    a note is added.
    """
    if not old:
        return new, True  # new URL: keep whatever we saw (login wall screenshot is evidence too)
    rec = dict(old)
    degraded = (keep_good and new.get("status") in FAILED_STATUSES
                and old.get("status") not in FAILED_STATUSES)
    notes = [n for n in (old.get("notes") or "").split("; ") if n and not n.startswith("playwright_")]
    if degraded:
        notes.append(f"playwright_refresh_failed={new.get('status')}@{new.get('captured_at', '')[:10]}")
        rec["notes"] = "; ".join(notes)
        return rec, False
    # a dated previous post beats an undated new guess (e.g. "aktuality_section" heuristics)
    keep_old_post = bool(old.get("last_post_date")) and not new.get("last_post_date")
    for k, v in new.items():
        if k in ("notes", "hidden_links"):
            continue
        if k.startswith("last_post") and (keep_old_post or not v):
            continue  # don't erase a known last post just because extraction failed today
        if v is not None:
            rec[k] = v
    # union of hidden links
    seen = set()
    links = []
    for h in (old.get("hidden_links") or []) + (new.get("hidden_links") or []):
        key = (h.get("url") if isinstance(h, dict) else str(h)).lower()
        if key not in seen:
            seen.add(key)
            links.append(h)
    if links or "hidden_links" in old:
        rec["hidden_links"] = links
    new_notes = [n for n in (new.get("notes") or "").split("; ") if n]
    rec["notes"] = "; ".join(dict.fromkeys(notes + new_notes))
    return rec, True


def backup_file(path: Path, tag: str) -> Path | None:
    if not path.is_file():
        return None
    dest = path.with_name(f"{path.name}.bak-pre-{tag}")
    if not dest.exists():
        shutil.copy2(path, dest)
    return dest


def archive_screenshot(shots_dir: Path, name: str, day: str) -> Path | None:
    """Same layout as scripts/backup_screenshots.py: screenshots/archive/YYYY-MM-DD/<name>."""
    src = shots_dir / name
    if not src.is_file():
        return None
    dest = shots_dir / "archive" / day / name
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def write_into_report(report_root: Path, run_records: list[dict], run_shots_dir: Path,
                      keep_good: bool = True, dry_run: bool = False) -> dict:
    """Copy screenshots into report/screenshots and append merged records to captures.jsonl."""
    cap = report_root / "data" / "captures.jsonl"
    shots = report_root / "screenshots"
    existing = load_captures(cap)
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    tag = "playwright-" + now.strftime("%Y%m%d-%H%M%S")
    summary = {"appended": 0, "screenshots_replaced": 0, "kept_previous": 0, "backup": None, "archived": 0}
    lines = []
    for r in run_records:
        old = existing.get(norm_url(r["url"]))
        merged, accept_shot = merge_record(old, r, keep_good=keep_good)
        fname = Path(r.get("screenshot_path") or "").name
        if accept_shot and fname and (run_shots_dir / fname).is_file():
            if not dry_run:
                shots.mkdir(parents=True, exist_ok=True)
                if archive_screenshot(shots, fname, day):
                    summary["archived"] += 1
                shutil.copy2(run_shots_dir / fname, shots / fname)
            merged["screenshot_path"] = f"screenshots/{fname}"
            summary["screenshots_replaced"] += 1
        elif not accept_shot:
            summary["kept_previous"] += 1
        lines.append(json.dumps(merged, ensure_ascii=False))
    if lines and not dry_run:
        summary["backup"] = str(backup_file(cap, tag)) if cap.exists() else None
        cap.parent.mkdir(parents=True, exist_ok=True)
        needs_nl = cap.exists() and cap.stat().st_size > 0 and not cap.read_bytes().endswith(b"\n")
        with cap.open("a", encoding="utf-8") as f:
            if needs_nl:
                f.write("\n")
            for ln in lines:
                f.write(ln + "\n")
    summary["appended"] = len(lines)
    return summary
