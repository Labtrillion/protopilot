import { SCORE_WEIGHTS, bestImprovement, evaluateBranch, scoreBranch } from './simulation-score.js';
import { byId, elapsed, escape, reading } from './ui.js';

const RESULTS_HASH = '#simulation-results';
const formatNumber = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 });

function quantity(value, unit) {
  return Number.isFinite(value) ? `${reading(value)}${unit}` : '—';
}

function relativeMetric(label, delta, unit, higher, lower) {
  const difference = Math.abs(delta);
  const trend = difference < 0.005 ? 'same' : delta > 0 ? 'better' : 'worse';
  const change = trend === 'same' ? '같음' : `${formatNumber.format(difference)} ${unit} ${trend === 'better' ? higher : lower}`;
  return `<li>${label} <strong data-trend="${trend}">${change}</strong></li>`;
}

function comparison(entry, reference) {
  if (!reference) return '<p class="simulation-note">비교할 다른 대응안의 결과가 없습니다.</p>';
  const { rating, branch } = entry;
  const tripped = Boolean(branch.shutdown), referenceTripped = Boolean(reference.branch.shutdown);
  const tripDifference = tripped === referenceTripped ? ''
    : `<li>보호 정지 <strong data-trend="${tripped ? 'worse' : 'better'}">${tripped ? '이 대응에서만 발생' : '비교 대상에서만 발생'}</strong></li>`;
  const recovered = Number.isFinite(branch.alarm_recovery_minutes);
  const referenceRecovered = Number.isFinite(reference.branch.alarm_recovery_minutes);
  const recoveryDifference = recovered && referenceRecovered
    ? relativeMetric('알람 범위 복귀', reference.branch.alarm_recovery_minutes - branch.alarm_recovery_minutes, '분', '더 빠름', '더 늦음')
    : recovered !== referenceRecovered
      ? `<li>알람 범위 복귀 <strong data-trend="${recovered ? 'better' : 'worse'}">${recovered ? '이 대응에서만 복귀' : '비교 대상에서만 복귀'}</strong></li>` : '';
  return `<div class="simulation-comparison">
    <p>${escape(reference.branch.label)} 대비</p>
    <ul>
      ${relativeMetric('점수', rating.score - reference.rating.score, '점', '높음', '낮음')}
      ${tripDifference}
      ${recoveryDifference}
      ${relativeMetric('범위 내 운전', rating.safeMinutes - reference.rating.safeMinutes, '분', '더 김', '더 짧음')}
      ${relativeMetric('누적 생산량', rating.volume - reference.rating.volume, 'm³', '더 많음', '더 적음')}
    </ul>
  </div>`;
}

