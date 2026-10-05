"""Session storage: Playwright ``storage_state`` files + browser cookie import.

Supported import formats (auto-detected):

1. Playwright storage_state JSON  ``{"cookies": [...], "origins": [...]}``
2. JSON array exported by browser extensions (Cookie-Editor, EditThisCookie, …)
3. Netscape ``cookies.txt`` (e.g. "Get cookies.txt LOCALLY" extension, yt-dlp)
4. Raw ``Cookie:`` header string ``name=value; name2=value2`` (needs ``--domain``)

Cookie values are never printed; only names, domains and expiry.
"""
from __future__ import annotations

import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from .services import SERVICES, Service

_SAMESITE_MAP = {
    "no_restriction": "None", "none": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "Lax", "": "Lax",
}


def state_path(secrets_dir: Path, service_key: str) -> Path:
    override = os.environ.get(f"ACR_{service_key.upper()}_STORAGE_STATE")
    if override:
        return Path(override).expanduser()
    return secrets_dir / f"{service_key}.storage_state.json"


def ensure_secrets_dir(secrets_dir: Path) -> None:
    secrets_dir.mkdir(parents=True, exist_ok=True)
    try:
        secrets_dir.chmod(0o700)
    except OSError:
        pass


def write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    tmp.replace(path)


def load_state(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict) and "cookies" in data:
        data.setdefault("origins", [])
        return data
    return None


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------
def _norm_cookie(c: dict) -> dict | None:
    name = c.get("name")
    value = c.get("value")
    domain = c.get("domain") or c.get("host")
    if not name or value is None or not domain:
        return None
    expires = c.get("expires", c.get("expirationDate", c.get("expiry")))
    if c.get("session") is True or expires in (None, "", 0):
        expires = -1
    try:
        expires = float(expires)
    except (TypeError, ValueError):
        expires = -1
    if expires > 1e11:  # milliseconds → seconds
        expires = expires / 1000.0
    host_only = c.get("hostOnly")
    if host_only is False and not str(domain).startswith("."):
        domain = "." + str(domain)
    same_site = c.get("sameSite")
    same_site = _SAMESITE_MAP.get(str(same_site).lower(), "Lax") if same_site is not None else "Lax"
    secure = bool(c.get("secure", False))
    if same_site == "None" and not secure:
        secure = True  # browsers reject SameSite=None without Secure
    return {
        "name": str(name),
        "value": str(value),
        "domain": str(domain),
        "path": c.get("path") or "/",
        "expires": expires,
        "httpOnly": bool(c.get("httpOnly", False)),
        "secure": secure,
        "sameSite": same_site,
    }


def parse_netscape(text: str) -> list[dict]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only = True
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, include_sub, path, secure, expiry, name, value = parts[:7]
        c = _norm_cookie({
            "domain": domain,
            "hostOnly": include_sub.upper() != "TRUE",
            "path": path,
            "secure": secure.upper() == "TRUE",
            "expires": int(expiry) if expiry.isdigit() and int(expiry) > 0 else -1,
            "name": name,
            "value": value,
            "httpOnly": http_only,
        })
        if c:
            out.append(c)
    return out


def parse_cookie_header(text: str, domain: str) -> list[dict]:
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:]
    dom = domain if domain.startswith(".") else "." + domain
    out = []
    for part in text.split(";"):
        if "=" not in part:
            continue
        name, _, value = part.strip().partition("=")
        c = _norm_cookie({"name": name.strip(), "value": value.strip(), "domain": dom,
                          "secure": True, "sameSite": "no_restriction",
                          "expires": time.time() + 30 * 86400})
        if c:
            out.append(c)
    return out


