<?php

declare(strict_types=1);
require dirname(__DIR__) . '/bootstrap.php';
header('Content-Type: application/json; charset=utf-8');

function fail_json(int $status, string $message): never
{
    http_response_code($status);
    echo json_encode(['ok'=>false,'message'=>$message], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') fail_json(405, 'POSTのみ利用できます。');
if (!hash_equals($_SESSION['csrf_token'], (string) ($_SERVER['HTTP_X_CSRF_TOKEN'] ?? ''))) fail_json(403, 'CSRFトークンが無効です。');
if (!$dashboard['risk']['paper_betting_enabled']) fail_json(403, '紙上投票は停止中です。');

$payload = json_decode((string) file_get_contents('php://input'), true);
if (!is_array($payload)) fail_json(400, 'JSONが不正です。');

$race = null;
foreach ($dashboard['races'] as $candidate) {
    if ($candidate['race_id'] === ($payload['race_id'] ?? null)) { $race = $candidate; break; }
}
if (!$race) fail_json(404, '対象レースが見つかりません。');
if ($race['decision_status'] === 'skip') fail_json(422, '見送りレースは記録できません。');
if ($race['data_coverage']['critical_missing_codes']) fail_json(422, '重要データが不足しています。');

$stake = filter_var($payload['stake_yen'] ?? null, FILTER_VALIDATE_INT);
$minimum = (int) $dashboard['risk']['minimum_stake_yen'];
$unit = (int) $dashboard['risk']['stake_unit_yen'];
if ($stake === false || $stake < $minimum || $stake % $unit !== 0) fail_json(422, '金額単位が不正です。');
if ($stake > (int) $race['max_stake_yen']) fail_json(422, 'レース別上限を超えています。');

$spent = array_sum(array_column($_SESSION['paper_bets'], 'stake_yen'));
if ($spent + $stake > (int) $dashboard['risk']['daily_limit_yen']) fail_json(422, '本日の上限を超えています。');

$validOption = false;
foreach ($race['available_bet_options'] as $option) {
    if ($option['bet_type_code'] === ($payload['bet_type_code'] ?? null) && $option['combination'] === ($payload['combination'] ?? null)) {
        $validOption = true; break;
    }
}
if (!$validOption) fail_json(422, '買い目候補が不正です。');

$recordedAt = (new DateTimeImmutable('now', new DateTimeZone('Asia/Tokyo')))->format(DATE_ATOM);
$record = [
    'paper_bet_id' => bin2hex(random_bytes(12)),
    'race_id' => $race['race_id'],
    'bet_type_code' => $payload['bet_type_code'],
    'combination' => $payload['combination'],
    'stake_yen' => $stake,
    'odds_at_record' => $race['current_odds'],
    'recorded_at' => $recordedAt,
    'model_version' => $dashboard['site']['model_version'],
    'policy_version' => $dashboard['site']['policy_version'],
];
$_SESSION['paper_bets'][] = $record;

echo json_encode([
    'ok' => true,
    'record' => $record,
    'recorded_at' => $recordedAt,
    'spent_today_yen' => $spent + $stake,
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
