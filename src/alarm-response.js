import { byId, elapsed, escape, reading } from './ui.js';

const metrics = [
  ['shutdown', '모델 트립', (branch) => branch.shutdown ? '발생' : '없음'],
  ['window_hours', '비교 시간', (branch) => elapsed(branch.window_hours)],
  ['violation_minutes', '정상 운전 범위 이탈 (분)'],
  ['protection_minutes', '자동 보호 개입 (분)'],
  ['peak_pressure', '반응기 최고 압력 (kPa(g))'],
  ['peak_temperature', '반응기 최고 온도 (°C)'],
  ['temperature_sd', '반응기 온도 표준편차 (°C)'],
  ['peak_separator_level', '후단 분리기 최고 액위 (%)'],
  ['mean_production', '평균 제품 유량 (m³/h)'],
  ['production_target', '계획된 생산 목표 (m³/h)'],
  ['action_applied', '감량 적용', (branch) => {
    if (branch.id === 'hold') return '해당 없음';
    return branch.action_applied ? '적용' : '미적용';
  }],
];

function renderDecision(decision) {
  const confidence = decision.response.confidence;
  const source = decision.fallback ? '로컬 규칙으로 판단' : 'Zoro-1 · System 1';
  return `<section class="study-section">
    <div class="study-heading"><h3>우선 검토할 대응</h3><span>${source}</span></div>
    <p><strong>${escape(decision.response.label)}</strong>
      ${confidence !== null ? `<span class="study-confidence"> · 신뢰도 ${Math.round(confidence * 100)}%</span>` : ''}
    </p>
    <p>${escape(decision.diagnosis.label)}</p>
  </section>`;
}

function renderComparison({ branches, start, decision, alarm }) {
  const selected = decision.response.choice;
  const sensorTest = alarm.source === '센서 시험';
  const unavailable = branches.filter((branch) => branch.error);
  return `<section class="study-section">
    <h3 id="study-results-heading">대응별 시뮬레이션 결과</h3>
    <p>알람 발생 시점 ${elapsed(start.time_hours)}부터 최대 ${elapsed(start.horizon_hours)} 비교했습니다.
      모든 분기는 같은 공정 상태·시드·외란에서 시작합니다.</p>
    ${sensorTest ? '<p>센서 시험 알람입니다. 시뮬레이션에는 오프셋 적용 전 모델 계측을 사용했습니다.</p>' : ''}
    ${unavailable.map((branch) => `<p role="status">${escape(branch.label)} · ${escape(branch.error)}</p>`).join('')}
    <div class="study-table-scroll" tabindex="0" role="region" aria-labelledby="study-results-heading">
      <table class="study-table">
        <thead><tr><th scope="col">비교 지표</th>${branches.map((branch) =>
          `<th scope="col">${escape(branch.label)}${branch.id === selected ? '<small>우선 검토</small>' : ''}</th>`).join('')}</tr></thead>
        <tbody>${metrics.map(([key, label, format]) =>
          `<tr><th scope="row">${label}</th>${branches.map((branch) =>
            `<td>${branch.error ? '—' : format ? format(branch) : reading(branch[key])}</td>`).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>
  </section>`;
}

function renderResult(data) {
  const { sop, decision, readings, related_alarms, alarm } = data;
  if (!sop) return '<p>이 알람 태그와 일치하는 데모용 SOP가 없습니다.</p>';
  const related = related_alarms.filter((item) => item.id !== alarm.id);
  return `<section class="study-section">
    <div class="study-heading"><h3>${escape(sop.title)}</h3><span>${escape(sop.id)} · 개정 ${sop.revision}</span></div>
    <ol>${sop.checks.map((check) => `<li>${escape(check)}</li>`).join('')}</ol>
    <a href="${escape(sop.source_url)}" target="_blank" rel="noopener noreferrer">${escape(sop.source_title)}</a>
    <details class="study-evidence">
      <summary>알람 발생 시점의 계측${related.length ? ` · 동시 알람 ${related.length}건` : ''}</summary>
      ${related.length ? `<p>${related.map((item) => escape(item.label)).join(' · ')}</p>` : ''}
      <dl>${readings.map(({ tag, label, value, unit }) =>
        `<div><dt>${escape(label)} <code>${escape(tag)}</code></dt><dd>${reading(value, unit)} ${escape(unit ?? '')}</dd></div>`).join('')}</dl>
    </details>
  </section>
  ${decision ? renderDecision(decision) + renderComparison(data) : ''}
  <p class="study-trace">${data.logged ? `실행 로그 ${escape(data.decision_id)}` : '실행 로그를 저장하지 못했습니다.'}</p>`;
}

export function mountAlarmResponse(request) {
  const dialog = byId('alarm-response-dialog');
  const status = byId('alarm-response-status');
  const result = byId('alarm-response-result');
  const retry = byId('retry-alarm-response');
  let selection;
  let requestNumber = 0;

  async function compare() {
    const number = ++requestNumber;
    status.textContent = 'SOP를 찾고 같은 시작 조건에서 대응을 비교하는 중입니다.';
    status.removeAttribute('data-error');
    result.hidden = true;
    retry.hidden = true;
    try {
      const data = await request('/api/alarm-response', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run: selection.run, alarm_id: selection.alarm.id }),
        signal: AbortSignal.timeout(60000),
      });
      if (number !== requestNumber || !dialog.open) return;
      result.innerHTML = renderResult(data);
      result.hidden = false;
      status.textContent = data.branches.length
        ? '라이브 관제와 분리해 계산한 TEP 시뮬레이션 결과입니다.'
        : '알람 발생 시점의 기록을 조회했습니다.';
    } catch (error) {
      if (number !== requestNumber || !dialog.open) return;
      status.textContent = error.name === 'TimeoutError' ? '비교 요청 시간이 초과됐습니다.' : error.message;
      status.dataset.error = 'true';
      retry.hidden = false;
    }
  }

  byId('close-alarm-response').addEventListener('click', () => dialog.close());
  retry.addEventListener('click', compare);
  return {
    open(run, alarm) {
      selection = { run, alarm };
      byId('alarm-response-title').textContent = alarm.label;
      byId('alarm-response-context').textContent = [alarm.tag, alarm.source, elapsed(alarm.time_hours)].filter(Boolean).join(' · ');
      dialog.showModal();
      dialog.scrollTop = 0;
      compare();
    },
  };
}
