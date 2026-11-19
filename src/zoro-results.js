import { formatZoroShare, softmaxShares } from './simulation-score.js';
import { escape, reading } from './ui.js';

const REASONS = {
  fewer_than_two_eligible_setpoints: '변경이 필요한 설정값 두 개를 확정하지 못했습니다.',
  no_eligible_setpoints: '변경 기준을 충족한 설정값이 없습니다.',
  single_change_failed_simulation: '단일 변경안이 시뮬레이션 운전 기준을 통과하지 못했습니다.',
  no_better_preview: '유지보다 유리한 변경안을 찾지 못했습니다.',
  preview_not_confirmed: 'Zoro-1이 시뮬레이션 결과를 바탕으로 변경을 확정하지 못했습니다.',
  rulebook_unavailable: '지정한 SOP 파일에서 판단에 필요한 규칙을 읽지 못했습니다.',
  insufficient_rag_evidence: '알람을 판단할 검색 근거가 부족합니다.',
  rag_error: '알람 근거 검색에 실패했습니다.',
  not_configured: 'Zoro-1 서버가 연결되지 않았습니다.',
  no_alternatives: '허용 범위 안에서 조정할 설정값 두 개를 찾지 못했습니다.',
  zero_scores: 'Zoro-1이 추가 조합을 선별하지 않았습니다.',
  score_unavailable: 'Zoro-1의 후보 채점 응답을 받지 못했습니다.',
  baseline_unavailable: '기준안의 비교 지표를 계산하지 못했습니다.',
  no_eligible_branch: '운전 조건을 충족하는 비교안이 없습니다.',
  feature1_unavailable: '피처 1 기준안을 확정하지 못해 후속 비교를 시작하지 않았습니다.',
  feature1_pair_unavailable: '피처 1 기준안을 확정하지 못해 후속 비교를 시작하지 않았습니다.',
};
const GATES = {
  trip: '보호 정지', no_trip: '보호 정지',
  final_window_inside: '종료 구간 추적 변수 허용 범위', production_vs_baseline: '기준안 대비 생산량', no_production: '생산량 없음',
  quality_unavailable: '품질 지표 누락', incomplete_horizon: '비교 시간 미완료',
  quality: '제품 품질', operating_limit: '운전 한계 위반', warning_benefit: '경고 시간 개선 폭',
  warning_exposure: '경고 시간 증가', cost_per_volume: '단위 생산 비용',
  recovery_production: '복원 생산량 증가 폭', simulation_failed: '분기 계산 실패',
  baseline_unavailable: '유지 분기 지표 누락',
};
const BRANCH_NAMES = { hold: '현재 목표 유지', rule: '로컬 규칙', zoro: 'Zoro-1 기준안', feature1: 'Zoro-1 기준안' };
const RESPONSE_SCORE_TEMPERATURE = 0.15;
const movePercent = new Intl.NumberFormat('ko-KR', { style: 'percent', signDisplay: 'exceptZero', maximumFractionDigits: 2 });
const quantity = (value, unit = '') => Number.isFinite(value) ? reading(value) + unit : '—';

function reasonText(reason) {
  return REASONS[reason?.split(':')[0]] ?? (reason ? 'Zoro-1 판단을 완료하지 못했습니다.' : '');
}

export function feature2Verdict(feature2 = {}) {
  if (feature2.recommended) return feature2.status === 'improved' ? '개선안 권고' : '기준안 유지';
  if (feature2.status === 'skipped' || feature2.reason?.startsWith('feature1_')) return '후속 비교 미실행';
  return feature2.status ? '권고안 미확정' : '후속 비교 결과 없음';
}

export function zoroVerdict(data) {
  if (data.feature2?.recommended) return `피처 2 ${feature2Verdict(data.feature2)}`;
  return `피처 1 ${feature1Outcome(data).title}`;
}

function feature2Plans({ baseline, evaluated = [] }) {
  const baselineId = baseline?.id ?? 'feature1';
  const scoredBaseline = evaluated.find(({ id }) => id === baselineId) ?? baseline;
  return [scoredBaseline, ...evaluated.filter(({ id }) => id !== baselineId)].filter(Boolean);
}

function changesList(changes, setpoints) {
  if (!Array.isArray(changes)) return '<p>설정값 변경 정보 없음</p>';
  return changes.length ? `<ul class="zoro-changes">${changes.map(({ setpoint, from, to, unit }) => {
    const spec = setpoints[setpoint];
    return `<li><span>${escape(spec?.label ?? setpoint)}</span><strong>${quantity(from)} → ${quantity(to)} ${escape(unit ?? spec?.unit ?? '')}</strong></li>`;
  }).join('')}</ul>` : '<p>현재 설정값 유지</p>';
}

