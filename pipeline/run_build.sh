#!/usr/bin/env bash
# Kompletní build mapy nad SQLite:
#   1) archive_updates.py  – zápis nových příspěvků do data/updates_history.jsonl (žurnál)
#   2) db/import_data.py   – ingest všech JSON/JSONL do db/mapa.sqlite (idempotentní)
#   3) build_site_v2.py    – web čte VŠECHNA data z SQLite, publikuje mapa.sqlite + sql.html
#   4) build_archive_is_page.py – stránka archiv-is.html (také z SQLite)
set -euo pipefail
cd "$(dirname "$0")"
export ACR_ROOT="$PWD"
python3 - <<'PY'
import importlib.util
s = importlib.util.spec_from_file_location('archive_updates', 'scripts/archive_updates.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
st = m.run(); print(f"archive: total={st.get('total')} added={st.get('added')}")
PY
python3 ../db/import_data.py --root "$PWD" ${ACR_SHOTS:+--shots "$ACR_SHOTS"}
python3 build_site_v2.py
python3 scripts/build_archive_is_page.py
