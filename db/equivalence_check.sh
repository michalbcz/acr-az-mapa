#!/bin/bash
# Ekvivalence: JSON build (spec/kompletni-specifikace) vs SQLite build (feature/sqlite-data) nad stejnými daty
set -e
W=/workspace/acr-map/work-branches/acr-az-mapa; T=/tmp/eq; SH=/workspace/acr-map/report/screenshots
rm -rf $T && mkdir -p $T/A $T/B && cd $T
git -C $W archive spec/kompletni-specifikace pipeline | tar -x -C A
git -C $W archive feature/sqlite-data pipeline db | tar -x -C B
rm -rf A/pipeline/data && cp -r B/pipeline/data A/pipeline/data
for d in A B; do ln -s $SH $d/pipeline/screenshots; done
(cd A/pipeline && python3 -c "
import importlib.util;s=importlib.util.spec_from_file_location('a','scripts/archive_updates.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print('archive',m.run()['added'])")
rm -rf B/pipeline/data && cp -r A/pipeline/data B/pipeline/data
(cd A/pipeline && ACR_ROOT=$PWD python3 build_site_v2.py > $T/A.log 2>&1 && python3 scripts/build_archive_is_page.py >> $T/A.log)
cp A/pipeline/data/updates_history.jsonl B/pipeline/data/   # side-effect archive_updates v buildu A
(cd B/pipeline && export ACR_ROOT=$PWD && python3 ../db/import_data.py --root $PWD --shots $SH > $T/imp.log && python3 ../db/roundtrip_test.py $PWD ../db/mapa.sqlite > $T/rt.log && mv data ../data_hidden && python3 build_site_v2.py > $T/B.log 2>&1 && python3 scripts/build_archive_is_page.py >> $T/B.log; mv ../data_hidden data)
diff -r -x mapa.sqlite -x sql.html A/pipeline/site_v2 B/pipeline/site_v2 && echo "IDENTICAL $(find A/pipeline/site_v2 -type f | wc -l) files"
ls -la B/pipeline/site_v2/mapa.sqlite B/pipeline/site_v2/sql.html; cat $T/rt.log | tr '\n' ' '
