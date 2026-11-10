"""Target URL loading / filtering."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .records import norm_url
from .services import service_key_for_url


@dataclass
class Target:
    url: str
    category: str
    service: str  # facebook | instagram | x | linkedin | youtube | web


SOCIAL_KEYS = {"facebook", "instagram", "x", "linkedin", "youtube"}


def _from_obj(obj, category: str = "") -> list[tuple[str, str]]:
    out = []
    if isinstance(obj, str):
        if obj.startswith("http"):
            out.append((obj, category))
    elif isinstance(obj, list):
        for x in obj:
            out += _from_obj(x, category)
    elif isinstance(obj, dict):
        if "url" in obj and isinstance(obj["url"], str):
            out.append((obj["url"], obj.get("category") or category))
        else:
            for k, v in obj.items():
                out += _from_obj(v, k if not category else category)
    return out


def load_file(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    name = path.name.lower()
    if name.endswith(".jsonl"):
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("url"):
                    out.append((r["url"], r.get("category") or "captures"))
        return out
    if name.endswith(".json"):
        return _from_obj(json.loads(text))
    return [(ln.strip(), "list") for ln in text.splitlines() if ln.strip().startswith("http")]


def collect(files: list[Path], urls: list[str], categories: list[str] | None = None,
            only: list[str] | None = None, match: str | None = None, limit: int | None = None,
            skip_groups: bool = False) -> list[Target]:
    pairs: list[tuple[str, str]] = []
    for f in files:
        pairs += load_file(f)
    pairs += [(u, "cli") for u in urls]
    seen = set()
    out: list[Target] = []
    cats = {c.lower() for c in categories or []}
    only_set = {o.lower() for o in only or []}
    rx = re.compile(match, re.I) if match else None
    for url, cat in pairs:
        k = norm_url(url)
        if k in seen:
            continue
        seen.add(k)
        svc = service_key_for_url(url)
        if cats and cat.lower() not in cats and cat != "cli":
            continue
        if only_set:
            ok = (svc in only_set) or ("social" in only_set and svc in SOCIAL_KEYS) or ("web" in only_set and svc == "web")
            if not ok:
                continue
        if rx and not rx.search(url):
            continue
        if skip_groups and ("facebook.com/groups/" in url.lower() or "facebook.com/share/g/" in url.lower()):
            continue
        out.append(Target(url=url, category=cat, service=svc))
    # social first (priority), then webs — stable within groups
    order = {"facebook": 0, "instagram": 1, "x": 2, "youtube": 3, "linkedin": 4, "web": 5}
    out.sort(key=lambda t: order.get(t.service, 9))
    return out[:limit] if limit else out