function resultDetails(entry, reference, { bestLabel = '', finalWindowHours, heading = 'h3' } = {}) {
  const { rating, branch, issue } = entry;
  const state = branch.shutdown ? 'trip' : rating?.completed ? 'complete' : 'early';
  const outcome = branch.shutdown ? '보호 정지' : rating ? (rating.completed ? '완료' : '중도 종료') : '—';
  const recovery = branch.alarm_recovery_minutes === 0 ? '시작부터 범위 내'
    : Number.isFinite(branch.alarm_recovery_minutes) ? `${elapsed(branch.alarm_recovery_minutes / 60)} 후`
      : '미복귀';
  const action = branch.id === 'hold' ? '해당 없음' : branch.action_applied ? '적용' : '미적용';
  const shortRecovery = finalWindowHours && branch.alarm_final_window_inside === false
    && Number.isFinite(branch.alarm_recovery_minutes);
  const recoveryNote = shortRecovery
    ? `<p class="simulation-note">종료 전 정상 범위 유지 ${elapsed(Math.max(0, branch.window_hours - branch.alarm_recovery_minutes / 60))} · 검토 기준 ${elapsed(finalWindowHours)}</p>` : '';
  return `<div class="simulation-card-heading">
      <${heading}>${escape(branch.label)}</${heading}>
      ${bestLabel ? `<span class="simulation-best">${bestLabel}</span>` : ''}
    </div>
    ${rating ? `<div class="simulation-score"><strong>${formatNumber.format(rating.score)}</strong><span>/ 100점</span></div>`
      : '<p class="simulation-unavailable">점수 산정 불가</p>'}
    ${branch.error ? `<p class="simulation-note">${escape(branch.error)}</p>` : `
      ${issue && rating ? `<p class="simulation-unavailable">${issue}</p>` : ''}
      <dl class="simulation-metrics">
        <div><dt>운전 결과</dt><dd data-outcome="${state}">${outcome}</dd></div>
        <div><dt>실제 운전 시간</dt><dd>${Number.isFinite(branch.window_hours) ? elapsed(branch.window_hours) : '—'}</dd></div>
        ${Object.hasOwn(branch, 'action_applied') ? `<div><dt>감량 적용</dt><dd>${action}</dd></div>` : ''}
        ${Object.hasOwn(branch, 'alarm_recovery_minutes') ? `<div><dt>알람 범위 복귀</dt><dd>${recovery}</dd></div>` : ''}
        ${Object.hasOwn(branch, 'final_window_inside') ? `<div><dt>종료 구간 운전 범위</dt><dd>${branch.final_window_inside ? '충족' : '미충족'}</dd></div>` : ''}
        ${Object.hasOwn(branch, 'alarm_final_window_inside') ? `<div><dt>종료 구간 알람 기준</dt><dd>${branch.alarm_final_window_inside ? '충족' : '미충족'}</dd></div>` : ''}
        <div><dt>운전 범위 이탈</dt><dd>${quantity(branch.violation_minutes, '분')}</dd></div>
        <div><dt>자동 보호 개입</dt><dd>${quantity(branch.protection_minutes, '분')}</dd></div>
        <div><dt>반응기 최고 압력</dt><dd>${quantity(branch.peak_pressure, ' kPa(g)')}</dd></div>
        <div><dt>반응기 최고 온도</dt><dd>${quantity(branch.peak_temperature, ' °C')}</dd></div>
        <div><dt>온도 표준편차</dt><dd>${quantity(branch.temperature_sd, ' °C')}</dd></div>
        <div><dt>분리기 최고 액위</dt><dd>${quantity(branch.peak_separator_level, '%')}</dd></div>
        <div><dt>계획 생산 목표</dt><dd>${quantity(branch.production_target, ' m³/h')}</dd></div>
        <div><dt>평균 제품 유량</dt><dd>${quantity(branch.mean_production, ' m³/h')}</dd></div>
        <div><dt>누적 생산량</dt><dd>${quantity(rating?.volume, ' m³')}</dd></div>
      </dl>
      ${recoveryNote}
      ${branch.shutdown?.message ? `<p class="simulation-note">정지 사유 · ${escape(branch.shutdown.message)}</p>` : ''}
      ${rating ? comparison(entry, reference) : '<p class="simulation-note">점수에 필요한 시뮬레이션 지표가 없습니다.</p>'}`}`;
}

function renderLegacyCards(data) {
  const ranked = data.branches.map((branch) => ({ branch, rating: scoreBranch(branch, data.start) }))
    .sort((a, b) => (b.rating?.score ?? -1) - (a.rating?.score ?? -1));
  const scored = ranked.filter(({ rating }) => rating);
  const baseline = scored.find(({ branch }) => branch.id === 'hold');
  const bestScore = scored[0]?.rating.score;
  const bestCount = scored.filter(({ rating }) => rating.score === bestScore).length;
  byId('simulation-cards').innerHTML = ranked.map((entry) => {
    const reference = baseline && baseline !== entry ? baseline : scored.find((option) => option !== entry);
    const bestLabel = bestCount && entry.rating?.score === bestScore ? (bestCount > 1 ? '공동 최고 점수' : '최고 점수') : '';
    return `<li class="simulation-card"${bestLabel ? ' data-best' : ''}>${resultDetails(entry, reference, { bestLabel })}</li>`;
  }).join('');
}

