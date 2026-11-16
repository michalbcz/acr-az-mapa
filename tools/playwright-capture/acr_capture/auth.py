"""Login flows that end with a Playwright ``storage_state`` file in ``secrets/``.

Three ways to get a session (pick one per service):

1. ``login <service>`` — opens a *headed* Chromium, you log in by hand
   (2FA / checkpoints included), the session is saved automatically.
2. ``login <service> --use-env-credentials`` — same, but the form is pre-filled
   from ``ACR_<SVC>_EMAIL`` / ``ACR_<SVC>_PASSWORD`` (still headed by default so
   you can solve 2FA). ``--headless`` is possible but platforms often block it.
3. ``import-cookies <service> file`` — no credentials at all: export cookies
   from your everyday browser and convert them (see cookies.py / README).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .browser import browser_session, context_has_cookies, dismiss_consent, has_display, human_pause, new_context
from .config import Settings, credentials_for
from .cookies import ensure_secrets_dir, state_path, write_private_json
from .services import SERVICES, Service


def _type_like_human(locator, text: str) -> None:
    locator.click()
    locator.fill("")
    locator.type(text, delay=60)


def _autofill(page, svc: Service, login: str, password: str) -> None:
    if svc.key == "facebook":
        _type_like_human(page.locator('input[name="email"]').first, login)
        human_pause()
        _type_like_human(page.locator('input[name="pass"]').first, password)
        human_pause()
        page.keyboard.press("Enter")
    elif svc.key == "instagram":
        page.wait_for_selector('input[name="username"]', timeout=20_000)
        _type_like_human(page.locator('input[name="username"]').first, login)
        human_pause()
        _type_like_human(page.locator('input[name="password"]').first, password)
        human_pause()
        page.keyboard.press("Enter")
    elif svc.key == "x":
        page.wait_for_selector('input[autocomplete="username"]', timeout=25_000)
        _type_like_human(page.locator('input[autocomplete="username"]').first, login)
        human_pause()
        page.keyboard.press("Enter")
        page.wait_for_selector('input[name="password"]', timeout=25_000)  # may ask for handle/phone first → manual
        _type_like_human(page.locator('input[name="password"]').first, password)
        human_pause()
        page.keyboard.press("Enter")
    elif svc.key == "linkedin":
        _type_like_human(page.locator("#username").first, login)
        human_pause()
        _type_like_human(page.locator("#password").first, password)
        human_pause()
        page.keyboard.press("Enter")
    else:
        raise RuntimeError(f"Autofill not implemented for {svc.key}; log in manually.")


def interactive_login(settings: Settings, service_key: str, use_env_credentials: bool = False,
                      headless: bool = False, timeout_s: int = 300) -> Path:
    svc = SERVICES[service_key]
    if not headless and not has_display():
        sys.exit(
            "Není dostupný displej (DISPLAY). Možnosti / No display available. Options:\n"
            "  • spusť to na svém počítači / run on your desktop and copy secrets/<svc>.storage_state.json here\n"
            "  • xvfb-run -a python -m acr_capture login ...  (a VNC/remote desktop to see the window)\n"
            "  • python -m acr_capture import-cookies <svc> cookies.json  (no login needed)"
        )
    ensure_secrets_dir(settings.secrets_dir)
    dest = state_path(settings.secrets_dir, service_key)
    login = password = None
    if use_env_credentials:
        login, password = credentials_for(service_key)
        if not (login and password):
            sys.exit(f"Chybí přihlašovací údaje v env / missing env credentials for {service_key} (see .env.example)")

    with browser_session(settings, headless=headless) as (_pw, browser):
        ctx = new_context(browser, settings, storage_state=dest if dest.is_file() else None)
        page = ctx.new_page()
        page.goto(svc.login_url, wait_until="domcontentloaded")
        human_pause(1.0, 2.0)
        dismiss_consent(page, settings)
        if context_has_cookies(ctx, svc):
            print(f"[{svc.label}] Už přihlášeno / already logged in — refreshing saved session.")
        elif login and password:
            print(f"[{svc.label}] Vyplňuji formulář z env / filling login form from env …")
            try:
                _autofill(page, svc, login, password)
            except Exception as ex:  # fall back to manual
                print(f"  autofill failed ({type(ex).__name__}) — dokonči přihlášení ručně / finish manually.")
        if not context_has_cookies(ctx, svc):
            print(f"[{svc.label}] Přihlas se v otevřeném okně (vč. 2FA). Čekám max {timeout_s}s …\n"
                  f"  Log in in the browser window (incl. 2FA). Waiting up to {timeout_s}s …")
        deadline = time.time() + timeout_s
        while time.time() < deadline and not context_has_cookies(ctx, svc):
            time.sleep(2)
        if not context_has_cookies(ctx, svc):
            ctx.close()
            sys.exit(f"[{svc.label}] Přihlášení nedokončeno / login not completed (no {svc.session_cookies} cookie).")
        # let the platform finish setting secondary cookies, then visit home once
        time.sleep(3)
        try:
            page.goto(svc.home_url, wait_until="domcontentloaded")
            human_pause(2.0, 3.5)
        except Exception:
            pass
        write_private_json(dest, ctx.storage_state())
        ctx.close()
    print(f"[{svc.label}] Session uložena / saved → {dest}  (gitignored, chmod 600)")
    return dest


def check_session(settings: Settings, service_key: str) -> dict:
    """Open the service home headless with the saved state and report login status."""
    svc = SERVICES[service_key]
    path = state_path(settings.secrets_dir, service_key)
    if not path.is_file():
        return {"service": service_key, "state_file": str(path), "exists": False, "logged_in": False}
    with browser_session(settings, headless=True) as (_pw, browser):
        ctx = new_context(browser, settings, storage_state=path)
        page = ctx.new_page()
        try:
            page.goto(svc.home_url, wait_until="domcontentloaded")
            human_pause(2.0, 3.0)
        except Exception as ex:
            return {"service": service_key, "exists": True, "logged_in": False, "error": str(ex)[:200]}
        url_after = page.url
        redirected_to_login = any(x in url_after for x in ("/login", "accounts/login", "i/flow/login", "checkpoint"))
        ok = context_has_cookies(ctx, svc) and not redirected_to_login
        if ok:
            write_private_json(path, ctx.storage_state())  # roll refreshed cookies forward
        ctx.close()
    return {"service": service_key, "state_file": str(path), "exists": True, "logged_in": ok,
            "landed_on": url_after.split("?")[0]}
