# 画面位置とPHP変数の対応表

`index.php`には、主要な差込箇所の直前へPHP変数名をHTMLコメントで記載しています。

| 画面位置 | PHP変数・式 | 表示例 |
|---|---|---|
| ページタイトル | `$site['page_title']` | Race Decision Desk |
| 開催会場数 | `$activeVenueCount` | 6会場開催中 |
| 残りレース数 | `$remainingRaceCount` | 19レース残り |
| 検証資金 | `$risk['bankroll_yen']` | ¥100,000 |
| 本日使用 | `$risk['spent_today_yen']` | ¥400 |
| 本日上限 | `$risk['daily_limit_yen']` | ¥2,000 |
| 残り上限 | `$remainingDailyLimitYen` | ¥1,600 |
| 会場名 | `$venue['venue_name']` | 多摩川 |
| 会場コード | `$venue['venue_code']` | 05 |
| 会場の必要データ | `$venue['required_data_obtained'] / $venue['required_data_total']` | 38/39 |
| レース番号 | `$race['race_number']` | 10R |
| 締切時刻 | `$race['scheduled_deadline_at']` | 22:20 |
| 判断状態 | `$race['decision_label']` | 検証候補 |
| 買い目 | `$race['recommended_bet']` | 2連単 1→3 |
| レース上限 | `$race['max_stake_yen']` | ¥400 |
| 今のオッズ | `$race['current_odds']` | 5.8倍 |
| 5分前オッズ | `$race['odds_5_minutes_ago']` | 5.4倍 |
| 100円の期待払戻 | `$race['expected_return_per_100_yen']` | 108円 |
| 必要データ件数 | `$race['data_coverage']['obtained'] / ['total']` | 12/13項目 |
| 不足項目 | `$race['data_coverage']['missing_labels']` | 部品交換情報 |
| 判断理由 | `$race['decision_reasons'][]` | 展示タイムが上位 |
| オッズ履歴 | `$race['odds_history'][]` | 5.1→5.8 |
| モデル版 | `$site['model_version']` | model-v1 |
| 判断ルール版 | `$site['policy_version']` | paper-only-v2 |

## JavaScriptへ渡す変数

```php
window.DASHBOARD_DATA = <?= json_for_html($dashboard) ?>;
window.DASHBOARD_CONFIG = <?= json_for_html([
    'csrfToken' => $_SESSION['csrf_token'],
    'paperBetEndpoint' => 'api/paper-bet.php',
]) ?>;
```

PHP配列をJSONとして渡し、レース選択・絞り込み・金額検証・カウントダウンに使用します。
