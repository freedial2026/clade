<?php

declare(strict_types=1);

if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}

require_once __DIR__ . '/src/helpers.php';

$dataFile = getenv('DASHBOARD_DATA_FILE') ?: __DIR__ . '/data/sample-dashboard.php';
if (!is_file($dataFile)) {
    throw new RuntimeException('Dashboard data file not found: ' . $dataFile);
}

$dashboard = require $dataFile;
validate_dashboard_data($dashboard);
$dashboard = prepare_dashboard_data($dashboard);

$_SESSION['paper_bets'] ??= [];
$_SESSION['csrf_token'] ??= bin2hex(random_bytes(24));

$sessionSpent = array_sum(array_map(
    static fn(array $bet): int => (int) ($bet['stake_yen'] ?? 0),
    $_SESSION['paper_bets']
));
$dashboard['risk']['spent_today_yen'] = max((int) $dashboard['risk']['spent_today_yen'], $sessionSpent);
