<?php

declare(strict_types=1);
require __DIR__ . '/bootstrap.php';

$site = $dashboard['site'];
$risk = $dashboard['risk'];
$venues = $dashboard['venues'];
$races = $dashboard['races'];
$activeVenueCount = count($venues);
$remainingRaceCount = array_sum(array_column($venues, 'remaining_race_count'));
$remainingDailyLimitYen = max(0, $risk['daily_limit_yen'] - $risk['spent_today_yen']);
$dailyUsagePercent = $risk['daily_limit_yen'] > 0
    ? min(100, (int) round($risk['spent_today_yen'] / $risk['daily_limit_yen'] * 100))
    : 0;
$selectedRace = $races[0];
$collectionReport = $dashboard['collection_report'] ?? null;
$roiReport = $dashboard['roi_report'] ?? null;
?>
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#07111f">
  <!-- $site['page_title'] -->
  <title><?= e($site['page_title']) ?></title>
  <!-- $site['meta_description'] -->
  <meta name="description" content="<?= e($site['meta_description']) ?>">
  <link rel="stylesheet" href="assets/styles.css">
</head>
<body>
<div class="app-shell">
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark">RD</div>
      <div>
        <!-- $site['brand_name'] / $site['brand_subtitle'] -->
        <strong><?= e($site['brand_name']) ?></strong>
        <span><?= e($site['brand_subtitle']) ?></span>
      </div>
    </div>
    <div class="header-status">
      <!-- $activeVenueCount -->
      <span class="status-chip status-live"><i></i><b id="activeVenueCount"><?= e($activeVenueCount) ?></b>会場開催中</span>
      <!-- $remainingRaceCount -->
      <span class="status-chip"><b id="remainingRaceCount"><?= e($remainingRaceCount) ?></b>レース残り</span>
      <!-- $site['operating_mode_label'] -->
      <span class="status-chip"><?= e($site['operating_mode_label']) ?></span>
    </div>
  </header>

  <section class="risk-strip" aria-label="資金と上限">
    <!-- $risk['bankroll_yen'] -->
    <div><span>検証資金</span><strong><?= e(yen($risk['bankroll_yen'])) ?></strong></div>
    <!-- $risk['spent_today_yen'] -->
    <div><span>本日使用</span><strong id="spentValue"><?= e(yen($risk['spent_today_yen'])) ?></strong></div>
    <!-- $risk['daily_limit_yen'] -->
    <div><span>本日上限</span><strong><?= e(yen($risk['daily_limit_yen'])) ?></strong></div>
    <!-- $remainingDailyLimitYen -->
    <div><span>残り上限</span><strong id="remainingValue"><?= e(yen($remainingDailyLimitYen)) ?></strong></div>
    <div class="risk-progress-wrap">
      <div class="risk-progress-label"><span>日次利用率</span><span id="riskPercent"><?= e($dailyUsagePercent) ?>%</span></div>
      <div class="risk-progress"><span id="riskProgress" style="width:<?= e($dailyUsagePercent) ?>%"></span></div>
    </div>
  </section>

  <main>
    <section class="urgent-section card-panel">
      <div class="section-heading">
        <div><span class="eyebrow">NEXT DEADLINES ACROSS VENUES</span><h1>複数会場の次締切</h1></div>
        <!-- $site['last_updated_at'] -->
        <div class="updated">最終更新 <b id="lastUpdated"><?= e((new DateTimeImmutable($site['last_updated_at']))->format('H:i:s')) ?></b></div>
      </div>
      <div class="urgent-note">締切が近い順です。必要データは割合ではなく、取得済み件数と不足項目を表示します。</div>
      <div class="deadline-lane" id="deadlineLane">
        <?php foreach (array_slice($races, 0, 6) as $race): $coverage = $race['data_coverage']; ?>
          <article class="deadline-card" data-race-id="<?= e($race['race_id']) ?>">
            <div class="deadline-head">
              <!-- $race['venue_name'] / $race['race_number'] -->
              <strong><?= e($race['venue_name']) ?> <?= e($race['race_number']) ?>R</strong>
              <span class="decision-pill <?= e(decision_css_class($race['decision_status'])) ?>"><?= e($race['decision_label']) ?></span>
            </div>
            <!-- $race['scheduled_deadline_at'] -->
            <div class="deadline-time" data-deadline-at="<?= e($race['scheduled_deadline_at']) ?>">--:--</div>
            <div class="deadline-at">締切 <?= e(format_deadline($race['scheduled_deadline_at'])) ?></div>
            <div class="deadline-detail">
              <div><span>買い目候補</span><b><?= e($race['recommended_bet']['bet_type_label']) ?> <?= e($race['recommended_bet']['combination']) ?></b></div>
              <div><span>このレースの上限</span><b><?= e(yen($race['max_stake_yen'])) ?></b></div>
              <div><span>100円の期待払戻</span><b><?= e(expected_return_text($race['expected_return_per_100_yen'])) ?></b></div>
              <!-- $coverage['obtained'] / $coverage['total'] -->
              <div><span>必要データ</span><b><?= e($coverage['obtained']) ?> / <?= e($coverage['total']) ?>項目</b></div>
            </div>
            <?php if ($coverage['missing_labels']): ?>
              <div class="missing-data">未取得：<?= e(implode('・', $coverage['missing_labels'])) ?></div>
            <?php else: ?>
              <div class="missing-data complete">すべて取得済み</div>
            <?php endif; ?>
            <button class="deadline-action" data-select-race="<?= e($race['race_id']) ?>">このレースを選択</button>
          </article>
        <?php endforeach; ?>
      </div>
    </section>

    <section class="workspace-grid">
      <div class="workspace-main">
        <section class="venue-overview card-panel">
          <div class="section-heading compact">
            <div><span class="eyebrow">VENUE OVERVIEW</span><h2>会場別の状況</h2></div>
            <div class="legend"><span><i class="dot candidate"></i>検証候補</span><span><i class="dot waiting"></i>情報待ち</span><span><i class="dot skip"></i>見送り</span></div>
          </div>
          <div class="venue-grid" id="venueGrid">
            <?php foreach ($venues as $venue): ?>
              <article class="venue-card" data-filter-venue="<?= e($venue['venue_code']) ?>">
                <div class="venue-card-top">
                  <div class="venue-id">
                    <span class="venue-code"><?= e($venue['venue_code']) ?></span>
                    <div><strong><?= e($venue['venue_name']) ?></strong><span><?= e($venue['water_type_label']) ?></span></div>
                  </div>
                  <!-- $venue['required_data_obtained'] / $venue['required_data_total'] -->
                  <span class="data-state">必要データ <?= e($venue['required_data_obtained']) ?>/<?= e($venue['required_data_total']) ?></span>
                </div>
                <div class="venue-card-mid">
                  <div><span>残りレース</span><b><?= e($venue['remaining_race_count']) ?>R</b></div>
                  <div><span>検証候補</span><b><?= e($venue['candidate_count']) ?></b></div>
                  <div><span>情報待ち</span><b><?= e($venue['waiting_count']) ?></b></div>
                </div>
                <div class="venue-next"><span>次 <?= e($venue['next_race_number'] ?? '—') ?>R</span><strong><?= $venue['next_deadline_at'] ? e(format_deadline($venue['next_deadline_at'])) : '終了' ?></strong></div>
              </article>
            <?php endforeach; ?>
          </div>
        </section>

        <section class="controls">
          <div class="segmented" role="group" aria-label="一覧の表示方法">
            <button class="active" data-filter-status="all">すべて</button>
            <button data-filter-status="candidate">検証候補</button>
            <button data-filter-status="waiting">情報待ち</button>
            <button data-filter-status="skip">見送り</button>
          </div>
          <div class="control-right">
            <label class="search-box"><span>⌕</span><input id="raceSearch" type="search" placeholder="会場名・レース番号"></label>
            <select id="venueFilter" aria-label="会場で絞り込み">
              <option value="all">全会場</option>
              <?php foreach ($venues as $venue): ?><option value="<?= e($venue['venue_code']) ?>"><?= e($venue['venue_name']) ?></option><?php endforeach; ?>
            </select>
          </div>
        </section>

        <section class="race-board card-panel">
          <div class="section-heading compact">
            <div><span class="eyebrow">RACE BOARD</span><h2>全会場・締切順</h2></div>
            <span class="board-summary" id="boardSummary"><?= e(count($races)) ?>レース</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>締切</th><th>会場</th><th>R</th><th>状態</th><th>買い目候補</th><th>レース上限</th><th>今のオッズ</th><th>5分前→現在</th><th>100円の期待払戻</th><th>必要データ</th><th>不足項目</th><th></th></tr></thead>
              <tbody id="raceRows">
              <?php foreach ($races as $race): $coverage = $race['data_coverage']; ?>
                <tr data-race-row data-race-id="<?= e($race['race_id']) ?>" data-venue-code="<?= e($race['venue_code']) ?>" data-decision-status="<?= e($race['decision_status']) ?>" data-search-text="<?= e($race['venue_name'] . $race['race_number'] . 'R') ?>">
                  <td><?= e(format_deadline($race['scheduled_deadline_at'])) ?></td>
                  <td><?= e($race['venue_name']) ?></td>
                  <td><strong><?= e($race['race_number']) ?>R</strong></td>
                  <td><span class="decision-pill <?= e(decision_css_class($race['decision_status'])) ?>"><?= e($race['decision_label']) ?></span></td>
                  <td><?= e($race['recommended_bet']['bet_type_label']) ?> <?= e($race['recommended_bet']['combination']) ?></td>
                  <td><?= e(yen($race['max_stake_yen'])) ?></td>
                  <td><?= e(odds_text($race['current_odds'])) ?></td>
                  <td><?= e(odds_text($race['odds_5_minutes_ago'])) ?> → <?= e(odds_text($race['current_odds'])) ?></td>
                  <td><?= e(expected_return_text($race['expected_return_per_100_yen'])) ?></td>
                  <td><?= e($coverage['obtained']) ?> / <?= e($coverage['total']) ?></td>
                  <td><?= $coverage['missing_labels'] ? e(implode('・', $coverage['missing_labels'])) : 'なし' ?></td>
                  <td><button class="select-race" data-select-race="<?= e($race['race_id']) ?>">選択</button></td>
                </tr>
              <?php endforeach; ?>
              </tbody>
            </table>
          </div>
          <div class="mobile-race-list">
            <?php foreach ($races as $race): $coverage = $race['data_coverage']; ?>
              <article class="mobile-card" data-race-row data-race-id="<?= e($race['race_id']) ?>" data-venue-code="<?= e($race['venue_code']) ?>" data-decision-status="<?= e($race['decision_status']) ?>" data-search-text="<?= e($race['venue_name'] . $race['race_number'] . 'R') ?>">
                <div class="mobile-card-head"><div><strong><?= e($race['venue_name']) ?> <?= e($race['race_number']) ?>R</strong><span>締切 <?= e(format_deadline($race['scheduled_deadline_at'])) ?></span></div><span class="decision-pill <?= e(decision_css_class($race['decision_status'])) ?>"><?= e($race['decision_label']) ?></span></div>
                <div class="mobile-card-stats"><div><span>買い目候補</span><b><?= e($race['recommended_bet']['bet_type_label']) ?> <?= e($race['recommended_bet']['combination']) ?></b></div><div><span>上限</span><b><?= e(yen($race['max_stake_yen'])) ?></b></div><div><span>必要データ</span><b><?= e($coverage['obtained']) ?>/<?= e($coverage['total']) ?></b></div></div>
                <div class="mobile-card-actions"><span><?= $coverage['missing_labels'] ? '未取得：' . e(implode('・', $coverage['missing_labels'])) : 'すべて取得済み' ?></span><button class="select-race" data-select-race="<?= e($race['race_id']) ?>">選択</button></div>
              </article>
            <?php endforeach; ?>
          </div>
        </section>
      </div>

      <aside class="ticket-panel card-panel" id="ticketPanel">
        <div class="section-heading compact ticket-heading"><div><span class="eyebrow">COMMON BET SLIP</span><h2>共通投票票</h2></div><button class="text-button" id="clearTicket">クリア</button></div>
        <div class="selected-race-banner">
          <span id="selectedVenueCode"><?= e($selectedRace['venue_code']) ?></span>
          <div><small>選択中</small><strong id="selectedRaceName"><?= e($selectedRace['venue_name']) ?> <?= e($selectedRace['race_number']) ?>R</strong><b id="selectedDeadline">締切 <?= e(format_deadline($selectedRace['scheduled_deadline_at'])) ?></b></div>
          <div class="mini-countdown"><small>残り</small><strong id="selectedCountdown">--:--</strong></div>
        </div>

        <div class="decision-box">
          <div><span>今の状態</span><strong id="selectedDecision" class="<?= e(decision_css_class($selectedRace['decision_status'])) ?>-text"><?= e($selectedRace['decision_label']) ?></strong></div>
          <div><span>このレースの上限</span><strong id="selectedMaxStake"><?= e(yen($selectedRace['max_stake_yen'])) ?></strong></div>
          <div><span>100円の期待払戻</span><strong id="selectedExpectedReturn"><?= e(expected_return_text($selectedRace['expected_return_per_100_yen'])) ?></strong></div>
          <div><span>必要データ</span><strong id="selectedDataCount"><?= e($selectedRace['data_coverage']['obtained']) ?> / <?= e($selectedRace['data_coverage']['total']) ?>項目</strong></div>
        </div>
        <div class="data-detail-box" id="selectedDataDetail">
          <strong><?= e($selectedRace['data_coverage']['state_label']) ?></strong>
          <span><?= $selectedRace['data_coverage']['missing_labels'] ? '未取得：' . e(implode('・', $selectedRace['data_coverage']['missing_labels'])) : '不足項目はありません' ?></span>
        </div>
        <div class="plain-explanation"><strong>「100円の期待払戻」の見方</strong><span>同じ条件を長期に繰り返した場合の平均見込みです。元本100円を含み、1回の利益や的中を保証しません。</span></div>

        <label><span>買い目</span><select id="betOption"></select></label>
        <label><span>金額</span><div class="amount-input"><span>¥</span><input id="stake" type="number" min="<?= e($risk['minimum_stake_yen']) ?>" step="<?= e($risk['stake_unit_yen']) ?>" value="<?= e($selectedRace['max_stake_yen'] ?: $risk['minimum_stake_yen']) ?>" inputmode="numeric"></div></label>
        <div class="quick-amounts"><button data-amount="100">100</button><button data-amount="300">300</button><button data-amount="500">500</button><button data-amount="1000">1,000</button></div>
        <div class="ticket-metrics"><div><span>今のオッズ</span><b id="ticketOdds"><?= e(odds_text($selectedRace['current_odds'])) ?></b></div><div><span>的中時払戻目安</span><b id="estimatedPayout">—</b></div><div><span>最大損失</span><b id="maxLoss">—</b></div><div><span>投票後の残り上限</span><b id="afterLimit">—</b></div></div>
        <div class="reason-list" id="selectedReasons"></div>
        <div class="ticket-warning" id="ticketWarning" hidden></div>
        <label class="confirm-check"><input type="checkbox" id="confirmCheck"><span>会場・レース・買い目・金額を確認しました</span></label>
        <button class="primary-action" id="paperVote" disabled>紙上投票を記録</button>
        <button class="secondary-action" disabled>実投票連携（無効）</button>
        <p class="fine-print">実投票、自動購入、連敗時の増額、追加入金は実装していません。</p>
      </aside>
    </section>

    <section class="lower-grid">
      <article class="info-panel card-panel"><div class="section-heading compact"><div><span class="eyebrow">ODDS CHANGE</span><h2>選択レースのオッズの動き</h2></div><span class="mini-chip" id="chartLabel"></span></div><div class="odds-history" id="oddsHistory"></div><div class="chart-callout" id="chartCallout"></div></article>
      <article class="info-panel card-panel"><div class="section-heading compact"><div><span class="eyebrow">DATA AVAILABILITY</span><h2>選択レースの取得状況</h2></div></div><div class="health-list" id="selectedAvailabilityList"></div><div class="coverage-note"><strong>分母とカバレッジ</strong><span id="coverageText"></span></div></article>
      <article class="info-panel card-panel"><div class="section-heading compact"><div><span class="eyebrow">AUDIT LOG</span><h2>記録した紙上投票</h2></div></div><div class="empty-log" id="paperLog"><strong>まだ記録がありません</strong><span>会場、レース、買い目、金額、オッズ、モデル版を記録します。</span></div></article>
    </section>

    <?php if ($collectionReport): ?>
    <section class="report-section">
      <article class="info-panel card-panel report-panel">
        <div class="section-heading compact">
          <div><span class="eyebrow">DATA COLLECTION</span><h2>取得済みデータ件数</h2></div>
          <span class="mini-chip"><?= e((new DateTimeImmutable($collectionReport['generated_at']))->format('m/d H:i')) ?> 時点</span>
        </div>
        <div class="table-wrap">
          <table class="report-table">
            <thead><tr><th>データ源</th><th>件数</th><th>対象レース数</th><th>期間</th></tr></thead>
            <tbody>
              <?php foreach ($collectionReport['sources'] as $source): ?>
                <tr>
                  <td><?= e($source['label']) ?></td>
                  <td><strong><?= e(number_format($source['count'])) ?></strong></td>
                  <td><?= e(number_format($source['distinct_races'])) ?></td>
                  <td>
                    <?php if ($source['since'] && $source['through']): ?>
                      <?= e((new DateTimeImmutable($source['since']))->format('Y-m-d H:i')) ?>
                      〜
                      <?= e((new DateTimeImmutable($source['through']))->format('Y-m-d H:i')) ?>
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
    <?php endif; ?>

    <?php if ($roiReport): ?>
    <section class="report-section">
      <article class="info-panel card-panel report-panel">
        <div class="section-heading compact">
          <div><span class="eyebrow">ROI REPORT</span><h2>現在のROI（バックテスト結果）</h2></div>
          <span class="mini-chip"><?= e((new DateTimeImmutable($roiReport['generated_at']))->format('m/d H:i')) ?> 時点</span>
        </div>
        <div class="roi-note"><?= e($roiReport['note']) ?></div>
        <div class="table-wrap">
          <table class="report-table">
            <thead><tr><th>戦略</th><th>ROI</th><th>件数</th><th>備考</th></tr></thead>
            <tbody>
              <?php foreach ($roiReport['baselines'] as $row): ?>
                <tr>
                  <td><?= e($row['label']) ?></td>
                  <td class="<?= $row['roi'] >= $roiReport['break_even_roi'] ? 'roi-positive' : 'roi-negative' ?>"><?= e(number_format($row['roi'], 4)) ?></td>
                  <td><?= e(number_format($row['n'])) ?></td>
                  <td><?= e($row['note']) ?></td>
                </tr>
              <?php endforeach; ?>
              <?php $ev = $roiReport['ev_hypothesis']; ?>
              <tr class="roi-hypothesis-row">
                <td><?= e($ev['label']) ?> <span class="mini-chip">仮説</span></td>
                <td class="<?= $ev['roi'] >= $roiReport['break_even_roi'] ? 'roi-positive' : 'roi-negative' ?>">
                  <?= e(number_format($ev['roi'], 4)) ?>
                  <small>（上位払戻除く <?= e(number_format($ev['trimmed_roi'], 4)) ?>）</small>
                </td>
                <td><?= e(number_format($ev['n'])) ?> (的中 <?= e(number_format($ev['hits'])) ?>)</td>
                <td><?= e($ev['note']) ?></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="roi-breakeven">損益分岐 ROI = <?= e(number_format($roiReport['break_even_roi'], 4)) ?>（控除率により、これを下回るのが基準です）</div>
      </article>
    </section>
    <?php endif; ?>
  </main>
  <footer><span>表示データはデモです。投資成果を保証するものではありません。</span><span>Model <?= e($site['model_version']) ?> / Policy <?= e($site['policy_version']) ?></span></footer>
</div>

<div class="modal-backdrop" id="confirmModal" hidden><div class="modal" role="dialog" aria-modal="true"><span class="eyebrow">FINAL CHECK</span><h2>紙上投票を記録しますか？</h2><div class="modal-summary" id="modalSummary"></div><div class="modal-risk"><strong>最大損失は投票額までです。</strong><span>損失を取り戻す目的の増額は行わないでください。</span></div><div class="modal-actions"><button class="secondary-action" id="cancelVote">戻る</button><button class="primary-action" id="confirmVote">記録する</button></div></div></div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script>
window.DASHBOARD_DATA = <?= json_for_html($dashboard) ?>;
window.DASHBOARD_CONFIG = <?= json_for_html([
    'csrfToken' => $_SESSION['csrf_token'],
    'paperBetEndpoint' => 'api/paper-bet.php',
]) ?>;
</script>
<script src="assets/app.js"></script>
</body>
</html>
