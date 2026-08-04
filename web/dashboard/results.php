<?php

declare(strict_types=1);
require_once __DIR__ . '/src/helpers.php';

/**
 * Prediction-vs-result report, read from the JSON `db.results_report`
 * writes. Deliberately does not go through `bootstrap.php` -- that file's
 * `validate_dashboard_data` checks the vendor decision-desk template's own
 * schema (site/risk/venues/races), which this report does not share, and
 * this page needs no session/CSRF/paper-bet state.
 */

$snapshotPath = getenv('RESULTS_SNAPSHOT_FILE') ?: __DIR__ . '/snapshot/results-report-snapshot.json';
if (!is_file($snapshotPath)) {
    throw new RuntimeException(
        'Results report snapshot not found: ' . $snapshotPath .
        ' -- has boat_prediction.db.results_report been run?'
    );
}
$raw = file_get_contents($snapshotPath);
if ($raw === false) {
    throw new RuntimeException('Could not read results report snapshot: ' . $snapshotPath);
}
$report = json_decode($raw, true);
if (!is_array($report)) {
    throw new RuntimeException('Results report snapshot did not decode to an array: ' . $snapshotPath);
}
foreach (['dates', 'default_date', 'overall_summary', 'summary_by_date', 'races_by_date', 'cron_report'] as $key) {
    if (!array_key_exists($key, $report)) {
        throw new RuntimeException("results report data missing required key: {$key}");
    }
}

$dates = $report['dates'];
$requestedDate = $_GET['date'] ?? null;
$selectedDate = (is_string($requestedDate) && in_array($requestedDate, $dates, true))
    ? $requestedDate
    : $report['default_date'];

$summary = $report['summary_by_date'][$selectedDate] ?? null;
$races = $report['races_by_date'][$selectedDate] ?? [];
$overall = $report['overall_summary'];
$cronJobs = $report['cron_report']['jobs'] ?? [];
?>
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#07111f">
  <title>予想結果レポート — Race Decision Desk</title>
  <meta name="description" content="各レースの予想（出走表時点・直前情報反映後）と実際の結果、的中状況、cronによるデータ取得状況をまとめたレポートページ。">
  <link rel="stylesheet" href="assets/styles.css">
