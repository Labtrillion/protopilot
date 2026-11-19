import { AREAS } from './topology.js';
import { byId, option, reading } from './ui.js';

export function mountSetpointSlider(apply) {
  const slider = byId('setpoint-slider');
  const label = byId('setpoint-label');
  const output = byId('setpoint-value');
  let loop, run, current;
  let available = false, editing = false, pending = null;

  function showValue(value) {
    const text = Number.isFinite(value) ? `${reading(value, loop.unit)} ${loop.unit}` : '—';
    output.value = text;
    slider.setAttribute('aria-valuetext', text);
  }

  function render() {
    slider.disabled = !available || pending !== null;
    if (editing) return;
    const value = pending && pending.loop === loop && pending.run === run ? pending.value : current;
    if (Number.isFinite(value)) slider.value = value;
    showValue(value);
  }

  slider.addEventListener('input', () => {
    editing = true;
    showValue(slider.valueAsNumber);
  });
  slider.addEventListener('change', async () => {
    if (!editing || slider.disabled) return;
    editing = false;
    const value = slider.valueAsNumber;
    if (value === current) return;
    const hadFocus = document.activeElement === slider;
    pending = { loop, run, value };
    render();
    try {
      await apply(loop.id, value);
    } finally {
      pending = null;
      render();
      if (hadFocus && !slider.disabled && document.activeElement === document.body) {
        slider.focus({ preventScroll: true });
      }
    }
  });
  slider.addEventListener('pointercancel', () => {
    editing = false;
    render();
  });

  return function update(nextLoop, status, connected) {
    if (nextLoop !== loop) {
      loop = nextLoop;
      label.textContent = loop ? `${loop.label} SP` : 'SP 목표';
      if (loop) {
        slider.min = loop.min;
        slider.max = loop.max;
        slider.step = loop.unit === 'kPa(g)' ? 1 : 0.1;
      }
      editing = false;
    }
    if (!connected || status?.run !== run) editing = false;
    run = status?.run;
    current = status?.setpoints[loop?.id];
    available = connected && Number.isFinite(current);
    render();
  };
}

