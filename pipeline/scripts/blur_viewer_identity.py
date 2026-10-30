#!/usr/bin/env python3
"""Blur the logged-in viewer's identity (and friends'/contacts' identity) from social screenshots.

Covers (2026-10-16 rewrite):
  * all: top-right profile chip / avatar (incl. screenshots with browser chrome)
  * X:  bottom-left account switcher (viewer name + @handle + avatar),
        "Followed by … others you follow" / "Sledují …" line incl. avatar stack
  * FB: "friends who follow" avatar row under the page header
  * IG: floating "Messages"/"Zprávy" bubble with contacts' avatars
Detection: OCR via `tesseract` CLI (tsv) when available, with fixed relative-region
fallback per platform. Platform from --url / --url-map, else from the filename.
Does NOT blur the page subject.
"""
from __future__ import annotations
import argparse, re, shutil, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageFilter

VIEWER_RE = re.compile(r"(bernhard|michalb|@michal|^michal$)", re.I)
FOLLOWED_RE = re.compile(r"^(followed|sleduj[ií]|sledujú|others)$", re.I)

def blur_rect(im, box, radius=22):
    x0,y0,x1,y1 = [int(v) for v in box]
    x0=max(0,x0); y0=max(0,y0); x1=min(im.width,x1); y1=min(im.height,y1)
    if x1<=x0 or y1<=y0: return
    crop = im.crop((x0,y0,x1,y1))
    for _ in range(2):
        crop = crop.filter(ImageFilter.GaussianBlur(radius=radius))
    im.paste(crop, (x0,y0))

def platform_of(url: str, name: str) -> str:
    s = (url or "").lower() + " " + name.lower()
    if "x.com" in s or "twitter" in s or re.match(r"^x-", name.lower()): return "x"
    if "facebook" in s or name.lower().startswith(("facebook-","fb-")): return "fb"
    if "instagram" in s or name.lower().startswith(("instagram-","ig-")): return "ig"
    if "linkedin" in s: return "li"
    if "youtube" in s: return "yt"
    return ""

def ocr_words(im):
    """Return list of dicts {text,x0,y0,x1,y1,key} in image coords, or None if no OCR."""
    if not shutil.which("tesseract"): return None
    sc = 3 if im.width < 1600 else 2
    big = im.convert("L").resize((im.width*sc, im.height*sc), Image.LANCZOS)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)/"i.png"; big.save(p)
        try:
            r = subprocess.run(["tesseract", str(p), "stdout", "-l", _langs(), "--psm", "11", "tsv"],
                               capture_output=True, text=True, timeout=180)
        except Exception:
            return None
    out = []
    for ln in r.stdout.splitlines()[1:]:
        f = ln.split("\t")
        if len(f) < 12 or not f[11].strip(): continue
        try: conf = float(f[10])
        except ValueError: conf = -1
        x,y,w,h = (int(v)/sc for v in f[6:10])
        out.append(dict(text=f[11].strip(), conf=conf, x0=x, y0=y, x1=x+w, y1=y+h,
                        key=(f[2], f[3], f[4])))
    return out

_LANGS = None
def _langs():
    global _LANGS
    if _LANGS is None:
        try:
            have = subprocess.run(["tesseract","--list-langs"],capture_output=True,text=True).stdout.split()
        except Exception: have = []
        _LANGS = "+".join(l for l in ("eng","ces") if l in have) or "eng"
    return _LANGS

def line_box(words, w0):
    """Bounding box of the visual text line containing w0: words on the same baseline,
    chained left/right while the horizontal gap is small (so nav columns are not merged)."""
    hh = max(4, w0["y1"]-w0["y0"])
    cy = (w0["y0"]+w0["y1"])/2
    row = sorted([w for w in words if abs((w["y0"]+w["y1"])/2 - cy) < hh*0.7], key=lambda w: w["x0"])
    x0, x1 = w0["x0"], w0["x1"]; ws = [w0]
    changed = True
    while changed:
        changed = False
        for w in row:
            if w in ws: continue
            if (w["x1"] >= x0 - hh*2.5 and w["x0"] <= x1 + hh*2.5):
                ws.append(w); x0 = min(x0, w["x0"]); x1 = max(x1, w["x1"]); changed = True
    return (x0, min(w["y0"] for w in ws), x1, max(w["y1"] for w in ws))

def chrome_offset(h):
    return 0

def regions_for(url, name, im, words):
    w, h = im.size
    plat = platform_of(url, name)
    regs = []
    # Universal top-right profile chip; 0.13h also covers screenshots incl. browser chrome
    if plat not in ("x", "ig"):   # X/IG have no viewer chip top-right
        regs.append((int(w*0.80), 0, w, int(h*0.13)))
    found = set()
    if words:
        for wd in words:
            t = wd["text"]
            if VIEWER_RE.search(t) and (plat != "ig" or "bernhard" in t.lower()):
                x0,y0,x1,y1 = line_box(words, wd); lh = y1-y0
                # name + handle (2 lines) + avatar on the left
                regs.append((x0-lh*4.5, y0-lh*1.6, x1+lh*2, y1+lh*2.6)); found.add("viewer")
            if plat in ("x","") and FOLLOWED_RE.search(t):
                x0,y0,x1,y1 = line_box(words, wd); lh = max(8, y1-y0)
                # possible wrap to 2nd line; avatar stack left of text
                regs.append((x0-lh*5, y0-lh*0.35, wd["x0"]+w*0.25, y1+lh*2.3)); found.add("followed")
            if plat in ("ig","") and re.fullmatch(r"(messages|zpr[aá]vy)", t, re.I) and wd["y0"] > h*0.75:
                lh = wd["y1"]-wd["y0"]
                regs.append((wd["x0"]-lh*3.5, wd["y0"]-lh*2.2, min(w, wd["x1"]+w*0.13), wd["y1"]+lh*2.2)); found.add("messages")
        if plat == "fb":
            reg = fb_friends_row(words, w, h)
            if reg: regs.append(reg); found.add("friends")
    # Fixed fallbacks
    if plat == "x":
        # bottom-left account switcher, always (pure chrome)
        regs.append((0, int(h*0.88), int(w*0.34), h))
        # always: band right under the "N Following  N Followers" stats row
        # (that is where "Followed by … others you follow" lives; small grey text OCRs poorly)
        fb = x_followers_fallback(words, w, h)
        if fb and (words is not None or "followed" not in found): regs.append(fb)
    if plat == "fb" and "friends" not in found and not words:
        # no OCR at all: typical unscrolled layout (header block right of the page avatar)
        regs.append((int(w*0.28), int(h*0.23), int(w*0.43), int(h*0.29)))
    if plat == "ig" and "messages" not in found:
        regs.append((int(w*0.83), int(h*0.89), w, h))
    if plat in ("li",):
        regs.append((int(w*0.80), 0, w, int(h*0.13)))
    return regs, found