function moveLabels(branch, setpoints) {
  return branch.moves?.map(({ setpoint, move }) => {
    const rate = Number.isFinite(move) ? movePercent.format(move) : '조정';
    return `${escape(setpoints[setpoint]?.label ?? setpoint)} ${move === 0 ? '유지' : rate}`;
  }).join(' · ');
}

export function renderZoroSummary(data) {
  const { decision, feature2 = {}, setpoints, evidence = [] } = data;
  const selection = feature2.selection;
  const detail = reasonText(feature2.reason) || reasonText(selection?.reason);
  const outcome = feature1Outcome(data);
  const scored = Object.keys(selection?.scores ?? {}).length;
  const selected = selection?.selected_setpoints ?? [];
  const plans = feature2Plans(feature2);
  const shares = responseShares(feature2, plans);
  const scoredPlans = plans.filter(({ id }) => Number.isFinite(shares[id]));
  const hasResponseScores = plans.some(({ id }) => Number.isFinite(feature2.response_scores?.[id]));
  const scoredItems = Array.isArray(selection?.selected_setpoints) ? '설정값' : '후보';
  return `<section class="zoro-summary">
    <div class="zoro-feature1-summary">
      <h3>피처 1 · ${outcome.title}</h3>
      ${outcome.detail ? `<p>${outcome.detail}</p>` : ''}
      ${outcome.notice ? `<p class="simulation-unavailable">${outcome.notice}</p>` : ''}
      <p class="study-note">${decision.gate ? 'Zoro-1 판단 응답 수신' : 'Zoro-1 판단 응답 없음'} · 알람 근거 ${evidence.length}건</p>
    </div>
    <h3>피처 2 · ${feature2Verdict(feature2)}</h3>
    ${detail ? `<p>${detail}</p>` : ''}
    ${feature2.recommended ? changesList(feature2.recommended.changes, setpoints) : ''}
    ${selected.length ? `<p class="study-note">선정 설정값 · ${selected.map((key) => escape(setpoints[key]?.label ?? key)).join(' · ')}</p>` : ''}
    ${feature2.response_score_reason ? '<p class="study-note">Zoro-1 대응안 점수 응답을 받지 못했습니다.</p>' : ''}
    ${hasResponseScores ? '<p class="study-note">적합도는 Zoro-1의 상대 점수입니다.</p>' : ''}
    ${scoredPlans.length ? `<p class="study-note">Zoro-1 상대 적합도</p><ul class="zoro-changes">${scoredPlans.map((plan, index) =>
      `<li><span>${BRANCH_NAMES[plan.id] ?? (moveLabels(plan, setpoints) || escape(plan.label ?? `대안 ${index + 1}`))}</span><strong>${formatZoroShare(shares[plan.id])}</strong></li>`
    ).join('')}</ul>` : ''}
    ${plans.length || scored || selection?.choice_error ? `<p class="study-note">${scoredItems} ${scored}개 채점 · 대응안 ${plans.length}개 비교${selection?.choice_error ? ' · 우선순위 응답 실패' : ''}</p>` : ''}
  </section>`;
}

function renderBranch(branch, data, index, { share } = {}) {
  const selection = data.feature2.selection;
  const recommended = data.feature2.recommended?.id === branch.id;
  const moves = moveLabels(branch, data.setpoints);
  const name = BRANCH_NAMES[branch.id] ?? (moves || escape(branch.label ?? '대안'));
  return `<li class="simulation-card"${recommended ? ' data-best' : ''}>
    <div class="simulation-card-heading"><h3>${index}. ${name}</h3>
      ${recommended ? '<span class="simulation-best">권고안</span>' : ''}
      ${selection?.first_choice === branch.id ? '<span class="simulation-best">Zoro-1 우선 검토 (choice)</span>' : ''}</div>
    ${moves && branch.id === 'feature1' ? `<p class="study-note">${moves}</p>` : ''}
    ${branch.error ? '<p class="simulation-unavailable">분기 계산에 실패했습니다.</p>' : ''}
    ${changesList(branch.changes, data.setpoints)}
    <div class="simulation-score"><span>Zoro-1 상대 적합도</span><strong>${formatZoroShare(share)}</strong></div>
    ${branch.error ? '' : `<dl class="simulation-metrics">
      <div><dt>단위 생산 비용</dt><dd>${quantity(branch.cost_per_m3, ' USD/m³')}</dd></div>
      <div><dt>추종 정확도</dt><dd>${quantity(branch.tracking_accuracy)}</dd></div>
      <div><dt>누적 생산량</dt><dd>${quantity(branch.produced_m3, ' m³')}</dd></div>
      <div><dt>목표 대비 생산 손실</dt><dd>${quantity(branch.production_loss_m3, ' m³')}</dd></div>
    </dl>`}
  </li>`;
}

