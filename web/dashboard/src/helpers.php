<?php

declare(strict_types=1);

function e(mixed $value): string
{
    return htmlspecialchars((string) $value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function yen(int|float|null $value): string
{
    if ($value === null) {
        return '—';
    }
    return '¥' . number_format((float) $value, 0);
}

function odds_text(float|int|null $value): string
{
    return $value === null ? '—' : number_format((float) $value, 1) . '倍';
}

function expected_return_text(int|float|null $value): string
{
    return $value === null ? '算出不可' : number_format((float) $value, 0) . '円';
}

function decision_css_class(string $status): string
{
    return match ($status) {
        'candidate' => 'candidate',
        'waiting' => 'waiting',
        default => 'skip',
    };
}

function tone_css_class(string $tone): string
{
    return match ($tone) {
        'positive' => 'positive',
        'neutral' => 'neutral',
        default => 'risk',
    };
}

function format_deadline(string $isoDateTime): string
{
    return (new DateTimeImmutable($isoDateTime))->format('H:i');
}

/** @return array{obtained:int,total:int,missing_codes:list<string>,missing_labels:list<string>,critical_missing_codes:list<string>,critical_missing_labels:list<string>,state_label:string} */
function calculate_data_coverage(array $race, array $dataCatalog): array
{
    $availability = $race['data_availability'] ?? [];
    $total = count($dataCatalog);
    $obtained = 0;
    $missingCodes = [];
    $missingLabels = [];
    $criticalMissingCodes = [];
    $criticalMissingLabels = [];

    foreach ($dataCatalog as $code => $definition) {
        if (($availability[$code] ?? false) === true) {
            $obtained++;
            continue;
        }
        $missingCodes[] = $code;
        $missingLabels[] = $definition['label'];
        if (($definition['critical'] ?? false) === true) {
            $criticalMissingCodes[] = $code;
            $criticalMissingLabels[] = $definition['label'];
        }
    }

    $stateLabel = match (true) {
        $obtained === $total => 'すべて取得済み',
        count($criticalMissingCodes) > 0 => '判断材料が不足',
        default => 'ほぼ揃っています',
    };

    return [
        'obtained' => $obtained,
        'total' => $total,
        'missing_codes' => $missingCodes,
        'missing_labels' => $missingLabels,
        'critical_missing_codes' => $criticalMissingCodes,
        'critical_missing_labels' => $criticalMissingLabels,
        'state_label' => $stateLabel,
    ];
}

function prepare_dashboard_data(array $dashboard): array
{
    $catalog = $dashboard['data_catalog'] ?? [];
    foreach ($dashboard['races'] as &$race) {
        $race['data_coverage'] = calculate_data_coverage($race, $catalog);
    }
    unset($race);

    $raceById = [];
    foreach ($dashboard['races'] as $race) {
        $raceById[$race['race_id']] = $race;
    }

    foreach ($dashboard['venues'] as &$venue) {
        $nextRace = $raceById[$venue['next_race_id']] ?? null;
        $venue['next_race_number'] = $nextRace['race_number'] ?? null;
        $venue['next_deadline_at'] = $nextRace['scheduled_deadline_at'] ?? null;

        $venueRaces = array_values(array_filter(
            $dashboard['races'],
            static fn(array $race): bool => $race['venue_code'] === $venue['venue_code']
        ));
        $obtained = array_sum(array_column(array_column($venueRaces, 'data_coverage'), 'obtained'));
        $total = array_sum(array_column(array_column($venueRaces, 'data_coverage'), 'total'));
        $venue['required_data_obtained'] = $obtained;
        $venue['required_data_total'] = $total;
    }
    unset($venue);

    return $dashboard;
}

function validate_dashboard_data(array $dashboard): void
{
    foreach (['site', 'risk', 'data_catalog', 'venues', 'races'] as $requiredKey) {
        if (!array_key_exists($requiredKey, $dashboard)) {
            throw new RuntimeException("dashboard data missing required key: {$requiredKey}");
        }
    }

    $raceIds = [];
    foreach ($dashboard['races'] as $race) {
        foreach (['race_id','venue_code','venue_name','race_number','scheduled_deadline_at','decision_status','decision_label','max_stake_yen','data_availability'] as $key) {
            if (!array_key_exists($key, $race)) {
                throw new RuntimeException("race data missing {$key}");
            }
        }
        if (isset($raceIds[$race['race_id']])) {
            throw new RuntimeException("duplicate race_id: {$race['race_id']}");
        }
        $raceIds[$race['race_id']] = true;
    }
}

function json_for_html(mixed $value): string
{
    return json_encode(
        $value,
        JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_THROW_ON_ERROR
    );
}