def parse_cookie_file(path: Path, domain: str | None = None) -> tuple[list[dict], list[dict], str]:
    """Return (cookies, origins, detected_format)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        data = json.loads(text)
        if isinstance(data, dict) and "cookies" in data:
            cookies = [c for c in (_norm_cookie(x) for x in data["cookies"]) if c]
            return cookies, list(data.get("origins") or []), "playwright_storage_state"
        if isinstance(data, dict) and "name" in data:  # single cookie object
            data = [data]
        if isinstance(data, list):
            return [c for c in (_norm_cookie(x) for x in data) if c], [], "json_cookie_array"
        raise ValueError("Unknown JSON cookie format")
    if "\t" in text and ("# Netscape" in text or "TRUE\t" in text or "FALSE\t" in text):
        return parse_netscape(text), [], "netscape_cookies_txt"
    if "=" in text:
        if not domain:
            raise ValueError("Raw Cookie header detected — pass --domain (e.g. .facebook.com)")
        return parse_cookie_header(text, domain), [], "cookie_header"
    raise ValueError("Could not detect cookie file format")


def filter_for_service(cookies: list[dict], svc: Service) -> list[dict]:
    keep = []
    for c in cookies:
        d = c["domain"].lstrip(".").lower()
        if any(d == cd or d.endswith("." + cd) for cd in svc.cookie_domains):
            keep.append(c)
    return keep


def merge_cookies(existing: list[dict], new: list[dict]) -> list[dict]:
    key = lambda c: (c["name"], c["domain"].lstrip("."), c.get("path", "/"))  # noqa: E731
    merged = {key(c): c for c in existing}
    for c in new:
        merged[key(c)] = c
    return list(merged.values())


def import_cookies(src: Path, service_key: str, secrets_dir: Path, domain: str | None = None,
                   filter_domains: bool = True, replace: bool = False) -> tuple[Path, dict]:
    svc = SERVICES[service_key]
    cookies, origins, fmt = parse_cookie_file(src, domain=domain)
    total = len(cookies)
    if filter_domains:
        cookies = filter_for_service(cookies, svc)
    ensure_secrets_dir(secrets_dir)
    dest = state_path(secrets_dir, service_key)
    prev = None if replace else load_state(dest)
    state = {
        "cookies": merge_cookies(prev["cookies"] if prev else [], cookies),
        "origins": origins or (prev["origins"] if prev else []),
    }
    write_private_json(dest, state)
    info = {
        "format": fmt, "read": total, "kept": len(cookies),
        "session_ok": has_session_cookies(state["cookies"], svc),
        "missing": missing_session_cookies(state["cookies"], svc),
    }
    return dest, info


def missing_session_cookies(cookies: list[dict], svc: Service) -> list[str]:
    now = time.time()
    names = {c["name"] for c in cookies
             if (c.get("expires", -1) in (-1, None) or c.get("expires", -1) > now)
             and any(c["domain"].lstrip(".").endswith(d) for d in svc.cookie_domains)}
    return [n for n in svc.session_cookies if n not in names]


def has_session_cookies(cookies: list[dict], svc: Service) -> bool:
    return bool(svc.session_cookies) and not missing_session_cookies(cookies, svc)


def describe_state(path: Path, svc: Service) -> dict:
    """Summary without secret values."""
    st = load_state(path)
    if not st:
        return {"service": svc.key, "path": str(path), "exists": path.exists(), "valid": False}
    soonest = None
    for c in st["cookies"]:
        if c["name"] in svc.session_cookies and c.get("expires", -1) not in (-1, None):
            soonest = c["expires"] if soonest is None else min(soonest, c["expires"])
    mode = oct(path.stat().st_mode & 0o777)
    return {
        "service": svc.key,
        "path": str(path),
        "exists": True,
        "valid": True,
        "cookies": len(st["cookies"]),
        "session_cookies_ok": has_session_cookies(st["cookies"], svc),
        "missing": missing_session_cookies(st["cookies"], svc),
        "session_expires": (datetime.fromtimestamp(soonest, tz=timezone.utc).astimezone().isoformat(timespec="minutes")
                            if soonest else None),
        "file_mode": mode,
        "modified": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="minutes"),
    }
