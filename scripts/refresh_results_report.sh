#!/bin/sh
# Regenerate the prediction-vs-result report snapshot and hand it to
# boatpred's PHP-FPM pool. Mirrors refresh_dashboard.sh: same .env
# sourcing, same sudo install pattern, only the module and output path
# differ.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

if [ ! -f .env ]; then
    echo "refresh_results_report.sh: no .env in $root; DATABASE_URL would be unset" >&2
    exit 1
fi

# shellcheck disable=SC1091  # .env is deployment config, not in the repo
set -a
. ./.env
set +a

tmp=$(mktemp)
".venv/bin/python" -m boat_prediction.db.results_report --output "$tmp"
sudo install -m 644 "$tmp" /srv/boat-dashboard/snapshot/results-report-snapshot.json
rm -f "$tmp"
