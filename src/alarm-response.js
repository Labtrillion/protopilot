import { byId, elapsed, escape, reading } from './ui.js';

const summaryMetrics = [
  { key: 'shutdown', label: '보호 정지', format: (branch) => branch.shutdown ? '발생' : '없음' },
  { key: 'violation_minutes', label: '운전 범위 이탈', unit: '분' },
  { key: 'mean_production', label: '평균 제품 유량', unit: 'm³/h' },
];

const detailMetrics = [
  ['window_hours', '비교 시간', (branch) => elapsed(branch.window_hours)],
  ['protection_minutes', '자동 보호 개입 (분)'],
  ['peak_pressure', '반응기 최고 압력 (kPa(g))'],
  ['peak_temperature', '반응기 최고 온도 (°C)'],
  ['temperature_sd', '반응기 온도 표준편차 (°C)'],
  ['peak_separator_level', '후단 분리기 최고 액위 (%)'],
  ['production_target', '계획된 생산 목표 (m³/h)'],
  ['action_applied', '감량 적용', (branch) => {
    if (branch.id === 'hold') return '해당 없음';
    return branch.action_applied ? '적용' : '미적용';
  }],
];

function renderDecision(decision) {
  const confidence = decision.response.confidence;
  const source = decision.fallback ? '로컬 규칙' : 'Zoro-1 · System 1';
  return `<section class="adjust-info study-decision">
    <div class="study-heading"><h3>우선 검토할 대응</h3><span>${source}${confidence !== null ? ` · 신뢰도 ${Math.round(confidence * 100)}%` : ''}</span></div>
    <strong>${escape(decision.response.label)}</strong>
    <p>${escape(decision.diagnosis.label)}</p>
  </section>`;
}

function renderSummary(branches, selected) {
  return `<div class="study-table-frame">
    <table class="study-table" aria-label="주요 대응 비교 지표">
      <thead><tr><th scope="col">대응</th>${summaryMetrics.map(({ label, unit }) =>
        `<th scope="col" class="right">${label}${unit ? `<span class="channel-unit">${unit}</span>` : ''}</th>`).join('')}</tr></thead>
      <tbody>${branches.map((branch) => `<tr${branch.id === selected ? ' class="study-selected"' : ''}>
        <th scope="row">${escape(branch.label)}${branch.id === selected ? '<small>우선 검토</small>' : ''}</th>
        ${summaryMetrics.map(({ key, unit, format }) => `<td class="right${unit ? ' numeric' : ''}">
          ${branch.error ? '—' : format ? format(branch) : reading(branch[key])}</td>`).join('')}</tr>`).join('')}
      </tbody>
    </table>
  </div>`;
}

function renderDetailTable(branches) {
  return `<div class="study-table-frame study-table-scroll" tabindex="0" role="region" aria-label="세부 대응 비교 지표">
    <table class="study-table study-detail-table">
      <thead><tr><th scope="col">비교 지표</th>${branches.map(({ label }) => `<th scope="col">${escape(label)}</th>`).join('')}</tr></thead>
      <tbody>${detailMetrics.map(([key, name, format]) =>
        `<tr><th scope="row">${name}</th>${branches.map((branch) =>
          `<td>${branch.error ? '—' : format ? format(branch) : reading(branch[key])}</td>`).join('')}</tr>`).join('')}
      </tbody>
    </table>
  </div>`;
}

function renderComparison({ branches, start, decision, alarm, sweep }) {
  const sensorTest = alarm.source === '센서 시험';
  const unavailable = branches.filter((branch) => branch.error);
  return `<section class="study-section">
    <div class="study-heading"><h3>SOP 대응안 비교</h3><span>${sweep ? `대안 ${sweep.branches.length}개 계산 · ` : ''}비교 시간 최대 ${elapsed(start.horizon_hours)}</span></div>
    ${renderSummary(branches, decision.response.choice)}
    <p class="study-note">알람이 발생한 상태에서 시작하며, 이탈 시간은 TEP 모델의 운전 범위를 기준으로 계산합니다.</p>
    ${sensorTest ? '<p class="study-note">센서 시험 전의 모델 측정값으로 비교했습니다.</p>' : ''}
    ${unavailable.map((branch) => `<p class="study-note" role="status">${escape(branch.label)} · ${escape(branch.error)}</p>`).join('')}
  </section>`;
}

