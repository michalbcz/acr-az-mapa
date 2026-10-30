import json
from pathlib import Path
rows=[json.loads(l) for l in Path(__file__).resolve().parents[1] / "data" / "captures.jsonl".read_text().splitlines() if l.strip()]
print("count", len(rows))
for r in rows:
    hl=",".join(h["url"].split("/")[-1][:40] for h in r["hidden_links"][:3])
    print(f"{r['status']:10} | {r['slug'][:55]:55} | post={r['last_post_title']!r} | {r['last_post_date']} | fb={len(r['hidden_links'])} {hl}")
    if r.get("notes"):
        print(f"             notes: {r['notes'][:180]}")
    sp = r.get("screenshot_path")
    if sp:
        p = Path(__file__).resolve().parents[1]/sp
        print(f"             shot: {sp} exists={p.exists()} size={p.stat().st_size if p.exists() else 0}")
