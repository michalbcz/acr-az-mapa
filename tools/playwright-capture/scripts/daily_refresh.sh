#!/usr/bin/env bash
# Example daily refresh: social first (with saved sessions), then webs, merge into
# the existing report and rebuild the site with the unchanged pipeline.
#
#   ./scripts/daily_refresh.sh            # capture + merge + build
#   NO_BUILD=1 ./scripts/daily_refresh.sh # capture + merge only
#
# Publishing (git commit + push of site_v2) stays a separate, manual step.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
REPORT="${ACR_REPORT_ROOT:-/workspace/acr-map/report}"
export ACR_REPORT_ROOT="$REPORT"

"$PY" -m acr_capture sessions >/dev/null
"$PY" -m acr_capture check-session all || echo "!! some sessions expired — run: $PY -m acr_capture login <service>"

"$PY" -m acr_capture capture --from-captures --only social --skip-groups --write-report
"$PY" -m acr_capture capture --from-captures --only web --write-report

if [[ -z "${NO_BUILD:-}" ]]; then
  (cd "$REPORT" && "$PY" build_site_v2.py)
fi