def x_followers_fallback(words, w, h):
    """Without OCR hit: blur the line just under the 'Following / Followers' stats."""
    if words:
        def has_num_left(wd):
            lh = max(4, wd["y1"]-wd["y0"]); cy = (wd["y0"]+wd["y1"])/2
            return any(re.fullmatch(r"[\d.,]+[KkMm]?", o["text"]) and abs((o["y0"]+o["y1"])/2-cy) < lh
                       and 0 <= wd["x0"]-o["x1"] < lh*3 for o in words)
        stats = [wd for wd in words if re.match(r"(follow|sleduj)", wd["text"], re.I)
                 and wd["y0"] > h*0.15 and has_num_left(wd)]
        if stats:
            # stats row = the Following/Followers pair that sits most to the left (the button is right)
            wd = min(stats, key=lambda x: x["x0"])
            x0,y0,x1,y1 = line_box(words, wd); lh = max(8, wd["y1"]-wd["y0"])
            # the number left of "Following" may be missed by OCR -> reach back ~2 stat widths
            lx = min(x0, wd["x0"] - w*0.07)
            return (lx-lh*1.5, wd["y1"]+lh*0.5, lx+w*0.30, wd["y1"]+lh*4.0)
        return None
    return (int(w*0.31), int(h*0.445), int(w*0.63), int(h*0.50))

def fb_friends_row(words, w, h):
    """Avatar row of friends who follow the page: between last header text line and the tab row."""
    fol = [x for x in words if re.search(r"followers|sledující|fanoušk", x["text"], re.I) and x["y0"] < h*0.8]
    tabs = [x for x in words if re.fullmatch(r"(About|Reels|Informace|Mentions|Zmínky)", x["text"])
            and x["y0"] < h*0.9]
    if not fol or not tabs: return None
    f = min(fol, key=lambda x: x["y0"]); ty = min(t["y0"] for t in tabs if t["y0"] > f["y1"]) if any(t["y0"] > f["y1"] for t in tabs) else None
    if ty is None: return None
    # left edge of header text column = leftmost word on the followers line
    lx = line_box(words, f)[0]
    above = [x for x in words if f["y0"] <= x["y0"] and x["y1"] < ty - 4 and x["x0"] >= lx-10
             and x["x0"] < lx + w*0.35 and x["conf"] > 60 and len(x["text"]) > 1
             and (x["y1"]-x["y0"]) < 1.7*(f["y1"]-f["y0"])]   # avatar rows OCR as tall junk
    last = max(x["y1"] for x in above) if above else f["y1"]
    lh = f["y1"]-f["y0"]
    y0 = last + 2
    y1 = ty - lh*0.6
    if y1 - y0 < lh: y0 = y1 - lh*3   # degenerate: blur fixed height above tabs
    return (lx-8, y0, lx + w*0.20, y1)

def process(path: Path, url: str = "", out: Path | None = None, radius: int = 22, ocr=True):
    im = Image.open(path).convert("RGB")
    words = ocr_words(im) if ocr else None
    regs, found = regions_for(url, path.name, im, words)
    for box in regs:
        blur_rect(im, box, radius=radius)
    dest = out or path
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)
    return dest, found, words is not None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--url", default="")
    ap.add_argument("--url-map", type=Path, help="jsonl captures to resolve url by filename stem")
    ap.add_argument("--inplace", "--in-place", action="store_true")
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--platform", choices=["x","fb","ig"], help="override platform detection")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--radius", type=int, default=22)
    args = ap.parse_args()
    url_by_stem = {}
    if args.url_map and args.url_map.exists():
        import json
        for line in args.url_map.read_text().splitlines():
            if not line.strip(): continue
            r = json.loads(line)
            sp = r.get("screenshot_path") or ""
            if sp: url_by_stem[Path(sp).stem] = r.get("url") or ""
            if r.get("slug"): url_by_stem[r["slug"]] = r.get("url") or ""
    pmap = {"x":"https://x.com/","fb":"https://facebook.com/","ig":"https://instagram.com/"}
    for p in args.paths:
        url = pmap[args.platform] if args.platform else (args.url or url_by_stem.get(p.stem, ""))
        if args.out_dir: out = args.out_dir / p.name
        elif args.inplace: out = p
        else: out = p.with_name(p.stem + "-blur" + p.suffix)
        dest, found, ocr = process(p, url=url, out=out, radius=args.radius, ocr=not args.no_ocr)
        print(f"blurred {p} -> {dest} platform={platform_of(url, p.name) or '?'} ocr={ocr} detected={sorted(found)}")

if __name__ == "__main__":
    main()
