"""Runtime settings, read from environment variables (and an optional ``.env``).

Nothing secret is ever stored in this module. Credentials and cookies live in
environment variables or in ``secrets/`` which is git-ignored.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_ROOT = Path("/workspace/acr-map/report")
BUNDLED_TARGETS = PROJECT_ROOT / "targets" / "urls.json"


def load_dotenv(path: Path | None = None) -> Path | None:
    """Minimal ``.env`` loader (no extra dependency). Existing env vars win."""
    candidates = [path] if path else [Path.cwd() / ".env", PROJECT_ROOT / ".env"]
    for p in candidates:
        if p and p.is_file():
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                os.environ.setdefault(key, val)
            return p
    return None


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "ano")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _viewport(raw: str) -> tuple[int, int]:
    try:
        w, h = raw.lower().split("x", 1)
        return int(w), int(h)
    except Exception:
        return 1440, 900


@dataclass
class Settings:
    report_root: Path | None
    secrets_dir: Path
    out_dir: Path
    headless: bool = True
    channel: str | None = "chromium"  # "chromium" (new headless), "chrome" (installed Google Chrome) or "" (headless shell)
    executable_path: str | None = None
    user_agent: str | None = None
    viewport: tuple[int, int] = (1440, 900)
    device_scale_factor: float = 1.0
    locale: str = "cs-CZ"
    timezone_id: str = "Europe/Prague"
    proxy: str | None = None
    nav_timeout_ms: int = 45_000
    idle_timeout_ms: int = 12_000
    delay_min_s: float = 2.0
    delay_max_s: float = 6.0
    consent_mode: str = "reject"  # reject | accept | skip
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        rr = os.environ.get("ACR_REPORT_ROOT")
        report_root: Path | None
        if rr:
            report_root = Path(rr).expanduser()
        elif DEFAULT_REPORT_ROOT.is_dir():
            report_root = DEFAULT_REPORT_ROOT
        else:
            report_root = None
        channel = os.environ.get("ACR_BROWSER_CHANNEL", "chromium").strip()
        return cls(
            report_root=report_root,
            secrets_dir=Path(os.environ.get("ACR_SECRETS_DIR", PROJECT_ROOT / "secrets")).expanduser(),
            out_dir=Path(os.environ.get("ACR_OUT_DIR", PROJECT_ROOT / "out")).expanduser(),
            headless=_env_bool("ACR_HEADLESS", True),
            channel=channel or None,
            executable_path=os.environ.get("ACR_BROWSER_EXECUTABLE") or None,
            user_agent=os.environ.get("ACR_USER_AGENT") or None,
            viewport=_viewport(os.environ.get("ACR_VIEWPORT", "1440x900")),
            device_scale_factor=_env_float("ACR_DEVICE_SCALE_FACTOR", 1.0),
            locale=os.environ.get("ACR_LOCALE", "cs-CZ"),
            timezone_id=os.environ.get("ACR_TIMEZONE", "Europe/Prague"),
            proxy=os.environ.get("ACR_PROXY") or None,
            nav_timeout_ms=_env_int("ACR_NAV_TIMEOUT_MS", 45_000),
            idle_timeout_ms=_env_int("ACR_IDLE_TIMEOUT_MS", 12_000),
            delay_min_s=_env_float("ACR_DELAY_MIN", 2.0),
            delay_max_s=_env_float("ACR_DELAY_MAX", 6.0),
            consent_mode=os.environ.get("ACR_CONSENT", "reject").strip().lower(),
        )

    # ---- convenience paths -------------------------------------------------
    @property
    def captures_jsonl(self) -> Path | None:
        return self.report_root / "data" / "captures.jsonl" if self.report_root else None

    @property
    def report_screenshots(self) -> Path | None:
        return self.report_root / "screenshots" if self.report_root else None

    def default_targets_file(self) -> Path:
        if self.report_root and (self.report_root / "urls.json").is_file():
            return self.report_root / "urls.json"
        return BUNDLED_TARGETS


def credentials_for(service_key: str) -> tuple[str | None, str | None]:
    """Return (login, password) from env, e.g. ACR_FB_EMAIL / ACR_FB_PASSWORD."""
    prefix = {
        "facebook": "ACR_FB",
        "instagram": "ACR_IG",
        "x": "ACR_X",
        "linkedin": "ACR_LINKEDIN",
        "youtube": "ACR_GOOGLE",
    }.get(service_key)
    if not prefix:
        return None, None
    login = (
        os.environ.get(f"{prefix}_EMAIL")
        or os.environ.get(f"{prefix}_USERNAME")
        or os.environ.get(f"{prefix}_LOGIN")
    )
    password = os.environ.get(f"{prefix}_PASSWORD")
    return (login or None), (password or None)