function renderResult(data) {
  const { sop, decision, readings, related_alarms, alarm } = data;
  if (!sop) return '<p class="adjust-info">이 알람에 등록된 대응 절차가 없습니다. 공정도에서 설비 위치와 알람 발생 시 측정값을 확인할 수 있습니다.</p>';
  const related = related_alarms.filter((item) => item.id !== alarm.id);
  const citation = typeof sop.source_url === 'string' && sop.source_url.startsWith('https://doi.org/') && sop.source_title
    ? `<a href="${escape(sop.source_url)}" target="_blank" rel="noopener noreferrer">${escape(sop.source_title)}</a>` : '';
  return `${decision ? renderDecision(decision) + renderComparison(data) : ''}
  <div class="study-disclosures">
    <details${decision ? '' : ' open'}>
      <summary>확인 절차</summary>
      <div class="study-section study-detail">
        <div class="study-heading"><h3>${escape(sop.title)}</h3><span>${escape(sop.id)} · 개정 ${sop.revision}</span></div>
        <ol>${sop.checks.map((check) => `<li>${escape(check)}</li>`).join('')}</ol>
        ${citation}
      </div>
    </details>
    ${decision ? `<details><summary>세부 지표</summary><div class="study-detail">${renderDetailTable(data.branches)}</div></details>` : ''}
    <details>
      <summary>알람 당시 계측${related.length ? ` · 동시 알람 ${related.length}건` : ''}</summary>
      <div class="study-section study-detail">
        ${related.length ? `<p>${related.map((item) => escape(item.label)).join(' · ')}</p>` : ''}
        <dl class="study-evidence">${readings.map(({ tag, label, value, unit }) =>
          `<div><dt>${escape(label)} <code>${escape(tag)}</code></dt><dd>${reading(value, unit)} ${escape(unit ?? '')}</dd></div>`).join('')}</dl>
        ${data.logged ? `<p class="study-note study-trace">실행 로그 <code>${escape(data.decision_id)}</code></p>` : ''}
      </div>
    </details>
  </div>
  ${data.logged ? '' : '<p class="study-note" role="status">실행 로그를 저장하지 못했습니다.</p>'}`;
}

export function mountAlarmResponse(request, openSimulationResults) {
  const dialog = byId('alarm-response-dialog');
  const status = byId('alarm-response-status');
  const result = byId('alarm-response-result');
  const retry = byId('retry-alarm-response');
  const simulations = byId('open-simulation-results');
  let selection, study;
  let requestNumber = 0;

  async function compare() {
    const number = ++requestNumber;
    const isCurrent = () => number === requestNumber && dialog.open;
    status.textContent = '대응 방안 비교를 요청하고 있습니다.';
    status.removeAttribute('data-error');
    result.hidden = true;
    retry.hidden = simulations.hidden = true;
    study = undefined;
    try {
      let data = await request('/api/alarm-response', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run: selection.run, alarm_id: selection.alarm.id, wait: false }),
      });
      while (isCurrent() && data.job_id) {
        const message = data.status === 'queued' ? '비교 대기 중입니다. 실행 중인 작업이 끝나면 자동으로 시작합니다.'
          : '알람 시점의 공정 상태에서 SOP와 대안을 계산하고 있습니다.';
        if (status.textContent !== message) status.textContent = message;
        await new Promise((resolve) => setTimeout(resolve, 1000));
        if (!isCurrent()) return;
        data = await request(`/api/alarm-response/${encodeURIComponent(data.job_id)}`);
      }
      if (!isCurrent()) return;
      result.innerHTML = renderResult(data);
      result.hidden = false;
      study = data;
      simulations.hidden = !study.branches.length;
      status.textContent = '';
    } catch (error) {
      if (!isCurrent()) return;
      status.textContent = error.name === 'TimeoutError' ? '비교 요청 시간이 초과됐습니다.'
        : error instanceof TypeError ? '서버에 연결할 수 없습니다. 연결을 확인하고 다시 시도하세요.' : error.message;
      status.dataset.error = 'true';
      retry.hidden = false;
    }
  }

  byId('close-alarm-response').addEventListener('click', () => dialog.close());
  retry.addEventListener('click', compare);
  simulations.addEventListener('click', () => {
    if (!study?.branches.length) return;
    dialog.close();
    openSimulationResults(study);
  });
  return {
    open(run, alarm) {
      if (dialog.open) return;
      selection = { run, alarm };
      byId('alarm-response-title').textContent = alarm.label;
      byId('alarm-response-context').textContent = [alarm.tag, alarm.source, elapsed(alarm.time_hours)].filter(Boolean).join(' · ');
      dialog.showModal();
      dialog.scrollTop = 0;
      compare();
    },
  };
}