function responseShares(feature2, plans) {
  const scores = Object.fromEntries(plans.map(({ id }) => [id, feature2.response_scores?.[id]]));
  return softmaxShares(scores, RESPONSE_SCORE_TEMPERATURE);
}

export function renderZoroCards(data) {
  const feature2 = data.feature2 ?? {};
  const plans = feature2Plans(feature2);
  const shares = responseShares(feature2, plans);
  return plans.map((branch, index) => renderBranch(branch, data, index + 1, { share: shares[branch.id] })).join('');
}

const FEATURE1_METRICS = [
  ['cost_per_m3', '단위 생산 비용', 'USD/m³'],
  ['produced_m3', '누적 생산량', 'm³'],
  ['production_loss_m3', '목표 대비 생산 손실', 'm³'],
  ['tracking_accuracy', '추종 정확도', ''],
  ['alarm_minutes', '누적 경고 시간', '분'],
  ['violation_minutes', '운전 한계 위반 시간', '분'],
];
function branchIssues(branch, horizonHours) {
  const failures = new Set(branch.failed_gates?.map((gate) => GATES[gate] ?? gate));
  if (branch.shutdown) failures.add(GATES.trip);
  if (branch.quality_ok === false) failures.add(GATES.quality);
  if (Number.isFinite(branch.window_hours) && branch.window_hours + 0.000001 < horizonHours && !branch.shutdown) {
    failures.add(GATES.incomplete_horizon);
  }
  return [...failures];
}

function feature1Status(branch, horizonHours) {
  if (branch.error) return '<strong data-state="error">계산 실패</strong>';
  const issues = branchIssues(branch, horizonHours);
  if (issues.length) return `<strong data-state="error">미충족 · ${escape(issues.join(' · '))}</strong>`;
  if (branch.violation_minutes > 0) return '<strong data-state="warning">운전 한계 위반 발생</strong>';
  return FEATURE1_METRICS.some(([key]) => Number.isFinite(branch[key])) ? '비교 완료' : '비교 지표 없음';
}

function feature1Outcome({ branches = [], decision, start }) {
  const zoro = branches.find(({ id }) => id === 'zoro');
  const changes = decision.changes ?? zoro?.changes;
  const noZoroChanges = decision.status === 'ready' && Array.isArray(changes) && changes.length === 0;
  const keepsTargets = decision.status === 'hold' || noZoroChanges;
  const selectedId = keepsTargets ? 'hold' : decision.status === 'ready' ? 'zoro' : null;
  const selected = branches.find(({ id }) => id === selectedId);
  const issues = selected && branchIssues(selected, start?.horizon_hours);
  let notice = '';
  if (selectedId && (!selected || selected.error)) {
    notice = '선택한 안의 시뮬레이션 결과를 확인하지 못했습니다.';
  } else if (issues?.length) {
    notice = `선택한 안의 시뮬레이션 검토 조건 미충족 · ${escape(issues.join(' · '))}`;
  } else if (selected?.violation_minutes > 0) {
    notice = '선택한 안에서 운전 한계 위반이 발생했습니다.';
  }
  const title = notice ? '선택한 안 재검토 필요'
    : keepsTargets ? '현재 설정값 유지'
      : decision.status === 'ready' ? 'Zoro-1 기준안 선택' : '운영자 검토 필요';
  const detail = noZoroChanges ? 'Zoro-1이 변경 대상으로 선택한 설정값이 없습니다.' : reasonText(decision.reason);
  return { title, selected, detail, notice };
}

