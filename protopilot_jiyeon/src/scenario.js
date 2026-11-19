import economicThenStiction from '../docs/scenarios/s1-economic-then-stiction.json';
import falseHighTemp from '../docs/scenarios/s2-false-high-temp.json';
import restoreAfterCooling from '../docs/scenarios/s3-restore-after-cooling.json';
import compositionAndTwoCoolers from '../docs/scenarios/s4-composition-and-two-coolers.json';
import shiftPlanMismatch from '../docs/scenarios/s5-shift-plan-mismatch.json';
import { byId, elapsed, escape, reading } from './ui.js';
import { mountArchive } from './archive.js';

const demoScenarios = new Map([
  economicThenStiction, falseHighTemp, restoreAfterCooling, compositionAndTwoCoolers, shiftPlanMismatch,
].map((scenario) => [scenario.id, scenario]));

const protections = {
  high_pressure_to_production: '고압 · 생산량 제한',
  high_level_to_recycle: '고액위 · 재순환 밸브 제한',
};

export function mountScenario(request, command, onReplay) {
  const dialog = byId('scenario-dialog');
  const form = byId('scenario-form');
  const select = byId('demo-scenario');
  const error = byId('scenario-error');
  const submit = byId('load-scenario');
  const open = byId('open-scenario');
  const options = byId('controller-options');
  let latest, online, updating = false, replaying = false;
  for (const [id, scenario] of demoScenarios) select.add(new Option(scenario.title, id));

  function renderReplayControls() {
    select.disabled = !online || replaying;
    submit.disabled = select.disabled || !select.value;
  }

  async function replay(scenario) {
    if (replaying) throw new Error('시나리오를 불러오는 중입니다.');
    replaying = true;
    renderReplayControls();
    try {
      await command({ op: 'replay_scenario', scenario });
      dialog.close();
      onReplay();
    } finally {
      replaying = false;
      renderReplayControls();
    }
  }
  const archive = mountArchive(request, replay);

  function render() {
    const flags = latest?.controller_flags;
    open.disabled = !online || !flags;
    renderReplayControls();
    for (const box of options.querySelectorAll('input')) {
      box.disabled = !online || updating || !flags;
      box.checked = Boolean(flags?.[box.value]);
    }
    const active = Object.entries(protections).filter(([key]) => latest?.protection?.[key]);
    const state = byId('protection-state');
    state.textContent = !flags ? '' : active.length ? '개입 중' : flags.overrides ? '대기' : '꺼짐';
    state.classList.toggle('protection-active', active.length > 0);
    const lines = active.map(([, label]) => label);
    if (latest?.control) {
      lines.push(`생산 지수 ${reading(latest.control.fp)} · 분리기 온도 목표 ${reading(latest.control.setpoints.separator_temperature)} °C`);
    }
    if (latest?.replay_end != null && latest.step < latest.replay_end) {
      lines.push(`재실행 종료 ${elapsed(latest.replay_end * latest.step_hours)} · 예약 조작 ${latest.scheduled_changes}건`);
    }
    byId('controller-detail').textContent = lines.join(' / ');
  }

  open.addEventListener('click', () => {
    form.reset();
    render();
    error.hidden = true;
    dialog.showModal();
    if (byId('recording-archive').open) archive.refresh();
  });
  byId('close-scenario').addEventListener('click', () => dialog.close());
  select.addEventListener('change', () => {
    error.hidden = true;
    renderReplayControls();
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const scenario = demoScenarios.get(select.value);
    if (!scenario || submit.disabled) return;
    error.hidden = true;
    try {
      await replay(scenario);
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    }
  });
  options.addEventListener('change', async (event) => {
    updating = true;
    const { value, checked } = event.target;
    render();
    try {
      await command({ op: 'controller', id: value, value: checked });
    } finally {
      updating = false;
      render();
    }
  });
  return {
    init(flags, channels) {
      archive.init(channels);
      options.innerHTML = Object.entries(flags).map(([key, label]) =>
        `<label><input type="checkbox" value="${escape(key)}" disabled> ${escape(label)}</label>`).join('');
    },
    update(status, connected) {
      latest = status;
      online = connected;
      render();
    },
  };
}
