"""Pixel-blur the logged-in viewer's identity (avatar / name chip) on screenshots.

Preferred: reuse ``<report_root>/scripts/blur_viewer_identity.py`` (the script the
daily pipeline already uses), so regions stay in one place. If it is not
available, an identical bundled fallback is used.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_cached = None


def _load_report_module(report_root: Path | None):
    if not report_root:
        return None
    script = report_root / "scripts" / "blur_viewer_identity.py"
    if not script.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("blur_viewer_identity", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        if hasattr(mod, "process"):
            return mod
    except Exception:
        return None
    return None


# ---- bundled fallback (mirrors report/scripts/blur_viewer_identity.py) ----------
def _regions_for(url: str, w: int, h: int):
    u = (url or "").lower()
    regs = [(int(w * .78), 0, w, int(h * .095)), (int(w * .82), int(h * .02), w - 8, int(h * .12))]
    if "facebook.com" in u:
        regs += [(int(w * .72), 0, w, int(h * .08)), (0, int(h * .08), int(w * .06), int(h * .14))]
    if "instagram.com" in u:
        regs.append((int(w * .85), 0, w, int(h * .09)))
    if "x.com" in u or "twitter.com" in u:
        regs += [(0, int(h * .78), int(w * .22), h), (int(w * .88), 0, w, int(h * .08))]
    if "linkedin.com" in u:
        regs.append((int(w * .80), 0, w, int(h * .09)))
    if "youtube.com" in u:
        regs.append((int(w * .90), 0, w, int(h * .08)))
    return regs


def _fallback_process(path: Path, url: str = "", out: Path | None = None, radius: int = 22) -> Path:
    from PIL import Image, ImageFilter
    im = Image.open(path).convert("RGB")
    for x0, y0, x1, y1 in _regions_for(url, im.width, im.height):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)
        if x1 > x0 and y1 > y0:
            im.paste(im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(radius=radius)), (x0, y0))
    dest = out or path
    if dest.suffix.lower() in (".jpg", ".jpeg"):
        im.save(dest, "JPEG", quality=88)
    else:
        im.save(dest)
    return dest


def blur_viewer_identity(path: Path, url: str, report_root: Path | None = None, radius: int = 22) -> str:
    """Blur in place. Returns which implementation was used."""
    global _cached
    if _cached is None:
        _cached = _load_report_module(report_root) or False
    if _cached:
        _cached.process(path, url=url, out=path, radius=radius)
        return "report/scripts/blur_viewer_identity.py"
    _fallback_process(path, url=url, out=path, radius=radius)
    return "bundled"
