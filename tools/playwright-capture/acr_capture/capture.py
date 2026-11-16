"""Main capture loop: open each target like a real visitor, screenshot, extract, record."""
from __future__ import annotations

import json
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import extract
from .blur import blur_viewer_identity
from .browser import (
    between_pages_delay, browser_session, close_login_overlays, context_has_cookies, dismiss_consent,
    hide_viewer_identity, human_mouse_and_scroll, new_context, wait_settled,
)
from .config import Settings
from .cookies import state_path
from .records import load_captures, norm_url, target_filename, write_into_report
from .services import SERVICES, is_facebook_group
from .targets import Target


@dataclass
class CaptureOptions:
    full_page: bool = False
    blur: str = "auto"            # auto | always | never
    social_format: str = "png"    # png | jpg (only for URLs without an existing screenshot name)
    use_sessions: bool = True
    refresh_sessions: bool = True  # write back rolled cookies after the run
    write_report: bool = False
    keep_good: bool = True
    save_html: bool = True
    headless: bool | None = None
    run_dir: Path | None = None


def plan(settings: Settings, targets: list[Target], opts: CaptureOptions) -> list[dict]:
    existing = load_captures(settings.captures_jsonl)
    rows = []
    for t in targets:
        old = existing.get(norm_url(t.url))
        slug, fname = target_filename(t.url, old, "." + opts.social_format)
        sp = state_path(settings.secrets_dir, t.service) if t.service in SERVICES else None
        rows.append({
            "url": t.url, "service": t.service, "category": t.category, "slug": slug, "file": fname,
            "known_in_captures": bool(old), "previous_status": (old or {}).get("status"),
            "session": ("yes" if sp and sp.is_file() else "no") if sp else "-",
        })
    return rows


def _capture_one(page, ctx, t: Target, fname: str, slug: str, shots_dir: Path, html_dir: Path,
                 settings: Settings, opts: CaptureOptions, logged_in: bool) -> dict:
    svc = SERVICES.get(t.service)
    notes: list[str] = ["playwright"]
    http_code = None
    shot_path = shots_dir / fname
    html = ""
    try:
        resp = page.goto(t.url, wait_until="domcontentloaded", timeout=settings.nav_timeout_ms)
        http_code = resp.status if resp else None
    except Exception as ex:
        notes.append(f"goto_error={type(ex).__name__}")
    notes += wait_settled(page, settings, svc.ready_selector if svc else None)
    clicked = dismiss_consent(page, settings)
    if clicked:
        notes.append("consent_dismissed")
    if svc and not logged_in and close_login_overlays(page, svc):
        notes.append("login_overlay_closed")
    human_mouse_and_scroll(page, settings, depth_px=1800 if svc else 1200)
    if svc and logged_in:
        n = hide_viewer_identity(page, svc)
        notes.append(f"viewer_identity_css_blur={n}")
    try:
        html = page.content()
    except Exception:
        html = ""
    shot_ok = False
    try:
        kw = {"path": str(shot_path), "full_page": opts.full_page, "animations": "disabled"}
        if shot_path.suffix.lower() in (".jpg", ".jpeg"):
            kw.update(type="jpeg", quality=85)
        page.screenshot(**kw)
        shot_ok = shot_path.is_file() and shot_path.stat().st_size > 1000
    except Exception as ex:
        notes.append(f"screenshot_error={type(ex).__name__}")

    if shot_ok and svc and (opts.blur == "always" or (opts.blur == "auto" and logged_in)):
        impl = blur_viewer_identity(shot_path, t.url, settings.report_root)
        notes.append("viewer identity blurred" if impl.startswith("report") else "viewer identity blurred (bundled)")

    if opts.save_html and html:
        (html_dir / f"{slug}.html").write_text(html, encoding="utf-8", errors="replace")

    status = extract.detect_status(t.url, html, http_code, shot_ok, logged_in)
    rec: dict = {
        "url": t.url,
        "slug": slug,
        "screenshot_path": f"screenshots/{fname}" if shot_ok else None,
        "last_post_title": None,
        "last_post_author": None,
        "last_post_date": None,
        "status": status,
        "hidden_links": [],
        "captured_at": extract.now_iso(),
        "source": "playwright",
    }
    if svc:
        fields, n2 = extract.extract_social_post(page, svc) if status == "ok" else ({}, [])
        rec.update(fields)
        notes += n2
        if logged_in:
            notes.append("logged-in capture")
        if is_facebook_group(t.url) and status == "private_group":
            notes.append("private group — not joined (policy)")
    else:
        if html:
            title, author, date_raw, n2 = extract.extract_latest_post_html(html, t.url)
            rec.update({"last_post_title": title, "last_post_author": author, "last_post_date": date_raw})
            notes += n2
            rec["hidden_links"] = extract.find_hidden_links(html, t.url)
            if status == "ok" and not title:
                notes.append("no_dated_news_on_page")
    if http_code:
        notes.append(f"http={http_code}")
    rec["notes"] = "; ".join(notes)
    return rec


