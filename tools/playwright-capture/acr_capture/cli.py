"""Command line interface:  python -m acr_capture <command> [options]

Commands
  doctor          check environment (Playwright, browser, report root, secrets)
  targets         list targets that would be captured (no browser)
  capture         open targets in headless Chromium, screenshot + extract
  merge           merge a finished run into report/data/captures.jsonl + screenshots
  login           headed login → secrets/<service>.storage_state.json
  import-cookies  convert a browser cookie export into a storage_state
  sessions        show saved sessions (no secret values)
  check-session   verify a saved session is still logged in (headless)
  export-state    copy a saved storage_state elsewhere (e.g. to another machine)
  blur            blur viewer identity on existing screenshots
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import Settings
from .services import LOGIN_SERVICES, SERVICES


def _add_target_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("targets / cíle")
    g.add_argument("--urls-file", action="append", type=Path, default=[],
                   help="JSON (urls.json-style dict / list), .jsonl (captures) or .txt; repeatable. "
                        "Default: <report>/urls.json or bundled targets/urls.json")
    g.add_argument("--from-captures", action="store_true",
                   help="use every URL from <report>/data/captures.jsonl (full refresh of the map)")
    g.add_argument("--url", action="append", default=[], help="single URL; repeatable")
    g.add_argument("--category", action="append", help="urls.json category (social, kvv, unit_webs, …)")
    g.add_argument("--only", action="append",
                   help="social | web | facebook | instagram | x | youtube | linkedin; repeatable")
    g.add_argument("--match", help="regex filter on URL")
    g.add_argument("--limit", type=int, help="max number of URLs")
    g.add_argument("--skip-groups", action="store_true", help="skip facebook.com/groups/* URLs")


def _collect(settings: Settings, args):
    from .targets import collect
    files = list(args.urls_file)
    if args.from_captures:
        if not settings.captures_jsonl or not settings.captures_jsonl.is_file():
            sys.exit("--from-captures: captures.jsonl nenalezen / not found (set ACR_REPORT_ROOT)")
        files.append(settings.captures_jsonl)
    if not files and not args.url:
        files.append(settings.default_targets_file())
    return collect(files, args.url, args.category, args.only, args.match, args.limit, args.skip_groups)


def cmd_doctor(settings: Settings, args) -> int:
    from .cookies import describe_state, state_path
    ok = True
    print(f"acr_capture {__version__}")
    try:
        import importlib.metadata as md
        print(f"playwright     : {md.version('playwright')}")
    except Exception:
        print("playwright     : NOT INSTALLED → pip install -r requirements.txt")
        ok = False
    try:
        import PIL
        print(f"pillow         : {PIL.__version__}")
    except Exception:
        print("pillow         : NOT INSTALLED (needed for blur)")
        ok = False
    print(f"report root    : {settings.report_root or '-'}"
          f"{'' if not settings.report_root or settings.report_root.is_dir() else '  (MISSING)'}")
    print(f"captures.jsonl : {settings.captures_jsonl if settings.captures_jsonl and settings.captures_jsonl.is_file() else '-'}")
    print(f"targets default: {settings.default_targets_file()}")
    print(f"secrets dir    : {settings.secrets_dir}")
    print(f"out dir        : {settings.out_dir}")
    print(f"browser        : channel={settings.channel or 'headless-shell'} headless={settings.headless} "
          f"viewport={settings.viewport[0]}x{settings.viewport[1]} locale={settings.locale} tz={settings.timezone_id}")
    for k in LOGIN_SERVICES:
        d = describe_state(state_path(settings.secrets_dir, k), SERVICES[k])
        print(f"session {k:<9}: {'OK' if d.get('session_cookies_ok') else ('present, missing ' + ','.join(d.get('missing', [])) if d.get('valid') else 'none')}")
    if args.launch and ok:
        from .browser import browser_session, new_context
        try:
            with browser_session(settings, headless=True) as (_pw, browser):
                ctx = new_context(browser, settings)
                page = ctx.new_page()
                page.set_content("<title>ok</title><h1>acr_capture</h1>")
                ua = page.evaluate("navigator.userAgent")
                wd = page.evaluate("navigator.webdriver")
                print(f"browser launch : OK  version={browser.version}  UA={ua}  webdriver={wd}")
                ctx.close()
        except Exception as ex:
            print(f"browser launch : FAILED {type(ex).__name__}: {str(ex)[:300]}\n"
                  "  → python -m playwright install chromium   (+ `install-deps` on fresh Linux)\n"
                  "  → or ACR_BROWSER_CHANNEL=chrome to use an installed Google Chrome")
            ok = False
    return 0 if ok else 1


def cmd_targets(settings: Settings, args) -> int:
    from .capture import CaptureOptions, plan
    targets = _collect(settings, args)
    rows = plan(settings, targets, CaptureOptions(social_format=args.format))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['service']:<9} {r['session']:<3} {('known' if r['known_in_captures'] else 'NEW'):<5} "
                  f"{(r['previous_status'] or '-'):<12} {r['file']:<60} {r['url']}")
        print(f"-- {len(rows)} target(s)")
    return 0


def cmd_capture(settings: Settings, args) -> int:
    from .capture import CaptureOptions, plan, run
    if args.headed:
        settings.headless = False
    targets = _collect(settings, args)
    opts = CaptureOptions(
        full_page=args.full_page, blur=args.blur, social_format=args.format,
        use_sessions=not args.anonymous, refresh_sessions=not args.no_refresh_sessions,
        write_report=args.write_report, keep_good=not args.allow_downgrade, save_html=not args.no_html,
        headless=None if not args.headed else False, run_dir=args.run_dir,
    )
    if args.dry_run:
        for r in plan(settings, targets, opts):
            print(f"DRY {r['service']:<9} session={r['session']:<3} -> {r['file']:<55} {r['url']}")
        print(f"-- dry-run: {len(targets)} target(s); write_report={opts.write_report}; nothing opened")
        return 0
    if not targets:
        print("Žádné cíle / no targets.")
        return 1
    records, run_dir, summary = run(settings, targets, opts)
    print(f"\nRun dir : {run_dir}\n  captures.jsonl, screenshots/, html/, summary.json")
    if summary:
        print(f"Report  : {json.dumps(summary, ensure_ascii=False)}")
        print("Next    : python3 build_site_v2.py   (in the report root, unchanged pipeline)")
    else:
        print("Report  : not modified (add --write-report, or later: python -m acr_capture merge <run_dir>)")
    return 0


def cmd_merge(settings: Settings, args) -> int:
    from .records import write_into_report
    if not settings.report_root:
        sys.exit("ACR_REPORT_ROOT not set / report root not found")
    run_dir: Path = args.run_dir
    recs = [json.loads(ln) for ln in (run_dir / "captures.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    if args.only_ok:
        recs = [r for r in recs if r.get("status") == "ok"]
    s = write_into_report(settings.report_root, recs, run_dir / "screenshots",
                          keep_good=not args.allow_downgrade, dry_run=args.dry_run)
    print(json.dumps({"dry_run": args.dry_run, **s}, ensure_ascii=False, indent=2))
    return 0


def cmd_login(settings: Settings, args) -> int:
    from .auth import interactive_login
    interactive_login(settings, args.service, use_env_credentials=args.use_env_credentials,
                      headless=args.headless, timeout_s=args.timeout)
    return 0


def cmd_import_cookies(settings: Settings, args) -> int:
    from .cookies import import_cookies
    dest, info = import_cookies(args.file, args.service, settings.secrets_dir, domain=args.domain,
                                filter_domains=not args.no_filter, replace=args.replace)
    print(f"Imported ({info['format']}): {info['kept']}/{info['read']} cookies → {dest}")
    if info["session_ok"]:
        print("Session cookies OK. Ověř / verify: python -m acr_capture check-session " + args.service)
    else:
        print(f"!! Chybí / missing session cookie(s): {', '.join(info['missing'])} — export while logged in, "
              "incl. HttpOnly cookies (DevTools / Cookie-Editor export all).")
    if args.delete_source:
        args.file.unlink()
        print(f"Source file deleted: {args.file}")
    else:
        print(f"Tip: smaž zdrojový export / delete the raw export when done: {args.file}")
    return 0


def cmd_sessions(settings: Settings, args) -> int:
    from .cookies import describe_state, state_path
    rows = [describe_state(state_path(settings.secrets_dir, k), SERVICES[k]) for k in LOGIN_SERVICES]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_check_session(settings: Settings, args) -> int:
    from .auth import check_session
    services = LOGIN_SERVICES if args.service == "all" else [args.service]
    res = [check_session(settings, s) for s in services]
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if all(r.get("logged_in") for r in res if r.get("exists")) else 2


def cmd_export_state(settings: Settings, args) -> int:
    from .cookies import state_path
    src = state_path(settings.secrets_dir, args.service)
    if not src.is_file():
        sys.exit(f"No saved session for {args.service}: {src}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, args.out)
    args.out.chmod(0o600)
    print(f"Copied {src} → {args.out} (contains live session cookies — treat like a password)")
    return 0


def cmd_blur(settings: Settings, args) -> int:
    from .blur import blur_viewer_identity
    for p in args.paths:
        impl = blur_viewer_identity(p, args.url, settings.report_root, radius=args.radius)
        print(f"blurred {p} ({impl})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m acr_capture",
        description="Playwright capture tooling for the AČR/AZ map (headless Chromium, real-user emulation).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="check environment")
    p.add_argument("--launch", action="store_true", help="also launch headless Chromium once")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("targets", help="list targets (no browser)")
    _add_target_args(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--format", choices=("png", "jpg"), default="png")
    p.set_defaults(func=cmd_targets)

    p = sub.add_parser("capture", help="capture screenshots + last posts")
    _add_target_args(p)
    o = p.add_argument_group("capture")
    o.add_argument("--dry-run", action="store_true", help="print plan, open nothing")
    o.add_argument("--headed", action="store_true", help="visible browser window (debug)")
    o.add_argument("--full-page", action="store_true", help="full-page screenshots (default: viewport)")
    o.add_argument("--format", choices=("png", "jpg"), default="png", help="file type for NEW urls")
    o.add_argument("--blur", choices=("auto", "always", "never"), default="auto",
                   help="viewer-identity blur; auto = only when logged in")
    o.add_argument("--anonymous", action="store_true", help="ignore saved sessions")
    o.add_argument("--no-refresh-sessions", action="store_true", help="don't write rolled cookies back")
    o.add_argument("--no-html", action="store_true", help="don't keep page HTML in the run dir")
    o.add_argument("--run-dir", type=Path, help="output dir (default out/run-YYYYmmdd-HHMMSS)")
    o.add_argument("--write-report", action="store_true",
                   help="merge into <report>/data/captures.jsonl + screenshots (with backups)")
    o.add_argument("--allow-downgrade", action="store_true",
                   help="let a failed refresh (login_wall/blocked) overwrite a previously OK capture")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("merge", help="merge a run dir into the report")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only-ok", action="store_true", help="merge only status=ok records")
    p.add_argument("--allow-downgrade", action="store_true")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("login", help="headed login, saves storage_state")
    p.add_argument("service", choices=LOGIN_SERVICES)
    p.add_argument("--use-env-credentials", action="store_true", help="prefill from ACR_<SVC>_EMAIL/PASSWORD")
    p.add_argument("--headless", action="store_true", help="no window (only with env credentials, no 2FA)")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for login")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("import-cookies", help="browser cookie export → storage_state")
    p.add_argument("service", choices=LOGIN_SERVICES)
    p.add_argument("file", type=Path, help="Cookie-Editor JSON / cookies.txt / storage_state / raw Cookie header")
    p.add_argument("--domain", help="needed for raw 'Cookie:' header input, e.g. .facebook.com")
    p.add_argument("--no-filter", action="store_true", help="keep cookies of all domains")
    p.add_argument("--replace", action="store_true", help="replace instead of merging with existing state")
    p.add_argument("--delete-source", action="store_true", help="delete the raw export after import")
    p.set_defaults(func=cmd_import_cookies)

    p = sub.add_parser("sessions", help="list saved sessions (no secret values)")
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("check-session", help="verify saved session is logged in")
    p.add_argument("service", choices=list(LOGIN_SERVICES) + ["all"])
    p.set_defaults(func=cmd_check_session)

    p = sub.add_parser("export-state", help="copy storage_state to a path")
    p.add_argument("service", choices=LOGIN_SERVICES)
    p.add_argument("out", type=Path)
    p.set_defaults(func=cmd_export_state)

    p = sub.add_parser("blur", help="blur viewer identity on screenshots (in place)")
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--url", default="", help="page URL (selects platform-specific regions)")
    p.add_argument("--radius", type=int, default=22)
    p.set_defaults(func=cmd_blur)
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    return int(args.func(settings, args) or 0)
