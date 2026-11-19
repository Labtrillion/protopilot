import { byId, elapsed, escape } from './ui.js';

const OUTCOMES = { recommendation: '권고', human_review: '운영자 검토' };
const REASONS = {
  recommendation_ready: '권고 조건 충족', human_review_selected: '모델이 운영자 검토 선택', limit_reached: '시험 한도 도달',
  unrecoverable_tool_error: '시뮬레이션 오류', no_valid_options: '유효한 선택지 없음',
  model_response_invalid: '모델 응답 거부', model_call_failed: 'Zoro-1 호출 실패',
};
const SELECTION = {
  select_case: '대응 case 선정', select_baseline: '현재 목표 유지(SOP 단독) 선정',
  none_acceptable: '적격 case 없음', continue: '탐색 중',
};
const fixed = (value, digits = 3) => (Number.isFinite(value) ? Number(value).toFixed(digits) : '—');

function renderCommand(command) {
  const changes = command.setpoint_changes.map((change) => {
    const delay = change.apply_at_s ? ` · ${Math.round(change.apply_at_s / 60)}분 후` : ' · 즉시';
    return `<li><code>${escape(change.setpoint_id)}</code> ${fixed(change.from_value, 3)} → <b>${fixed(change.target_value, 3)}</b> ${escape(change.unit)}${delay}</li>`;
  }).join('');
  return `<section><h3>시뮬레이터 명령</h3>
    <p><b>${command.decision === 'hold' ? '현재 목표 유지 (hold)' : '설정값 변경 제안'}</b> · case <code>${escape(command.case_id ?? '—')}</code></p>
    ${changes ? `<ul>${changes}</ul>` : ''}
    <p class="study-note">감독기 승인 후 적용 · 값은 예시 설정</p></section>`;
}

function renderBranches(data) {
  const state = data.final_state;
  const impacts = new Map(state.impacts.map((impact) => [impact.run_id, impact]));
  const steps = new Map((data.final.case_selection?.steps ?? []).map((step) => [step.run_id, step]));
  const rows = state.runs.map((run) => {
    const impact = impacts.get(run.run_id);
    const score = impact?.score;
    const step = steps.get(run.run_id);
    const verdict = step ? `${step.beats_baseline ? '기준 대비 우세' : '기준 대비 열세'} · ${escape(step.vs_baseline)}` : '기준(SOP 단독)';
    return `<tr><td><code>${escape(run.case_id ?? run.action_id)}</code></td>
      <td>${impact?.original_alarm_recovered ? '회복' : '미회복'}</td>
      <td>${fixed(score?.tracking_accuracy)}</td><td>${fixed(score?.usd_per_m3)}</td>
      <td>${fixed(score?.product_m3, 1)}</td><td>${escape((score?.failed_gates ?? []).join(', ') || '통과')}</td>
      <td>${verdict}</td></tr>`;
  }).join('');
  const selection = data.final.case_selection;
  return `<section><h3>분기 시뮬레이션 (알람 시점 복제, 가상 예측)</h3>
    <div class="table-scroll"><table class="study-table"><thead><tr><th>case</th><th>알람</th><th>정확도</th><th>USD/m³</th><th>생산 m³</th><th>게이트</th><th>판정</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="7">실행한 분기 없음</td></tr>'}</tbody></table></div>
    ${selection ? `<p><b>${SELECTION[selection.status] ?? escape(selection.status)}</b> · ${escape(selection.reason)}</p>` : ''}</section>`;
}

function renderDecisions(data) {
  const items = data.decisions.map((decision) => {
    const probability = decision.selection_distribution?.probabilities?.[decision.selected_option_id];
    return `<li><code>${escape(decision.selected_option_id ?? '거부')}</code> · ${decision.decided_by === 'zoro' ? '모델' : '정책'}
      ${Number.isFinite(probability) ? ` · 선택 분포 ${fixed(probability, 2)}` : ''}
      ${decision.validation.valid ? '' : ` · <span class="danger-text">${escape(decision.validation.errors.join('; '))}</span>`}</li>`;
  }).join('');
  return `<section><h3>판단 과정 (${data.decisions.length}회)</h3><ol class="study-steps">${items}</ol>
    <p class="study-note">선택 분포는 행동 선택 확률이며 안전·회복 확률이 아닙니다.</p></section>`;
}

function renderResult(data) {
  const final = data.final;
  const model = final.model_client_kind === 'real' ? 'Zoro-1' : '로컬 규칙 (Zoro-1 미연결)';
  const uncertainties = final.uncertainties.slice(0, 6).map((item) => `<li>${escape(item)}</li>`).join('');
  return `<section><h3>결과</h3>
      <p><b>${OUTCOMES[final.outcome] ?? escape(final.outcome)}</b> · ${REASONS[final.termination_reason] ?? escape(final.termination_reason)} · 판단: ${model}</p>
      <p>${escape(final.summary)}</p></section>
    ${renderCommand(final.setpoint_command)}${renderBranches(data)}${renderDecisions(data)}
    <section><h3>남은 불확실성</h3><ul>${uncertainties}</ul></section>
    <p class="study-note">${escape(final.disclaimer)}</p>`;
}

export function mountZoroAgent(request) {
  const dialog = byId('zoro-agent-dialog');
  const status = byId('zoro-agent-status');
  const result = byId('zoro-agent-result');
  const retry = byId('retry-zoro-agent');
  let selection;
  let requestNumber = 0;

  async function analyze() {
    const number = ++requestNumber;
    status.textContent = 'Zoro 에이전트가 SOP 후보 중 시험할 분기를 고르고, 알람 시점에서 분기 시뮬레이션을 실행하는 중입니다.';
    status.removeAttribute('data-error');
    result.hidden = true;
    retry.hidden = true;
    try {
      const data = await request('/api/zoro-agent', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run: selection.run, alarm_id: selection.alarm.id }),
        signal: AbortSignal.timeout(300000),
      });
      if (number !== requestNumber || !dialog.open) return;
      if (data.status === 'unsupported') {
        status.textContent = '등록된 대응 절차가 없어 운영자 확인이 필요합니다.';
        return;
      }
      result.innerHTML = renderResult(data);
      result.hidden = false;
      status.textContent = '라이브 관제와 분리해 계산한 결과입니다.';
    } catch (error) {
      if (number !== requestNumber || !dialog.open) return;
      status.textContent = error.name === 'TimeoutError' ? '에이전트 실행 시간이 초과됐습니다.' : error.message;
      status.dataset.error = 'true';
      retry.hidden = false;
    }
  }

  byId('close-zoro-agent').addEventListener('click', () => dialog.close());
  retry.addEventListener('click', analyze);
  return {
    open(run, alarm) {
      selection = { run, alarm };
      byId('zoro-agent-title').textContent = alarm.label;
      byId('zoro-agent-context').textContent = ['ZORO 대응 검토', alarm.tag, elapsed(alarm.time_hours)].filter(Boolean).join(' · ');
      dialog.showModal();
      dialog.scrollTop = 0;
      analyze();
    },
  };
}