def run(settings: Settings, targets: list[Target], opts: CaptureOptions, log=print) -> tuple[list[dict], Path, dict | None]:
    run_dir = opts.run_dir or (settings.out_dir / datetime.now().strftime("run-%Y%m%d-%H%M%S"))
    shots_dir = run_dir / "screenshots"
    html_dir = run_dir / "html"
    shots_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = run_dir / "captures.jsonl"
    existing = load_captures(settings.captures_jsonl)

    groups: "OrderedDict[str, list[Target]]" = OrderedDict()
    for t in targets:
        groups.setdefault(t.service, []).append(t)

    records: list[dict] = []
    with browser_session(settings, headless=opts.headless) as (_pw, browser), out_jsonl.open("a", encoding="utf-8") as fh:
        for service_key, items in groups.items():
            svc = SERVICES.get(service_key)
            sp = state_path(settings.secrets_dir, service_key) if svc else None
            use_state = bool(svc and opts.use_sessions and sp and sp.is_file())
            ctx = new_context(browser, settings, storage_state=sp if use_state else None)
            logged_in = bool(svc and use_state and context_has_cookies(ctx, svc))
            log(f"== {service_key}: {len(items)} URL(s); session={'yes' if logged_in else 'no'}")
            page = ctx.new_page()
            for i, t in enumerate(items, 1):
                old = existing.get(norm_url(t.url))
                slug, fname = target_filename(t.url, old, "." + opts.social_format)
                try:
                    rec = _capture_one(page, ctx, t, fname, slug, shots_dir, html_dir, settings, opts, logged_in)
                except Exception as ex:  # never kill the whole run
                    rec = {"url": t.url, "slug": slug, "screenshot_path": None, "last_post_title": None,
                           "last_post_author": None, "last_post_date": None, "status": "error",
                           "hidden_links": [], "notes": f"playwright; exception={type(ex).__name__}: {str(ex)[:160]}",
                           "captured_at": extract.now_iso(), "source": "playwright"}
                    try:
                        page.close()
                    except Exception:
                        pass
                    page = ctx.new_page()
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                records.append(rec)
                log(f"  [{i}/{len(items)}] {rec['status']:<13} {fname}  {(rec.get('last_post_date') or '')}"
                    f" {(rec.get('last_post_title') or '')[:60]}")
                if i < len(items):
                    between_pages_delay(settings)
            if use_state and opts.refresh_sessions and logged_in and sp:
                from .cookies import write_private_json
                write_private_json(sp, ctx.storage_state())
            ctx.close()

    summary = None
    if opts.write_report:
        if not settings.report_root:
            log("!! --write-report: ACR_REPORT_ROOT not set / report root not found — skipped")
        else:
            summary = write_into_report(settings.report_root, records, shots_dir, keep_good=opts.keep_good)
    c = Counter(r["status"] for r in records)
    (run_dir / "summary.json").write_text(json.dumps(
        {"finished_at": extract.now_iso(), "statuses": dict(c), "count": len(records), "report_merge": summary},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return records, run_dir, summary
