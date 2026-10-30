#!/usr/bin/env python3
"""Backup social-network screenshots before overwrite.

Copies existing screenshots whose filenames match social hosts
(facebook, instagram, x.com/twitter, youtube, linkedin) into
screenshots/archive/YYYY-MM-DD/<filename>.

Does not delete or modify originals. Safe to run repeatedly (skips
if archive copy already exists unless --force).
"""
from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "screenshots"
ARCHIVE = SHOTS / "archive"

SOCIAL_NAME_RE = re.compile(
    r"(?i)(facebook|instagram|linkedin|youtube|youtu|"
    r"(?:^|[-_])x(?:com|[-_])|"
    r"(?:^|[-_])x[-_]|"
    r"twitter)"
)

# Broader: also match by capture slug patterns used in this repo
SOCIAL_PREFIXES = (
    "facebook-",
    "instagram-",
    "linkedin-",
    "youtube-",
    "x-com-",
    "x-",
    "twitter-",
)


def is_social_shot(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return False
    if name.startswith(SOCIAL_PREFIXES):
        return True
    return bool(SOCIAL_NAME_RE.search(name))


def backup_social(day: str | None = None, force: bool = False) -> dict:
    day = day or datetime.now().strftime("%Y-%m-%d")
    dest_dir = ARCHIVE / day
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = skipped = missing = 0
    files = []
    for p in sorted(SHOTS.iterdir()):
        if not p.is_file():
            continue
        if not is_social_shot(p):
            continue
        dest = dest_dir / p.name
        if dest.exists() and not force:
            skipped += 1
            files.append({"src": str(p), "dest": str(dest), "action": "skip_exists"})
            continue
        shutil.copy2(p, dest)
        copied += 1
        files.append({"src": str(p), "dest": str(dest), "action": "copied"})
    return {
        "day": day,
        "archive_dir": str(dest_dir),
        "copied": copied,
        "skipped": skipped,
        "files": files,
    }


def backup_paths(paths: list[Path], day: str | None = None, force: bool = False) -> dict:
    """Backup specific screenshot paths (before overwrite)."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    dest_dir = ARCHIVE / day
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    files = []
    for p in paths:
        p = Path(p)
        if not p.is_file():
            files.append({"src": str(p), "action": "missing"})
            continue
        dest = dest_dir / p.name
        if dest.exists() and not force:
            skipped += 1
            files.append({"src": str(p), "dest": str(dest), "action": "skip_exists"})
            continue
        shutil.copy2(p, dest)
        copied += 1
        files.append({"src": str(p), "dest": str(dest), "action": "copied"})
    return {
        "day": day,
        "archive_dir": str(dest_dir),
        "copied": copied,
        "skipped": skipped,
        "files": files,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", default=None, help="Archive day YYYY-MM-DD (default today)")
    ap.add_argument("--force", action="store_true", help="Overwrite archive copies")
    ap.add_argument("paths", nargs="*", help="Optional specific files; default all social")
    args = ap.parse_args()
    if args.paths:
        stats = backup_paths([Path(p) for p in args.paths], day=args.day, force=args.force)
    else:
        stats = backup_social(day=args.day, force=args.force)
    print(
        f"archive={stats['archive_dir']} copied={stats['copied']} "
        f"skipped={stats['skipped']}"
    )
    return stats


if __name__ == "__main__":
    main()
