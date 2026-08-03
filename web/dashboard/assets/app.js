(() => {
  'use strict';
  const data = window.DASHBOARD_DATA;
  const config = window.DASHBOARD_CONFIG || {};
  const races = data.races;
  const catalog = data.data_catalog;
  const risk = data.risk;
  let selectedRace = races[0];
  let statusFilter = 'all';
  let venueFilter = 'all';
  let spentToday = Number(risk.spent_today_yen || 0);

  const $ = id => document.getElementById(id);
  const yen = value => value == null ? '—' : new Intl.NumberFormat('ja-JP',{style:'currency',currency:'JPY',maximumFractionDigits:0}).format(value);
  const oddsText = value => value == null ? '—' : `${Number(value).toFixed(1)}倍`;
  const expectedText = value => value == null ? '算出不可' : `${Number(value).toFixed(0)}円`;
  const countdownText = iso => {
    const seconds = Math.max(0, Math.floor((new Date(iso).getTime() - Date.now()) / 1000));
    return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
  };
  const decisionClass = status => status === 'candidate' ? 'candidate-text' : status === 'waiting' ? 'waiting-text' : 'skip-text';
  const selectedOption = () => {
    const raw = $('betOption').value;
    return selectedRace.available_bet_options.find(x => `${x.bet_type_code}|${x.combination}` === raw) || null;
  };

  function updateCountdowns() {
    document.querySelectorAll('[data-deadline-at]').forEach(element => {
      element.textContent = countdownText(element.dataset.deadlineAt);
    });
    $('selectedCountdown').textContent = countdownText(selectedRace.scheduled_deadline_at);
    $('lastUpdated').textContent = new Date().toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  }

  function filterRows() {
    const query = $('raceSearch').value.trim().toLowerCase();
    let visible = 0;
    document.querySelectorAll('[data-race-row]').forEach(row => {
      const statusOk = statusFilter === 'all' || row.dataset.decisionStatus === statusFilter;
      const venueOk = venueFilter === 'all' || row.dataset.venueCode === venueFilter;
      const queryOk = !query || (row.dataset.searchText || '').toLowerCase().includes(query);
      const show = statusOk && venueOk && queryOk;
      row.hidden = !show;
      if (show && row.tagName === 'TR') visible++;
    });
    $('boardSummary').textContent = `${visible}レース`;
  }

  function renderTicket() {
    const coverage = selectedRace.data_coverage;
    $('selectedVenueCode').textContent = selectedRace.venue_code;
    $('selectedRaceName').textContent = `${selectedRace.venue_name} ${selectedRace.race_number}R`;
    $('selectedDeadline').textContent = `締切 ${new Date(selectedRace.scheduled_deadline_at).toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit'})}`;
    $('selectedDecision').textContent = selectedRace.decision_label;
    $('selectedDecision').className = decisionClass(selectedRace.decision_status);
    $('selectedMaxStake').textContent = yen(selectedRace.max_stake_yen);
    $('selectedExpectedReturn').textContent = expectedText(selectedRace.expected_return_per_100_yen);
    $('selectedDataCount').textContent = `${coverage.obtained} / ${coverage.total}項目`;
    $('selectedDataDetail').innerHTML = `<strong>${coverage.state_label}</strong><span>${coverage.missing_labels.length ? `未取得：${coverage.missing_labels.join('・')}` : '不足項目はありません'}</span>`;
    $('ticketOdds').textContent = oddsText(selectedRace.current_odds);
    $('stake').value = selectedRace.max_stake_yen || risk.minimum_stake_yen;

    $('betOption').innerHTML = selectedRace.available_bet_options.length
      ? selectedRace.available_bet_options.map(option => `<option value="${option.bet_type_code}|${option.combination}">${option.bet_type_label} ${option.combination}</option>`).join('')
      : '<option value="">買い目なし</option>';

    $('selectedReasons').innerHTML = selectedRace.decision_reasons.map(reason => `<div class="reason-item ${reason.tone}"><strong>${reason.title}</strong><span>${reason.detail}</span></div>`).join('');
    $('chartLabel').textContent = `${selectedRace.recommended_bet.bet_type_label} ${selectedRace.recommended_bet.combination}`;
    const labels = ['-20分','-10分','-5分','-2分','現在'];
    $('oddsHistory').innerHTML = selectedRace.odds_history.length
      ? selectedRace.odds_history.map((odds,index) => `<div class="odds-point"><span>${labels[index] || ''}</span><b>${oddsText(odds)}</b></div>`).join('')
      : '<div class="coverage-note"><span>オッズ履歴を取得できていません。</span></div>';
    $('chartCallout').innerHTML = selectedRace.odds_5_minutes_ago != null && selectedRace.current_odds != null
      ? `<strong>5分前からの変化</strong><span>${oddsText(selectedRace.odds_5_minutes_ago)} → ${oddsText(selectedRace.current_odds)}</span>`
      : '<strong>比較不可</strong><span>現在オッズまたは5分前オッズが不足しています。</span>';

    $('selectedAvailabilityList').innerHTML = Object.entries(catalog).map(([code, definition]) => {
      const available = selectedRace.data_availability[code] === true;
      return `<div><span>${definition.label}${definition.critical ? '（重要）' : ''}</span><b class="${available ? 'ok' : 'missing'}">${available ? '取得済み' : '未取得'}</b></div>`;
    }).join('');
    $('coverageText').textContent = `必要 ${coverage.total}項目 / 取得済み ${coverage.obtained}項目 / 未取得 ${coverage.missing_labels.length}項目`;

    document.querySelectorAll('.deadline-card').forEach(card => card.classList.toggle('selected', card.dataset.raceId === selectedRace.race_id));
    $('confirmCheck').checked = false;
    updateTicketAmounts();
  }

  function updateRisk() {
    const remaining = Math.max(0, Number(risk.daily_limit_yen) - spentToday);
    const percent = risk.daily_limit_yen > 0 ? Math.min(100, Math.round(spentToday / risk.daily_limit_yen * 100)) : 0;
    $('spentValue').textContent = yen(spentToday);
    $('remainingValue').textContent = yen(remaining);
    $('riskPercent').textContent = `${percent}%`;
    $('riskProgress').style.width = `${percent}%`;
  }

  function updateTicketAmounts() {
    const stake = Number($('stake').value || 0);
    const odds = Number(selectedRace.current_odds || 0);
    const afterLimit = Number(risk.daily_limit_yen) - spentToday - stake;
    $('estimatedPayout').textContent = odds > 0 ? yen(Math.floor(stake * odds)) : '—';
    $('maxLoss').textContent = stake > 0 ? `−${yen(stake)}` : '—';
    $('afterLimit').textContent = yen(Math.max(0, afterLimit));

    let warning = '';
    if (selectedRace.decision_status === 'skip') warning = 'このレースは見送りです。紙上投票も記録できません。';
    else if (selectedRace.data_coverage.critical_missing_codes.length) warning = `重要データが不足しています：${selectedRace.data_coverage.critical_missing_labels.join('・')}`;
    else if (!selectedOption()) warning = '買い目候補がありません。';
    else if (stake < risk.minimum_stake_yen || stake % risk.stake_unit_yen !== 0) warning = `${yen(risk.minimum_stake_yen)}以上、${yen(risk.stake_unit_yen)}単位で入力してください。`;
    else if (stake > selectedRace.max_stake_yen) warning = `このレースの上限 ${yen(selectedRace.max_stake_yen)} を超えています。`;
    else if (afterLimit < 0) warning = '本日の上限を超えています。';

    $('ticketWarning').hidden = !warning;
    $('ticketWarning').textContent = warning;
    $('paperVote').disabled = Boolean(warning) || !$('confirmCheck').checked || !risk.paper_betting_enabled;
  }

  function selectRace(raceId) {
    const race = races.find(item => item.race_id === raceId);
    if (!race) return;
    selectedRace = race;
    renderTicket();
    if (innerWidth < 720) $('ticketPanel').classList.add('open');
  }

  function showToast(message) {
    $('toast').textContent = message;
    $('toast').classList.add('show');
    setTimeout(() => $('toast').classList.remove('show'), 2200);
  }

  async function savePaperBet() {
    const option = selectedOption();
    const payload = {
      race_id: selectedRace.race_id,
      bet_type_code: option.bet_type_code,
      combination: option.combination,
      stake_yen: Number($('stake').value),
      odds_at_record: selectedRace.current_odds,
    };

    if (!config.paperBetEndpoint) {
      spentToday += payload.stake_yen;
      prependLog(payload, new Date().toISOString());
      return {spent_today_yen: spentToday};
    }

    const response = await fetch(config.paperBetEndpoint, {
      method: 'POST',
      headers: {'Content-Type':'application/json','X-CSRF-Token':config.csrfToken},
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || '保存できませんでした。');
    spentToday = Number(result.spent_today_yen);
    prependLog(payload, result.recorded_at);
    return result;
  }

  function prependLog(payload, recordedAt) {
    const option = selectedOption();
    const log = $('paperLog');
    if (log.classList.contains('empty-log')) { log.className = ''; log.innerHTML = ''; }
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `<strong>${selectedRace.venue_name} ${selectedRace.race_number}R / ${option.bet_type_label} ${option.combination} / ${yen(payload.stake_yen)}</strong><span>${new Date(recordedAt).toLocaleTimeString('ja-JP')}・オッズ ${oddsText(payload.odds_at_record)}・${data.site.model_version}</span>`;
    log.prepend(entry);
  }

  document.querySelectorAll('[data-select-race]').forEach(button => button.addEventListener('click', () => selectRace(button.dataset.selectRace)));
  document.querySelectorAll('[data-filter-status]').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('[data-filter-status]').forEach(item => item.classList.remove('active'));
    button.classList.add('active'); statusFilter = button.dataset.filterStatus; filterRows();
  }));
  document.querySelectorAll('[data-filter-venue]').forEach(card => card.addEventListener('click', () => {
    venueFilter = venueFilter === card.dataset.filterVenue ? 'all' : card.dataset.filterVenue;
    $('venueFilter').value = venueFilter;
    document.querySelectorAll('[data-filter-venue]').forEach(item => item.classList.toggle('active', item.dataset.filterVenue === venueFilter));
    filterRows();
  }));
  $('venueFilter').addEventListener('change', () => { venueFilter = $('venueFilter').value; filterRows(); });
  $('raceSearch').addEventListener('input', filterRows);
  $('stake').addEventListener('input', updateTicketAmounts);
  $('betOption').addEventListener('change', updateTicketAmounts);
  $('confirmCheck').addEventListener('change', updateTicketAmounts);
  document.querySelectorAll('[data-amount]').forEach(button => button.addEventListener('click', () => { $('stake').value = button.dataset.amount; updateTicketAmounts(); }));
  $('clearTicket').addEventListener('click', () => { $('stake').value = risk.minimum_stake_yen; $('confirmCheck').checked = false; updateTicketAmounts(); });
  $('ticketPanel').querySelector('.ticket-heading').addEventListener('click', () => { if (innerWidth < 720) $('ticketPanel').classList.toggle('open'); });
  $('paperVote').addEventListener('click', () => {
    const option = selectedOption();
    $('modalSummary').innerHTML = `<div><span>会場・レース</span><strong>${selectedRace.venue_name} ${selectedRace.race_number}R</strong></div><div><span>締切</span><strong>${new Date(selectedRace.scheduled_deadline_at).toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit'})}</strong></div><div><span>買い目</span><strong>${option.bet_type_label} ${option.combination}</strong></div><div><span>金額</span><strong>${yen(Number($('stake').value))}</strong></div><div><span>記録時のオッズ</span><strong>${oddsText(selectedRace.current_odds)}</strong></div><div><span>必要データ</span><strong>${selectedRace.data_coverage.obtained}/${selectedRace.data_coverage.total}</strong></div>`;
    $('confirmModal').hidden = false;
  });
  $('cancelVote').addEventListener('click', () => $('confirmModal').hidden = true);
  $('confirmVote').addEventListener('click', async () => {
    $('confirmVote').disabled = true;
    try {
      await savePaperBet();
      updateRisk();
      $('confirmModal').hidden = true;
      $('confirmCheck').checked = false;
      updateTicketAmounts();
      showToast('紙上投票を記録しました');
    } catch (error) {
      showToast(error.message);
    } finally {
      $('confirmVote').disabled = false;
    }
  });

  updateRisk();
  filterRows();
  renderTicket();
  updateCountdowns();
  setInterval(updateCountdowns, 1000);
})();
