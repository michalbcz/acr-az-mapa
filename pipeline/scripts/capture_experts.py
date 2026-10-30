#!/usr/bin/env python3
"""Capture expert/defence-media URLs: screenshot + captures.jsonl + urls.json."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "screenshots"
DATA = ROOT / "data" / "captures.jsonl"
URLS_JSON = ROOT / "urls.json"
PROFILE = Path("/tmp/chrome-experts-profile")
SHOTS.mkdir(parents=True, exist_ok=True)
PROFILE.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT / "scripts"))
from backup_screenshots import backup_paths, is_social_shot  # noqa: E402

EXPERT_URLS = [
    # webs
    "https://www.czdefence.cz/",
    "https://www.armadninoviny.cz/",
    "https://www.natoaktual.cz/",
    "https://www.securityoutlines.cz/",
    "https://www.valka.cz/",
    "https://jirivojacek.cz/",
    "http://lvisingr.czweb.org/",
    "https://www.iir.cz/",
    "https://www.aobp.cz/",
    "https://magnetpress.online/atm/",
    "https://doarmady.mo.gov.cz/o-armade/poznejte-armadu/utvary-a-posadky/91-skupina-informacniho-boje",
    # social
    "https://x.com/IvoZelinka",
    "https://www.instagram.com/ivo.zelinka/",
    "https://www.facebook.com/91skupina",
    "https://www.instagram.com/91skupina/",
    "https://www.facebook.com/czdefence",
    "https://www.instagram.com/czdefence/",
    "https://x.com/CzDefence",
    "https://www.facebook.com/ArmadniNoviny",
    "https://www.facebook.com/natoaktual",
    "https://x.com/natoaktual",
    "https://www.facebook.com/securityoutlines",
    "https://www.instagram.com/security_outlines/",
    "https://x.com/carramba66",
    "https://www.facebook.com/CasopisATM",
    "https://www.linkedin.com/in/ivo-zelinka-5240611a",
    "https://x.com/Danny_elMayor",
]

SOCIAL_HOSTS = ("facebook.com", "instagram.com", "x.com", "twitter.com", "linkedin.com", "youtube.com")


def slug_for(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.lower().replace("www.", "").replace(".", "-")
    path = p.path.strip("/").replace("/", "-")
    if "facebook.com" in p.netloc.lower():
        if "profile.php" in path:
            q = p.query
            mid = re.search(r"id=(\d+)", q)
            return f"facebook-profile-{mid.group(1)}" if mid else "facebook-profile"
        slug = path.split("-")[0] if path else "facebook"
        return f"facebook-com-{slug.lower()}"[:90]
    if "instagram.com" in p.netloc.lower():
        handle = path.split("-")[0] if path else "ig"
        return f"instagram-com-{handle.lower()}"[:90]
    if p.netloc.lower().replace("www.", "") in ("x.com", "twitter.com"):
        handle = path.split("-")[0] if path else "x"
        return f"x-com-{handle.lower()}"[:90]
    if "linkedin.com" in p.netloc.lower():
        segs = [s for s in p.path.split("/") if s]
        return ("linkedin-" + "-".join(segs[-2:])).lower()[:90]
    slug = host if not path else f"{host}-{path}"
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-").lower()
    return slug[:90]


def is_social(url: str) -> bool:
    host = urlparse(url).netloc.lower().replace("www.", "")
    return any(h in host for h in SOCIAL_HOSTS)


def chrome_screenshot(url: str, shot_path: Path) -> tuple[bool, str]:
    cmd = [
        "google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu",
        "--disable-dev-shm-usage", f"--user-data-dir={PROFILE}",
        "--window-size=1400,1800",
        "--ignore-certificate-errors",
        f"--screenshot={shot_path}",
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        ok = shot_path.exists() and shot_path.stat().st_size > 1000
        return ok, (r.stderr or "")[-400:]
    except Exception as ex:
        return False, str(ex)


def detect_login_wall(shot_path: Path, url: str) -> bool:
    """Heuristic: very small shot or known social + tiny file → likely wall; refine via curl body."""
    if not shot_path.exists():
        return False
    host = urlparse(url).netloc.lower()
    # quick HTML check for login cues
    try:
        r = subprocess.run(
            ["curl", "-fsSL", "-A", "Mozilla/5.0 (compatible; ACR-map/1.0)",
             "--max-time", "25", "-L", url],
            capture_output=True, text=True, timeout=35,
        )
        body = (r.stdout or "").lower()
        cues = (
            "log in", "login", "sign in", "přihlásit", "must log in",
            "create an account", "join linkedin", "authwall",
        )
        if any(c in body for c in cues) and is_social(url):
            # LinkedIn 999 / auth walls
            if "linkedin" in host and ("authwall" in body or "join linkedin" in body or r.returncode != 0):
                return True
            if "instagram" in host and ("login" in body or "log in" in body):
                return True
            if "facebook" in host and ("login" in body or "log into facebook" in body):
                # FB often shows public page + login overlay; treat as login_wall only if title is login
                if "log into facebook" in body or 'content="facebook"' in body[:2000] and "login" in body[:3000]:
                    return True
    except Exception:
        pass
    if "linkedin" in host and shot_path.stat().st_size < 50000:
        return True
    return False


def load_captures() -> dict[str, dict]:
    by = {}
    if DATA.exists():
        for line in DATA.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("url"):
                by[o["url"]] = o
    return by


def write_captures(by: dict[str, dict]) -> None:
    # preserve insertion order: existing order then new
    lines = []
    seen = set()
    if DATA.exists():
        for line in DATA.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                lines.append(line)
                continue
            u = o.get("url")
            if u in by:
                lines.append(json.dumps(by[u], ensure_ascii=False))
                seen.add(u)
            else:
                lines.append(line)
                if u:
                    seen.add(u)
    for u, o in by.items():
        if u not in seen:
            lines.append(json.dumps(o, ensure_ascii=False))
    DATA.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_urls_json(urls: list[str]) -> None:
    data = json.loads(URLS_JSON.read_text(encoding="utf-8"))
    existing = set()
    for k, v in data.items():
        if isinstance(v, list):
            existing.update(v)
    experts = list(data.get("experts") or [])
    for u in urls:
        if u not in experts:
            experts.append(u)
    data["experts"] = experts
    URLS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process(url: str) -> dict:
    slug = slug_for(url)
    shot = SHOTS / f"{slug}.png"
    print(f"=== {slug} === {url}", flush=True)

    if shot.exists() and is_social(url):
        stats = backup_paths([shot])
        print(f"  backup: copied={stats['copied']} skipped={stats['skipped']}", flush=True)

    notes = []
    ok, err = chrome_screenshot(url, shot)
    if not ok:
        notes.append("screenshot_failed")
        if err:
            notes.append(f"chrome_err={err[:180]}")

    status = "ok" if ok else "error"
    if ok and detect_login_wall(shot, url):
        status = "login_wall"
        notes.append("login_wall_or_auth_prompt")

    # czdefence SSL flake from some clients — chrome ignore-cert may still render
    if "czdefence.cz" in url and ok:
        notes.append("ssl_flaky_from_box_chrome_ok")

    # labels for notes
    if "Danny_elMayor" in url:
        notes.append("verified_ACR_defence_analyst_bio")
    if "carramba66" in url:
        notes.append("Jiří_Vojáček")
    if "ivo-zelinka" in url or "IvoZelinka" in url or "ivo.zelinka" in url:
        notes.append("Ivo_Zelinka_person_neoficialni")
    if "91-skupina" in url:
        notes.append("91_skupina_MO_oficialni")

    title = None
    # light title from curl og:title for webs
    if not is_social(url):
        try:
            r = subprocess.run(
                ["curl", "-fsSL", "-A", "Mozilla/5.0 ACR-map/1.0", "--max-time", "25",
                 "-k" if "czdefence" in url else "-L", url],
                capture_output=True, text=True, timeout=35,
            )
            body = r.stdout or ""
            m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', body, re.I)
            if not m:
                m = re.search(r"<title>([^<]{3,160})</title>", body, re.I)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()[:160]
        except Exception as ex:
            notes.append(f"title_fetch_err={ex}")

    rec = {
        "url": url,
        "slug": slug,
        "screenshot_path": f"screenshots/{slug}.png" if shot.exists() and shot.stat().st_size > 500 else None,
        "last_post_title": title,
        "last_post_author": None,
        "last_post_date": None,
        "status": status,
        "hidden_links": [],
        "notes": "; ".join(notes) if notes else f"experts_capture_{datetime.now().strftime('%Y-%m-%d')}",
    }
    print(json.dumps({"slug": slug, "status": status, "shot": rec["screenshot_path"], "title": title}, ensure_ascii=False), flush=True)
    return rec


def main():
    urls = EXPERT_URLS
    by = load_captures()
    added = []
    for url in urls:
        rec = process(url)
        by[url] = rec
        added.append(url)
    write_captures(by)
    update_urls_json(added)
    print(f"DONE captures={len(by)} experts_processed={len(added)}")


if __name__ == "__main__":
    main()