export function renderFeature1Comparison(data) {
  const { branches = [], start, setpoints } = data;
  const hold = branches.find(({ id }) => id === 'hold');
  const { title, selected, detail, notice } = feature1Outcome(data);
  const plans = [hold, selected !== hold ? selected : null, ...branches.filter((plan) =>
    plan !== hold && plan !== selected,
  )].filter(Boolean);
  const usesHoldTargets = (plan) => hold?.changes?.length === 0 && plan !== hold && plan.changes?.length === 0;
  const allUseHoldTargets = plans.length > 1 && plans.every((plan) => !plan.error && (plan === hold || usesHoldTargets(plan)));
  const metrics = FEATURE1_METRICS.filter(([key]) => plans.some((plan) => !plan.error && Number.isFinite(plan[key])));
  const row = (label, render, className = '') => `<tr class="${className}"><th scope="row">${label}</th>${plans.map((plan) =>
    `<td${plan === selected ? ' data-selected' : ''}>${render(plan)}</td>`
  ).join('')}</tr>`;

  return `<div class="feature1-verdict">
    <h3>${title}</h3>${detail ? `<p>${detail}</p>` : ''}
    ${notice ? `<p class="simulation-unavailable">${notice}</p>` : ''}
    ${allUseHoldTargets ? '<p>모든 비교안을 같은 시작 상태와 설정값으로 실행했습니다.</p>' : ''}
  </div>
  ${plans.length > 1 ? '<p class="feature1-scroll-hint">표를 좌우로 밀어 다른 비교안을 확인하세요.</p>' : ''}
  ${plans.length ? `<div class="feature1-table-wrap" tabindex="0" role="region" aria-labelledby="feature1-comparison-title">
    <table class="feature1-table">
      <thead><tr><th scope="col">비교 항목</th>${plans.map((plan, index) => {
        const name = plan.id === 'zoro' && selected === hold && usesHoldTargets(plan) ? 'Zoro-1 판단'
          : plan.id === 'zoro' && plan !== selected ? 'Zoro-1 비교 후보'
            : BRANCH_NAMES[plan.id] ?? plan.label ?? `비교안 ${index + 1}`;
        return `<th scope="col"${plan === selected ? ' data-selected' : ''}>${escape(name)}
          ${plan === selected ? '<span class="simulation-best">피처 1 선택</span>' : ''}</th>`;
      }).join('')}</tr></thead>
      <tbody>
        ${row('설정값 변경', (plan) => {
          if (usesHoldTargets(plan)) return '<strong class="feature1-same-targets">유지안과 동일</strong>';
          return Array.isArray(plan.changes) ? changesList(plan.changes, setpoints) : '—';
        })}
        ${row('시뮬레이션 상태', (plan) => feature1Status(plan, start?.horizon_hours))}
        ${metrics.map(([key, label, unit]) => row(`${label}${unit ? `<small>${unit}</small>` : ''}`, (plan) => {
          const value = plan.error ? NaN : plan[key];
          const difference = plan !== hold && !hold?.error && Number.isFinite(hold?.[key]) ? value - hold[key] : NaN;
          return `<strong>${reading(value, unit)}</strong>${Number.isFinite(difference) && !(usesHoldTargets(plan) && difference === 0)
            ? `<small>유지 대비 ${difference > 0 ? '+' : ''}${reading(difference, unit)}</small>` : ''}`;
        }, 'feature1-metric')).join('')}
      </tbody>
    </table>
  </div>` : '<p class="simulation-note">비교할 시뮬레이션 결과가 없습니다.</p>'}`;
}

export function renderZoroEvidence({ decision, evidence = [], setpoints, decision_id: decisionId, feature2 = {} }) {
  const labels = { change_supported: '비교 결과가 변경을 지지함', temperature_sensor_check: '온도 센서 재확인 근거' };
  const gate = Object.entries(decision.gate ?? {}).filter(([, value]) => Number.isFinite(value)).map(([key, value]) =>
    `<li>${escape(labels[key] ?? setpoints[key]?.label ?? key)} · ${reading(value * 100)}%${decision.moves?.[key] ? ' · 변경 선택' : ''}</li>`).join('');
  const unmetConditions = feature2Plans(feature2).map((plan, index) => {
    const issues = branchIssues(plan);
    if (!issues.length) return '';
    const name = plan.id === 'feature1' ? '피처 1 기준안' : `대응안 ${index + 1}`;
    return `<li>${name} · ${escape(issues.join(' · '))}</li>`;
  }).join('');
  return `${gate ? `<li><b>Zoro-1 판단 점수 (noul)</b><ul>${gate}</ul></li>` : ''}
    ${unmetConditions ? `<li><b>피처 2 운전 조건 미충족</b><ul>${unmetConditions}</ul></li>` : ''}
    ${evidence.map(({ source_document: source, statement }) => `<li><b>${escape(source ?? '')}</b> ${escape(statement)}</li>`).join('')}
    <li>실행 ID <code>${escape(decisionId)}</code></li>`;
}