export function setupControls(schema, current, command, onAdjustment) {
  const dialog = byId('adjust-dialog');
  const type = byId('adjust-kind');
  const target = byId('adjust-target');
  const value = byId('adjust-value');
  const lower = byId('adjust-low');
  const on = byId('adjust-enabled');
  const error = byId('adjust-error');
  const apply = byId('adjust-apply');
  const release = byId('adjust-release');
  const lists = {
    setpoint: schema.setpoints,
    mv: schema.channels.filter((ch) => ch.group === 'manipulated_variables'),
    disturbance: schema.disturbances,
    offset: schema.channels.filter((ch) => ['measurements', 'additional_measurements'].includes(ch.group)),
    limit: schema.channels,
  };
  const titles = {
    setpoint: '설정값', mv: '명령값 (%)', offset: '오프셋', limit: '상한', disturbance: '',
  };
  const defaults = { setpoint: 'reactor_pressure', mv: 'XMV(10)', disturbance: 'IDV(4)', offset: 'XMEAS(7)', limit: 'XMEAS(7)' };

  function setupTarget(preferred) {
    target.innerHTML = lists[type.value].map((item) => option(item.id, `${item.id.startsWith('X') || item.id.startsWith('IDV') ? item.id + ' · ' : ''}${item.label}`)).join('');
    target.value = lists[type.value].some((item) => item.id === preferred) ? preferred : defaults[type.value];
    setupValue();
  }

  function setupValue() {
    const kind = type.value;
    const status = current();
    const id = target.value;
    const item = lists[kind].find((entry) => entry.id === id);
    const isIdv = kind === 'disturbance';
    const isLimit = kind === 'limit';
    value.required = !isIdv && !isLimit;
    value.disabled = isIdv;
    lower.disabled = !isLimit;
    on.disabled = !isIdv;
    byId('adjust-value-wrap').hidden = isIdv;
    byId('adjust-enabled-wrap').hidden = !isIdv;
    byId('adjust-low-wrap').hidden = !isLimit;
    byId('adjust-value-label').textContent = titles[kind];
    for (const attr of ['min', 'max']) value.removeAttribute(attr);
    const currentValue = status.values[id];
    const tagValue = `${reading(currentValue, item.unit)} ${item.unit ?? ''}`;
    let descriptor = '';
    if (kind === 'setpoint') {
      value.min = item.min;
      value.max = item.max;
      value.value = +status.setpoints[id].toFixed(5);
      descriptor = `현재 목표 ${reading(status.setpoints[id])} ${item.unit} · 입력 범위 ${item.min}–${item.max}`;
      if (item.formula) descriptor += ` · PV ${reading(status.derived?.[item.pv])} ${item.unit} (${item.formula})`;
    } else if (kind === 'mv') {
      value.min = 0;
      value.max = 100;
      value.value = +(status.manual[id] ?? currentValue ?? 0).toFixed(4);
      descriptor = `현재 명령 ${tagValue} · 실제 ${reading(status.actuators?.[id])}% · ${status.limited_manual?.includes(id) ? '보호 제어 제한' : id in status.manual ? '수동' : '자동 제어'}`;
    } else if (kind === 'offset') {
      value.value = status.offsets[id] ?? 0;
      descriptor = `현재 표시 ${tagValue} · 오프셋은 화면과 알람에 적용됩니다.`;
    } else if (isLimit) {
      const [min, max] = status.limits[id] ?? [];
      lower.value = min ?? '';
      value.value = max ?? '';
      descriptor = `현재값 ${tagValue} · 빈 입력은 해당 한계를 사용하지 않습니다. 둘 다 비우면 기본 기준을 복원합니다.`;
    } else {
      const active = status.disturbances.includes(id);
      on.value = active ? 0 : 1;
      descriptor = `현재 상태 ${active ? '적용 중' : '해제'} · TEP Studio 모델 외란`;
    }
    byId('adjust-info').textContent = `${AREAS[item.area].code} · ${AREAS[item.area].name} / ${descriptor}`;
    release.hidden = isIdv;
    release.textContent = ({ mv: '자동 제어 복귀', offset: '센서 시험 해제', limit: '기본 기준 복원' })[kind] ?? '기본값 복원';
    error.hidden = true;
  }

  async function submit(restore) {
    const kind = type.value;
    const id = target.value;
    const item = lists[kind].find((entry) => entry.id === id);
    const entered = value.value === '' ? null : value.valueAsNumber;
    const body = {
      op: kind, id,
      value: restore ? null : kind === 'disturbance' ? Number(on.value) : entered,
    };
    if (kind === 'limit') body.low = restore || lower.value === '' ? null : lower.valueAsNumber;
    apply.disabled = release.disabled = true;
    error.hidden = true;
    try {
      await command(body, () => onAdjustment(kind, item));
      dialog.close();
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      apply.disabled = release.disabled = false;
    }
  }

  type.addEventListener('change', () => setupTarget());
  target.addEventListener('change', setupValue);
  byId('adjust-form').addEventListener('submit', (event) => { event.preventDefault(); submit(false); });
  release.addEventListener('click', () => submit(true));
  byId('close-dialog').addEventListener('click', () => dialog.close());

  return function open(tag) {
    if (!current() || dialog.open) return;
    const kind = tag?.startsWith('XMV(') ? 'mv' : tag?.startsWith('IDV(') ? 'disturbance' : tag?.startsWith('XMEAS') ? 'limit' : 'setpoint';
    type.value = kind;
    setupTarget(tag);
    dialog.showModal();
    if (kind === 'setpoint' && tag) {
      value.focus();
      value.select();
    } else {
      type.focus();
    }
  };
}