function renderSweep(data) {
  const { sweep, start } = data;
  const baseline = evaluateBranch(sweep.baseline, start);
  const candidates = sweep.branches.map((branch) => evaluateBranch(branch, start));
  const improvement = bestImprovement(baseline, candidates);
  const eligible = candidates.filter(({ issue }) => !issue).length;
  const bestAlternative = candidates.filter(({ rating }) => rating)
    .sort((a, b) => Number(Boolean(a.issue)) - Number(Boolean(b.issue)) || b.rating.score - a.rating.score)[0];
  const verdict = improvement ? '개선안 검토' : baseline.issue ? 'SOP 기준안과 대안 재검토' : 'SOP 기준안 유지';
  const reason = improvement ? `${improvement.branch.label} 조합이 ${baseline.issue ? '종료 구간 검토 조건을 충족했습니다.'
    : `SOP 기준안보다 ${formatNumber.format(improvement.rating.score - baseline.rating.score)}점 높습니다.`}`
    : !baseline.rating ? 'SOP 기준안의 점수를 계산하지 못했습니다.'
      : baseline.issue ? `SOP 기준안의 판정은 ‘${baseline.issue}’이며 기준안을 대체할 조합을 찾지 못했습니다.`
        : '종료 구간 검토 조건을 충족하면서 SOP 기준안보다 높은 점수를 얻은 조합이 없습니다.';
  const preferred = data.decision?.response?.label;
  byId('simulation-verdict').innerHTML = `<div><h3>${verdict}</h3><p>${escape(reason)}</p>
    ${preferred ? `<p class="simulation-note">사전 SOP 검토 · ${data.decision.fallback ? '로컬 규칙' : 'Zoro-1'} · ${escape(preferred)}</p>` : ''}</div>
    <dl><div><dt>SOP 기준 점수</dt><dd>${baseline.rating ? `${formatNumber.format(baseline.rating.score)}점` : '산정 불가'}</dd></div>
    <div><dt>검토 조건 충족</dt><dd>${eligible} / ${candidates.length}개</dd></div></dl>`;

  const { final_window_hours: finalWindowHours } = sweep;
  byId('simulation-baseline-card').innerHTML = resultDetails(baseline, improvement ?? bestAlternative, { finalWindowHours });
  byId('simulation-sweep-cards').innerHTML = sweep.delay_minutes.map((delay, index) => {
    const row = candidates.filter(({ branch }) => branch.delay_minutes === delay)
      .sort((a, b) => a.branch.reduction_pct - b.branch.reduction_pct);
    const heading = `simulation-delay-${index}`;
    return `<section aria-labelledby="${heading}"><h3 id="${heading}">${delay ? `${delay}분 후 적용` : '즉시 적용'}</h3>
      <ul class="simulation-grid simulation-sweep-grid" role="list">${row.map((entry) => {
        const bestLabel = entry === improvement ? '개선 후보' : '';
        return `<li class="simulation-card"${bestLabel ? ' data-best' : ''}>
          ${resultDetails(entry, baseline.rating ? baseline : null, { bestLabel, finalWindowHours, heading: 'h4' })}</li>`;
      }).join('')}</ul></section>`;
  }).join('');
}

function renderResults(data) {
  byId('simulation-kind').textContent = data.sweep ? 'Function 2 · 프로토콜 개선' : '알람 대응 시뮬레이션';
  byId('simulation-title').textContent = data.alarm.label;
  byId('simulation-context').textContent = [data.alarm.tag, data.alarm.source, `발생 ${elapsed(data.alarm.time_hours)}`,
    `비교 시간 ${elapsed(data.start.horizon_hours)}`].filter(Boolean).join(' · ');
  byId('simulation-sensor-note').hidden = data.alarm.source !== '센서 시험';
  byId('simulation-sweep').hidden = !data.sweep;
  byId('simulation-cards').hidden = Boolean(data.sweep);
  if (data.sweep) renderSweep(data);
  else renderLegacyCards(data);
  const { completion, operatingRange, production } = SCORE_WEIGHTS;
  const gate = data.sweep ? `<li><b>개선안 검토 조건.</b> 보호 정지 없이 비교 시간을 완료하고 마지막 ${elapsed(data.sweep.final_window_hours)} 동안 정상 운전 범위와 해당 알람 기준을 모두 충족해야 합니다. 기준안과 대안이 모두 조건을 충족하면 점수가 더 높은 대안을 검토합니다.</li>` : '';
  byId('simulation-rubric').innerHTML = `${gate}<li><b>비교 시간 완료 ${completion}점.</b> 보호 정지 없이 예정된 비교 시간을 모두 완료하면 받습니다.</li>
    <li><b>운전 범위 ${operatingRange}점.</b> 예정된 비교 시간 중 TEP 모델의 정상 운전 범위에 있었던 시간의 비율로 계산합니다.</li>
    <li><b>생산량 ${production}점.</b> 알람 당시 목표 유량으로 예정 시간 동안 생산했을 양과 실제 누적 생산량의 비율로 계산합니다. 목표량을 넘으면 만점입니다.</li>`;
}

export function mountSimulationResults() {
  const panel = byId('simulation-results');
  const dashboard = byId('console');
  const topbar = byId('dashboard-topbar');
  let opener;

  function syncView() {
    const data = location.hash === RESULTS_HASH ? history.state?.alarmSimulation : null;
    const wasOpen = !panel.hidden;
    if (data) {
      renderResults(data);
      topbar.hidden = dashboard.hidden = true;
      panel.hidden = false;
      panel.scrollTop = 0;
      byId('simulation-title').focus({ preventScroll: true });
    } else if (wasOpen) {
      panel.hidden = true;
      topbar.hidden = dashboard.hidden = false;
      const destination = opener?.isConnected && opener.getClientRects().length ? opener : dashboard;
      destination.focus({ preventScroll: true });
    }
  }

  byId('simulation-back').addEventListener('click', () => history.back());
  window.addEventListener('popstate', syncView);
  syncView();
  return (data) => {
    opener = document.activeElement;
    byId('simulation-baseline').open = false;
    byId('simulation-scoring').open = false;
    history.pushState({ alarmSimulation: data }, '', RESULTS_HASH);
    syncView();
  };
}