</head>
<body>
<div class="app-shell">
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark">RD</div>
      <div>
        <strong>Race Decision Desk</strong>
        <span>予想結果レポート</span>
      </div>
    </div>
    <nav class="top-nav">
      <a href="index.php">ダッシュボード</a>
      <a href="results.php" class="active">予想結果レポート</a>
    </nav>
    <div class="header-status">
      <span class="status-chip"><?= e((new DateTimeImmutable($report['generated_at']))->format('m/d H:i')) ?> 更新</span>
    </div>
  </header>

  <main>
    <section class="urgent-section card-panel">
      <div class="section-heading">
        <div><span class="eyebrow">PREDICTION VS RESULT</span><h1>各レースの予想と結果</h1></div>
        <span class="board-summary">card: <?= e($report['card_model_version'] ?? '(未登録)') ?> / preview: <?= e($report['preview_model_version'] ?? '(未登録)') ?></span>
      </div>
      <div class="urgent-note">予想は締切前に記録した確率、結果は確定した着順です。的中率は勝利艇の的中/不的中の割合であり、回収率（ROI）ではありません。</div>

      <div class="date-tabs">
        <?php foreach ($dates as $d): ?>
          <a href="?date=<?= e($d) ?>" class="<?= $d === $selectedDate ? 'active' : '' ?>"><?= e($d) ?></a>
        <?php endforeach; ?>
      </div>

      <?php if ($summary): ?>
      <div class="summary-cards">
        <div><span>総レース数</span><strong><?= e($summary['races_total']) ?></strong></div>
        <div><span>確定</span><strong><?= e($summary['finished']) ?></strong></div>
        <div><span>中止/結果なし</span><strong><?= e($summary['cancelled'] + $summary['void']) ?></strong></div>
        <div><span>card 的中率</span><strong><?= $summary['card_hit_rate'] === null ? '—' : e(number_format($summary['card_hit_rate'] * 100, 1)) . '%' ?></strong></div>
        <div><span>preview 的中率</span><strong><?= $summary['preview_hit_rate'] === null ? '—' : e(number_format($summary['preview_hit_rate'] * 100, 1)) . '%' ?></strong></div>
      </div>
      <?php endif; ?>

      <div class="table-wrap">
        <table class="report-table">
          <thead>
            <tr>
              <th>会場</th><th>R</th><th>締切</th><th>状態</th>
              <th>予想 (card)</th><th>予想 (preview)</th><th>結果</th>
              <th>card</th><th>preview</th>
            </tr>
          </thead>
          <tbody>
            <?php if (!$races): ?>
              <tr><td colspan="9">この日のレースはありません。</td></tr>
            <?php endif; ?>
            <?php foreach ($races as $race): ?>
              <?php $cardBadge = hit_badge($race['card_hit']); $previewBadge = hit_badge($race['preview_hit']); ?>
              <tr>
                <td><?= e($race['venue_name']) ?></td>
                <td><?= e($race['race_number']) ?>R</td>
                <td><?= $race['scheduled_deadline_at'] !== null ? e(format_deadline($race['scheduled_deadline_at'])) : '—' ?></td>
                <td><span class="state-pill <?= e($race['race_state']) ?>"><?= e(race_state_label($race['race_state'])) ?></span></td>
                <td>
                  <?php if ($race['card_prediction']['top_lane'] !== null): ?>
                    <?= e($race['card_prediction']['top_lane']) ?>号艇 <span class="mini-chip"><?= e(probability_text($race['card_prediction']['probability'])) ?></span>
                  <?php else: ?>—<?php endif; ?>
                </td>
                <td>
                  <?php if ($race['preview_prediction']['top_lane'] !== null): ?>
                    <?= e($race['preview_prediction']['top_lane']) ?>号艇 <span class="mini-chip"><?= e(probability_text($race['preview_prediction']['probability'])) ?></span>
                  <?php else: ?>—<?php endif; ?>
                </td>
                <td><?= e(lanes_text($race['winner_lanes'])) ?></td>
                <td class="<?= e($cardBadge['class']) ?>"><?= e($cardBadge['label']) ?></td>
                <td class="<?= e($previewBadge['class']) ?>"><?= e($previewBadge['label']) ?></td>
              </tr>
            <?php endforeach; ?>
          </tbody>
        </table>
      </div>
    </section>

    <section class="report-section">
      <article class="info-panel card-panel report-panel">
        <div class="section-heading compact">
          <div><span class="eyebrow">OVERALL</span><h2>直近<?= e(count($dates)) ?>日間の集計</h2></div>
        </div>
        <div class="summary-cards">
          <div><span>総レース数</span><strong><?= e($overall['races_total']) ?></strong></div>
          <div><span>確定</span><strong><?= e($overall['finished']) ?></strong></div>
          <div><span>card 判定数</span><strong><?= e($overall['card_decided']) ?></strong></div>
          <div><span>card 的中率</span><strong><?= $overall['card_hit_rate'] === null ? '—' : e(number_format($overall['card_hit_rate'] * 100, 1)) . '%' ?></strong></div>
          <div><span>preview 的中率</span><strong><?= $overall['preview_hit_rate'] === null ? '—' : e(number_format($overall['preview_hit_rate'] * 100, 1)) . '%' ?></strong></div>
        </div>
      </article>
    </section>

    <section class="report-section">
      <article class="info-panel card-panel report-panel">
        <div class="section-heading compact">
          <div><span class="eyebrow">CRON JOBS</span><h2>cronで取得しているデータ (.21)</h2></div>
        </div>
        <div class="urgent-note">実行タイミングは <code>crontab</code> (リポジトリ外、host: 192.168.11.21) の実際の設定を転記したものです。件数・期間は同じデータベースから実測した値です。</div>
        <div class="table-wrap">
          <table class="report-table">
            <thead><tr><th>実行タイミング</th><th>ジョブ</th><th>取得データ</th><th>件数</th><th>対象レース数</th><th>期間</th></tr></thead>
            <tbody>
              <?php foreach ($cronJobs as $job): ?>
                <tr>
                  <td><?= e($job['schedule']) ?></td>
                  <td><code><?= e($job['module']) ?></code></td>
                  <td><?= e($job['label']) ?></td>
                  <td><?= $job['count'] === null ? '—' : e(number_format($job['count'])) ?></td>
                  <td><?= $job['distinct_races'] === null ? '—' : e(number_format($job['distinct_races'])) ?></td>
                  <td>
                    <?php if ($job['since'] && $job['through']): ?>
                      <?= e((new DateTimeImmutable($job['since']))->format('Y-m-d H:i')) ?>
                      〜
                      <?= e((new DateTimeImmutable($job['through']))->format('Y-m-d H:i')) ?>
                    <?php else: ?>
                      —
                    <?php endif; ?>
                  </td>
                </tr>
              <?php endforeach; ?>
            </tbody>
          </table>
        </div>
      </article>
    </section>
  </main>
  <footer><span>表示データはデモです。投資成果を保証するものではありません。</span><span>Model card=<?= e($report['card_model_version'] ?? '—') ?> / preview=<?= e($report['preview_model_version'] ?? '—') ?></span></footer>
</div>
</body>
</html>
