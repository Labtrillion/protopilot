import { escape, reading } from './ui.js';

const REASONS = {
  fewer_than_two_eligible_setpoints: '변경이 필요한 설정값 두 개를 확정하지 못했습니다.',
  insufficient_rag_evidence: '알람을 판단할 검색 근거가 부족합니다.',
  rag_error: '알람 근거 검색에 실패했습니다.',
  not_configured: 'Zoro-1 서버가 연결되지 않았습니다.',
  no_alternatives: '허용 범위 안에 추가 조합이 없습니다.',
  zero_scores: 'Zoro-1이 추가 조합을 선별하지 않았습니다.',
  score_unavailable: 'Zoro-1의 후보 채점 응답을 받지 못했습니다.',
  baseline_unavailable: '기준안의 비교 지표를 계산하지 못했습니다.',
  no_eligible_branch: '운전 조건을 충족하는 비교안이 없습니다.',
};
const GATES = {
  trip: '보호 정지', production_vs_baseline: '기준안 대비 생산량', no_production: '생산량 없음',
  quality_unavailable: '품질 지표 누락', incomplete_horizon: '비교 시간 미완료',
};
const BRANCH_NAMES = { hold: '현재 목표 유지', rule: '로컬 규칙', zoro: 'Zoro-1 기준안', feature1: 'Zoro-1 기준안' };
const quantity = (value, unit = '') => Number.isFinite(value) ? reading(value) + unit : '—';

function reasonText(reason) {
  return REASONS[reason?.split(':')[0]] ?? (reason ? 'Zoro-1 판단을 완료하지 못했습니다.' : '');
}

export function zoroVerdict({ decision, feature2 }) {
  if (feature2.recommended) return feature2.status === 'improved' ? '개선안 권고' : '기준안 유지';
  return decision.status === 'ready' ? '조건을 충족한 권고안 없음' : '운영자 검토 필요';
}

function changesList(changes, setpoints) {
  return changes.length ? `<ul class="zoro-changes">${changes.map(({ setpoint, from, to, unit }) => {
    const spec = setpoints[setpoint];
    return `<li><span>${escape(spec?.label ?? setpoint)}</span><strong>${quantity(from)} → ${quantity(to)} ${escape(unit ?? spec?.unit ?? '')}</strong></li>`;
  }).join('')}</ul>` : '<p>현재 설정값 유지</p>';
}

export function renderZoroSummary(data) {
  const { decision, feature2, setpoints, evidence } = data;
  const selection = feature2.selection;
  const detail = reasonText(decision.reason) || reasonText(feature2.reason) || reasonText(selection?.reason);
  const scored = Object.keys(selection?.scores ?? {}).length;
  return `<section class="zoro-summary">
    <h3>${zoroVerdict(data)}</h3>
    <p class="study-note">${decision.gate ? '실제 Zoro-1 응답' : 'Zoro-1 응답 없음'} · 알람 근거 ${evidence.length}건</p>
    ${detail ? `<p>${detail}</p>` : ''}
    ${feature2.recommended ? changesList(feature2.recommended.changes, setpoints) : ''}
    <p class="study-note">후보 ${scored}개 채점 · 대안 ${feature2.evaluated.length}개 시뮬레이션${selection?.choice_error ? ' · 우선순위 응답 실패' : ''}</p>
  </section>`;
}

function renderBranch(branch, data, index, { legacy = false } = {}) {
  const selection = data.feature2.selection;
  const score = selection?.scores[branch.id]?.score;
  const recommended = data.feature2.recommended?.id === branch.id;
  const name = BRANCH_NAMES[branch.id] ?? `대안 ${index}`;
  const gates = branch.failed_gates?.map((gate) => GATES[gate] ?? gate);
  return `<li class="simulation-card"${recommended ? ' data-best' : ''}>
    <div class="simulation-card-heading"><h3>${name}</h3>
      ${recommended ? '<span class="simulation-best">권고안</span>' : ''}
      ${selection?.first_choice === branch.id ? '<span class="simulation-best">Zoro-1 우선 검토 (choice)</span>' : ''}</div>
    ${changesList(branch.changes, data.setpoints)}
    ${branch.error ? '<p class="simulation-unavailable">분기 계산에 실패했습니다.</p>' : `<dl class="simulation-metrics">
      <div><dt>단위 생산 비용</dt><dd>${quantity(branch.cost_per_m3, ' USD/m³')}</dd></div>
      <div><dt>추종 정확도</dt><dd>${quantity(branch.tracking_accuracy)}</dd></div>
      <div><dt>누적 생산량</dt><dd>${quantity(branch.produced_m3, ' m³')}</dd></div>
      <div><dt>목표 대비 생산 손실</dt><dd>${quantity(branch.production_loss_m3, ' m³')}</dd></div>
      ${Number.isFinite(score) ? `<div><dt>Zoro-1 적합도 (score)</dt><dd>${quantity(score)} / 2</dd></div>` : ''}
    </dl>
    ${gates?.length ? `<p class="simulation-unavailable">미충족 · ${escape(gates.join(' · '))}</p>` : ''}
    ${legacy && branch.shutdown ? '<p class="simulation-unavailable">보호 정지</p>' : ''}`}
  </li>`;
}

export function renderZoroCards(data) {
  const { baseline, evaluated } = data.feature2;
  const comparisons = data.branches.filter(({ id }) => id !== 'zoro' || !baseline);
  return comparisons.map((branch, index) => renderBranch(branch, data, index, { legacy: true })).join('')
    + (baseline ? renderBranch(baseline, data, 0) : '')
    + evaluated.map((branch, index) => renderBranch(branch, data, index + 1)).join('');
}

export function renderZoroEvidence({ decision, evidence, setpoints, decision_id: decisionId }) {
  const gate = Object.entries(decision.gate ?? {}).map(([key, value]) =>
    `<li>${escape(setpoints[key]?.label ?? key)} · ${Math.round(value * 100)}%${decision.moves[key] ? ' · 변경 선택' : ''}</li>`).join('');
  return `${gate ? `<li><b>변경 필요성 (noul)</b><ul>${gate}</ul></li>` : ''}
    ${evidence.map(({ source_document: source, statement }) => `<li><b>${escape(source ?? '')}</b> ${escape(statement)}</li>`).join('')}
    <li>실행 ID <code>${escape(decisionId)}</code></li>`;
}
