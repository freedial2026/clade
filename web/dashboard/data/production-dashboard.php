<?php

declare(strict_types=1);

/**
 * Real data, read from the JSON snapshot `db.dashboard_report` writes.
 *
 * DB access stays entirely in Python: this file's only job is to decode
 * the snapshot and return the same array shape `sample-dashboard.php`
 * does. Keeping the schema itself in one place (`dashboard_report.py`,
 * next to the SQLAlchemy models it reads) is what stops the two
 * languages' ideas of "a race" from drifting apart.
 *
 * Path defaults to `../snapshot/dashboard-snapshot.json`, next to this
 * file's parent, but can be overridden with DASHBOARD_SNAPSHOT_FILE for
 * local testing against a copied-down snapshot.
 */

$snapshotPath = getenv('DASHBOARD_SNAPSHOT_FILE') ?: dirname(__DIR__) . '/snapshot/dashboard-snapshot.json';

if (!is_file($snapshotPath)) {
    throw new RuntimeException(
        'Dashboard snapshot not found: ' . $snapshotPath .
        ' -- has boat_prediction.db.dashboard_report been run?'
    );
}

$raw = file_get_contents($snapshotPath);
if ($raw === false) {
    throw new RuntimeException('Could not read dashboard snapshot: ' . $snapshotPath);
}

$data = json_decode($raw, true);
if (!is_array($data)) {
    throw new RuntimeException('Dashboard snapshot did not decode to an array: ' . $snapshotPath);
}

return $data;
