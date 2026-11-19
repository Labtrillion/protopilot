import { byId, elapsed } from './ui.js';
import { renderZoroSummary, zoroVerdict } from './zoro-results.js';

export function mountZoroAgent(request, openSimulationResults, getSetpoints) {
  const dialog = byId('zoro-agent-dialog');
  const status = byId('zoro-agent-status');
  const result = byId('zoro-agent-result');
  const retry = byId('retry-zoro-agent');
  const simulations = byId('open-zoro-simulation-results');
  const latestPanel = byId('latest-zoro-review');
  let selection, study, latest, run, requestNumber = 0;

  function update(runId) {
    run = runId;
    latestPanel.hidden = !latest || latest.run !== run;
    if (dialog.open && selection.run !== run) {
      status.textContent = '실행이 바뀌었습니다. 현재 알람에서 다시 검토하세요.';
      result.hidden = retry.hidden = simulations.hidden = true;
    }
  }

  async function analyze(shouldRetry = false) {
    const number = ++requestNumber;
    const isCurrent = () => number === requestNumber && dialog.open && selection.run === run;
    status.textContent = '알람 시점의 Zoro-1 판단을 요청하고 있습니다.';
    status.removeAttribute('data-error');
    result.hidden = retry.hidden = simulations.hidden = true;
    study = undefined;
    try {
      let data = await request('/api/setpoint-decision', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run: selection.run, alarm_id: selection.alarm.id, retry: shouldRetry }),
      });
      while (isCurrent() && data.job_id) {
        const message = data.status === 'queued' ? '앞선 분석이 끝나면 시작합니다.'
          : 'Zoro-1이 근거를 검토하고 후보를 채점합니다. 선별된 조합은 시뮬레이터에서 비교합니다.';
        if (status.textContent !== message) status.textContent = message;
        await new Promise((resolve) => setTimeout(resolve, 1250));
        if (!isCurrent()) return;
        data = await request(`/api/setpoint-decision/${encodeURIComponent(data.job_id)}`);
      }
      if (!isCurrent()) return;
      const { decision, feature2, start, branches, decision_id: decisionId, handoff } = data;
      study = {
        kind: 'zoro', run, alarm: selection.alarm, decision, feature2, start, branches, decision_id: decisionId,
        setpoints: Object.fromEntries(getSetpoints().map(({ id, label, unit }) => [id, { label, unit }])),
        evidence: (handoff.rag_result?.evidence ?? []).map(({ source_document, statement }) => ({ source_document, statement })),
      };
      result.innerHTML = renderZoroSummary(study);
      result.hidden = simulations.hidden = false;
      retry.hidden = feature2.status !== 'human_review' && !feature2.selection?.reason?.startsWith('score_unavailable')
        && !feature2.selection?.choice_error;
      status.textContent = '';
      latest = study;
      byId('latest-zoro-summary').textContent = `${study.alarm.label} · ${zoroVerdict(study)}`;
      update(run);
    } catch (error) {
      if (!isCurrent()) return;
      status.textContent = error.name === 'TimeoutError' ? '서버 응답 시간이 초과됐습니다.'
        : error instanceof TypeError ? '서버에 연결할 수 없습니다.' : error.message;
      status.dataset.error = 'true';
      retry.hidden = false;
    }
  }

  byId('close-zoro-agent').addEventListener('click', () => dialog.close());
  byId('open-latest-zoro-results').addEventListener('click', () => openSimulationResults(latest));
  retry.addEventListener('click', () => analyze(true));
  simulations.addEventListener('click', () => {
    dialog.close();
    openSimulationResults(study);
  });
  return {
    update,
    open(runId, alarm) {
      if (dialog.open) return;
      selection = { run: runId, alarm };
      byId('zoro-agent-title').textContent = alarm.label;
      byId('zoro-agent-context').textContent = ['Zoro-1 대응 검토', alarm.tag, alarm.source, elapsed(alarm.time_hours)]
        .filter(Boolean).join(' · ');
      dialog.showModal();
      dialog.scrollTop = 0;
      analyze();
    },
  };
}
