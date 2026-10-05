"""Browser launch + "behave like a real desktop user" helpers."""
from __future__ import annotations

import os
import random
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Settings
from .services import (
    Service, consent_button_texts, consent_selectors, is_forbidden_click,
)

# Mask the most obvious automation fingerprint. This is about rendering pages the
# way a normal visitor sees them (some sites serve stripped/blocked pages to
# bots); it is not meant to defeat access controls or CAPTCHAs.
INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
if (!window.chrome) { window.chrome = { runtime: {} }; }
"""

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-default-browser-check",
    "--lang=cs-CZ",
]


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover
        sys.exit("Playwright není nainstalovaný / not installed:\n"
                 "  pip install -r requirements.txt && python -m playwright install chromium")
    return sync_playwright


def has_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def launch(pw, settings: Settings, headless: bool | None = None):
    headless = settings.headless if headless is None else headless
    kwargs: dict = {"headless": headless, "args": list(LAUNCH_ARGS)}
    if settings.executable_path:
        kwargs["executable_path"] = settings.executable_path
    elif settings.channel:
        kwargs["channel"] = settings.channel
    if settings.proxy:
        kwargs["proxy"] = {"server": settings.proxy}
    if sys.platform.startswith("linux") and os.geteuid() == 0:
        kwargs["args"].append("--no-sandbox")
    try:
        return pw.chromium.launch(**kwargs)
    except Exception as ex:
        if kwargs.get("channel") == "chromium" and "Executable doesn't exist" in str(ex):
            # only the headless shell is installed (`playwright install --only-shell`)
            kwargs.pop("channel")
            return pw.chromium.launch(**kwargs)
        raise


def realistic_user_agent(browser) -> str:
    """UA of a normal desktop Chrome with the same major version (no 'HeadlessChrome')."""
    major = (browser.version or "130").split(".")[0]
    if sys.platform == "darwin":
        plat = "Macintosh; Intel Mac OS X 10_15_7"
    elif sys.platform.startswith("win"):
        plat = "Windows NT 10.0; Win64; x64"
    else:
        plat = "X11; Linux x86_64"
    return f"Mozilla/5.0 ({plat}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


def new_context(browser, settings: Settings, storage_state: Path | None = None):
    w, h = settings.viewport
    ctx = browser.new_context(
        viewport={"width": w, "height": h},
        screen={"width": w, "height": h + 120},
        device_scale_factor=settings.device_scale_factor,
        user_agent=settings.user_agent or realistic_user_agent(browser),
        locale=settings.locale,
        timezone_id=settings.timezone_id,
        color_scheme="light",
        java_script_enabled=True,
        extra_http_headers={"Accept-Language": f"{settings.locale},cs;q=0.9,en-US;q=0.7,en;q=0.6"},
        storage_state=str(storage_state) if storage_state and storage_state.is_file() else None,
    )
    ctx.add_init_script(INIT_SCRIPT)
    ctx.set_default_navigation_timeout(settings.nav_timeout_ms)
    ctx.set_default_timeout(15_000)
    return ctx


@contextmanager
def browser_session(settings: Settings, headless: bool | None = None) -> Iterator[tuple[object, object]]:
    sync_playwright = require_playwright()
    with sync_playwright() as pw:
        browser = launch(pw, settings, headless=headless)
        try:
            yield pw, browser
        finally:
            browser.close()


# ---------------------------------------------------------------------------
# human-ish behaviour
# ---------------------------------------------------------------------------
def human_pause(a: float = 0.4, b: float = 1.2) -> None:
    time.sleep(random.uniform(a, b))


def between_pages_delay(settings: Settings) -> None:
    time.sleep(random.uniform(settings.delay_min_s, max(settings.delay_min_s, settings.delay_max_s)))


def wait_settled(page, settings: Settings, ready_selector: str | None = None) -> list[str]:
    notes = []
    try:
        page.wait_for_load_state("load", timeout=settings.nav_timeout_ms)
    except Exception:
        notes.append("load_timeout")
    try:
        page.wait_for_load_state("networkidle", timeout=settings.idle_timeout_ms)
    except Exception:
        notes.append("networkidle_timeout")  # normal for FB/X (long-polling)
    if ready_selector:
        try:
            page.wait_for_selector(ready_selector, timeout=8_000, state="attached")
        except Exception:
            notes.append("ready_selector_missing")
    return notes


def human_mouse_and_scroll(page, settings: Settings, depth_px: int = 1600) -> None:
    """Move the mouse and scroll down/up gently so lazy content loads."""
    w, h = settings.viewport
    try:
        page.mouse.move(random.randint(int(w * .3), int(w * .7)), random.randint(int(h * .3), int(h * .6)), steps=12)
        done = 0
        while done < depth_px:
            step = random.randint(250, 450)
            page.mouse.wheel(0, step)
            done += step
            time.sleep(random.uniform(0.25, 0.6))
        time.sleep(random.uniform(0.6, 1.2))
        page.mouse.wheel(0, -done - 200)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(random.uniform(0.6, 1.0))
    except Exception:
        pass


def safe_click(locator) -> bool:
    try:
        txt = (locator.inner_text(timeout=1500) or "") + " " + (locator.get_attribute("aria-label") or "")
    except Exception:
        txt = ""
    if is_forbidden_click(txt) and not re.search(r"cookie|souhlas|consent", txt, re.I):
        return False
    try:
        locator.click(timeout=3000)
        return True
    except Exception:
        return False


def dismiss_consent(page, settings: Settings) -> str | None:
    """Click a cookie-consent button if one is visible. Returns label clicked."""
    if settings.consent_mode == "skip":
        return None
    frames = [page.main_frame] + [f for f in page.frames if f is not page.main_frame]
    for frame in frames:
        for sel in consent_selectors():
            try:
                loc = frame.locator(sel).first
                if loc.count() and loc.is_visible(timeout=500):
                    if settings.consent_mode == "accept" and "reject" in sel.lower():
                        continue
                    if safe_click(loc):
                        human_pause()
                        return sel
            except Exception:
                continue
        for text in consent_button_texts(settings.consent_mode):
            exact = len(text) <= 8
            for role in ("button", "link"):
                try:
                    loc = frame.get_by_role(role, name=text, exact=exact).first
                    if loc.count() and loc.is_visible(timeout=300):
                        if safe_click(loc):
                            human_pause()
                            return text
                except Exception:
                    continue
    return None


def close_login_overlays(page, svc: Service | None) -> bool:
    """Anonymous FB/IG show a modal login prompt; close it (never log in implicitly)."""
    if not svc or svc.key not in ("facebook", "instagram", "x"):
        return False
    for name in ("Close", "Zavřít", "Not now", "Teď ne", "Nyní ne"):
        try:
            loc = page.locator(f'div[role="dialog"] [aria-label="{name}"]').first
            if loc.count() and loc.is_visible(timeout=400):
                loc.click(timeout=2000)
                human_pause(0.3, 0.8)
                return True
            btn = page.get_by_role("button", name=name, exact=True).first
            if btn.count() and btn.is_visible(timeout=300):
                btn.click(timeout=2000)
                human_pause(0.3, 0.8)
                return True
        except Exception:
            continue
    return False


def hide_viewer_identity(page, svc: Service | None) -> int:
    """CSS-blur the logged-in viewer's own avatar/name before screenshotting."""
    if not svc or not svc.viewer_identity_selectors:
        return 0
    css = ",".join(svc.viewer_identity_selectors) + "{filter: blur(9px) !important;}"
    try:
        page.add_style_tag(content=css)
        return int(page.evaluate(
            "(sels) => sels.reduce((n, s) => { try { return n + document.querySelectorAll(s).length } catch(e) { return n } }, 0)",
            list(svc.viewer_identity_selectors),
        ))
    except Exception:
        return 0


def context_has_cookies(ctx, svc: Service) -> bool:
    try:
        names = {c["name"] for c in ctx.cookies([svc.home_url])}
    except Exception:
        return False
    return bool(svc.session_cookies) and all(n in names for n in svc.session_cookies)
