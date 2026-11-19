import { byId, elapsed, escape, reading } from './ui.js';
import { mountArchive } from './archive.js';

const protections = {
  high_pressure_to_production: '고압 · 생산량 제한',
  high_level_to_recycle: '고액위 · 재순환 밸브 제한',
};

export function mountScenario(request, command, onReplay) {
  const dialog = byId('scenario-dialog');
  const form = byId('scenario-form');
  const file = byId('scenario-file');
  const error = byId('scenario-error');
  const submit = byId('load-scenario');
  const open = byId('open-scenario');
  const options = byId('controller-options');
  let latest, online, updating = false;
  async function replay(scenario) {
    await command({ op: 'replay_scenario', scenario });
    dialog.close();
    onReplay();
  }
  const archive = mountArchive(request, replay);

  function render() {
    const flags = latest?.controller_flags;
    open.disabled = !online || !flags;
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
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    try {
      const selected = file.files[0];
      if (!selected || selected.size > 1024 * 1024) throw new Error('1 MB 이하의 시나리오 JSON을 선택하세요.');
      let scenario;
      try {
        scenario = JSON.parse(await selected.text());
      } catch {
        throw new Error('JSON 파일 형식을 확인하세요.');
      }
      await replay(scenario);
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      submit.disabled = false;
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
