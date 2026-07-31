#!/bin/sh
# Run one of the project's Python modules the way cron needs it.
#
# cron gives a job almost no environment: no PATH to the venv, no
# working directory, and none of the variables an interactive shell
# would have sourced. Rather than repeat that setup in every crontab
# line -- where a typo is silent until the job stops running -- every
# scheduled job goes through here.
#
#   scripts/cron_job.sh boat_prediction.db.ingest_daily card
#
# Output goes to logs/cron-YYYYMM.log, rotating monthly on its own so
# the crontab needs no date expansion (% is special there and has to be
# escaped, which is a common way to break a crontab line).
#
# Exits with the module's own status so cron reports a real failure.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

if [ ! -f .env ]; then
    echo "cron_job.sh: no .env in $root; DATABASE_URL would be unset" >&2
    exit 1
fi

# shellcheck disable=SC1091  # .env is deployment config, not in the repo
set -a
. ./.env
set +a

mkdir -p logs
log="logs/cron-$(date +%Y%m).log"

echo "=== $(date -Is) $* ===" >>"$log"
# `set -e` would abort here on a non-zero exit, before the status could
# be logged -- and a job that fails silently in a log nobody reads is
# the failure mode this wrapper exists to prevent.
set +e
".venv/bin/python" -m "$@" >>"$log" 2>&1
status=$?
set -e
echo "=== $(date -Is) exit=$status ===" >>"$log"
exit "$status"
