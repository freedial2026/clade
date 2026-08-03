#!/bin/sh
# Regenerate the dashboard snapshot and hand it to boatpred's PHP-FPM
# pool. Runs as ash (same DB peer-auth as every other cron job); the
# snapshot itself is written world-readable so boatpred can read it
# without an account/group change on either user.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

if [ ! -f .env ]; then
    echo "refresh_dashboard.sh: no .env in $root; DATABASE_URL would be unset" >&2
    exit 1
fi

# shellcheck disable=SC1091  # .env is deployment config, not in the repo
set -a
. ./.env
set +a

tmp=$(mktemp)
".venv/bin/python" -m boat_prediction.db.dashboard_report --output "$tmp"
sudo install -m 644 "$tmp" /srv/boat-dashboard/snapshot/dashboard-snapshot.json
rm -f "$tmp"
