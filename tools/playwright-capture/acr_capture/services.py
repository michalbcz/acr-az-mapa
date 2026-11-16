"""Per-service knowledge: hosts, login URLs, session cookies, consent buttons,
"viewer identity" UI selectors and safety guards."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Service:
    key: str
    label: str
    hosts: tuple[str, ...]
    home_url: str
    login_url: str
    # cookies whose presence means "logged in" (all must be present)
    session_cookies: tuple[str, ...] = ()
    # cookie domains to keep when importing a browser cookie export
    cookie_domains: tuple[str, ...] = ()
    # CSS selectors of the logged-in viewer's own avatar / name / account switcher.
    # They get CSS-blurred before the screenshot (pixel blur is applied afterwards too).
    viewer_identity_selectors: tuple[str, ...] = ()
    # selector that indicates the main content rendered (best-effort wait)
    ready_selector: str | None = None
    login_supported: bool = True
    notes: str = ""


SERVICES: dict[str, Service] = {
    "facebook": Service(
        key="facebook",
        label="Facebook",
        hosts=("facebook.com", "fb.com", "fb.me", "fb.watch"),
        home_url="https://www.facebook.com/",
        login_url="https://www.facebook.com/login/",
        session_cookies=("c_user", "xs"),
        cookie_domains=("facebook.com",),
        viewer_identity_selectors=(
            '[aria-label="Your profile"]', '[aria-label="Váš profil"]',
            '[aria-label="Account controls and settings"]', '[aria-label="Ovládací prvky a nastavení účtu"]',
            'div[role="banner"] svg[aria-label][role="img"]',
            'div[role="navigation"] a[href*="/me/"]',
        ),
        ready_selector='div[role="main"], div[role="feed"], div[role="article"]',
    ),
    "instagram": Service(
        key="instagram",
        label="Instagram",
        hosts=("instagram.com",),
        home_url="https://www.instagram.com/",
        login_url="https://www.instagram.com/accounts/login/",
        session_cookies=("sessionid",),
        cookie_domains=("instagram.com",),
        viewer_identity_selectors=(
            'nav a[href^="/"][role="link"] img[alt*="profil"]',
            'nav a[href^="/"][role="link"] img[alt*="profile"]',
            'a[href^="/"] img[alt$="profile picture"]',
            'a[href^="/"] img[alt^="Profilová fotka"]',
        ),
        ready_selector='main, header',
    ),
    "x": Service(
        key="x",
        label="X (Twitter)",
        hosts=("x.com", "twitter.com"),
        home_url="https://x.com/home",
        login_url="https://x.com/i/flow/login",
        session_cookies=("auth_token",),
        cookie_domains=("x.com", "twitter.com"),
        viewer_identity_selectors=(
            '[data-testid="SideNav_AccountSwitcher_Button"]',
            '[data-testid="AppTabBar_Profile_Link"]',
            '[data-testid="DashButton_ProfileIcon_Link"]',
        ),
        ready_selector='article[data-testid="tweet"], [data-testid="primaryColumn"]',
    ),
    "linkedin": Service(
        key="linkedin",
        label="LinkedIn",
        hosts=("linkedin.com",),
        home_url="https://www.linkedin.com/feed/",
        login_url="https://www.linkedin.com/login",
        session_cookies=("li_at",),
        cookie_domains=("linkedin.com",),
        viewer_identity_selectors=(
            ".global-nav__me", ".global-nav__me-photo", "img.global-nav__me-photo",
            ".feed-identity-module",
        ),
        ready_selector="main",
    ),
    "youtube": Service(
        key="youtube",
        label="YouTube",
        hosts=("youtube.com", "youtu.be"),
        home_url="https://www.youtube.com/",
        login_url="https://accounts.google.com/ServiceLogin?service=youtube",
        session_cookies=("SAPISID",),
        cookie_domains=("youtube.com", "google.com"),
        viewer_identity_selectors=("#avatar-btn", "ytd-topbar-menu-button-renderer #button"),
        ready_selector="ytd-app, #contents",
        login_supported=True,
        notes="Public channels do not need login; Google often blocks automated logins, prefer cookie import.",
    ),
}

LOGIN_SERVICES = tuple(k for k, s in SERVICES.items() if s.login_supported)


def host_of(url: str) -> str:
    h = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    return h.split("@")[-1].split(":")[0]


def service_for_url(url: str) -> Service | None:
    h = host_of(url)
    for svc in SERVICES.values():
        for d in svc.hosts:
            if h == d or h.endswith("." + d):
                return svc
    return None


def service_key_for_url(url: str) -> str:
    s = service_for_url(url)
    return s.key if s else "web"


def is_social(url: str) -> bool:
    return service_for_url(url) is not None


def is_facebook_group(url: str) -> bool:
    u = url.lower()
    return "facebook.com/groups/" in u or "facebook.com/share/g/" in u


# --------------------------------------------------------------------------
# Consent (cookie banner) buttons. Order matters: first match is clicked.
# --------------------------------------------------------------------------
_REJECT_TEXTS = [
    "Odmítnout volitelné soubory cookie", "Decline optional cookies",
    "Refuse non-essential cookies", "Odmítnout nepovinné soubory cookie",
    "Odmítnout vše", "Reject all", "Odmítnout", "Pouze nezbytné", "Only necessary",
    "Only allow essential cookies", "Povolit jen nezbytné soubory cookie",
]
_ACCEPT_TEXTS = [
    "Povolit všechny soubory cookie", "Allow all cookies", "Accept all cookies",
    "Přijmout vše", "Accept all", "Souhlasím", "Rozumím", "Povolit vše", "Přijmout",
    "I agree", "Agree", "OK",
]
_CMP_SELECTORS = [
    "#onetrust-reject-all-handler", "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyButtonDecline", "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "button.cc-nb-reject", "button.cc-nb-okagree", ".cc-btn.cc-dismiss",
    "button[data-cookiefirst-action='reject']", "button[data-cookiefirst-action='accept']",
]


def consent_button_texts(mode: str) -> list[str]:
    if mode == "skip":
        return []
    if mode == "accept":
        return _ACCEPT_TEXTS + _REJECT_TEXTS
    return _REJECT_TEXTS + _ACCEPT_TEXTS


def consent_selectors() -> list[str]:
    return list(_CMP_SELECTORS)


# --------------------------------------------------------------------------
# Safety: never click anything that changes the account's social graph.
# --------------------------------------------------------------------------
FORBIDDEN_CLICK_RE = re.compile(
    r"\b(join|přidat se|připojit se|požádat o členství|request to join|follow|sledovat|"
    r"like|to se mi líbí|subscribe|odebírat|connect|spojit se|message|zpráva)\b",
    re.I,
)


def is_forbidden_click(text: str) -> bool:
    return bool(FORBIDDEN_CLICK_RE.search(text or ""))
