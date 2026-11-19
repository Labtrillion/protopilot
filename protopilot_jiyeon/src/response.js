import { byId, elapsed, option, reading } from './ui.js';
import { drawResponseChart, responseWindow } from './response-chart.js';

const relatedControl = {
  reactor_pressure: 'XMV(6)', reactor_temperature: 'XMV(10)', reactor_level: 'XMV(11)',
  separator_level: 'XMV(7)', stripper_level: 'XMV(8)', production_rate: 'XMV(8)', pct_g: 'XMV(2)',
  ya: 'XMV(3)', yac: 'XMV(4)',
};
const separatorSignals = [
  { key: 'pressure', tag: 'XMEAS(13)', label: '분리기 압력', unit: 'kPa(g)', changeUnit: ' kPa(g)' },
  { key: 'level', tag: 'XMEAS(12)', label: '분리기 액위', unit: '%', changeUnit: '%p', showTime: true },
];
const HISTORY_LIMIT = 360;
// Deviation and the PV trend use the raw measurement, including during a sensor offset test.
const measuredPv = (status, tag) => status.derived?.[tag] ?? status.native_values?.[tag] ?? status.values[tag];

function latestSetpointChanges(events) {
  const changes = new Map();
  for (const event of events) {
    if (event.kind === 'change' && Number.isFinite(event.value) && Number.isFinite(event.time_hours)
      && event.previous_value !== event.value && !changes.has(event.tag)) changes.set(event.tag, event);
  }
  return changes;
}

function updateHistory(history, status, spec, event) {
  const control = relatedControl[spec.id];
  const { points } = history;
  const before = points.at(-1);
  const point = {
    time: status.time_hours, sample: status.sample, pv: measuredPv(status, spec.pv), sp: status.setpoints[spec.id],
    command: status.manual?.[control] ?? status.requested_action?.[control] ?? status.values[control],
    position: status.actuators?.[control],
  };
  const trackSeparator = spec.id === 'reactor_pressure';
  if (trackSeparator) {
    for (const { tag } of separatorSignals) point[tag] = measuredPv(status, tag);
  }
  let change;
  if (Number.isFinite(event?.previous_value)) {
    change = { time: event.time_hours, from: event.previous_value, to: event.value };
  } else if (Number.isFinite(before?.sp) && Number.isFinite(point.sp) && before.sp !== point.sp) {
    change = { time: event?.value === point.sp ? event.time_hours : point.time, from: before.sp, to: point.sp };
  }
  if (change) {
    if (trackSeparator && event?.time_hours === change.time && event.value === change.to) {
      const previous = history.change;
      const sameChange = previous?.time === change.time && previous.to === change.to;
      change.separator = event.separator_readings ?? (sameChange ? previous.separator : undefined)
        ?? (point.time === change.time ? point : points.findLast(({ time }) => time === change.time));
    }
    history.change = change;
  }
  if (!before || before.sample !== point.sample || before.sp !== point.sp || before.command !== point.command) {
    points.push(point);
    if (points.length > HISTORY_LIMIT) points.shift();
  }
}

function renderSeparatorSignals(signals, view, last, change, mark) {
  const baseline = change?.separator;
  if (mark && baseline && !view.points.some(({ time }) => time === mark.time)) {
    const index = view.points.findIndex(({ time }) => time > mark.time);
    view = { ...view, points: [...view.points.slice(0, index), { ...baseline, time: mark.time }, ...view.points.slice(index)] };
  }
  for (const { tag, label, unit, changeUnit, showTime, chart, current, difference } of signals) {
    const value = last[tag], start = baseline?.[tag];
    const delta = Number.isFinite(value) && Number.isFinite(start) ? value - start : null;
    current.textContent = Number.isFinite(value) ? `${reading(value, unit)}${unit === '%' ? '%' : ' ' + unit}` : '—';
    difference.textContent = delta === null ? '—' : `${delta > 0 ? '+' : ''}${reading(delta)}${changeUnit}`;
    const comparison = delta === null ? '비교 기준값 없음' : difference.textContent;
    const markerLabel = mark ? `, SP 변경 시점 ${elapsed(mark.time)}` : '';
    chart.setAttribute('aria-label', `${label} 추세, 현재 ${current.textContent}, SP 변경 대비 ${comparison}${markerLabel}`);
    drawResponseChart(chart, view, { actual: tag, unit, change: mark, showTime, compact: true });
  }
}

export function mountResponse(onSelect) {
  const select = byId('response-select'), values = byId('response-values');
  const pvChart = byId('response-chart'), valveChart = byId('response-valve-chart');
  const valve = byId('response-valve'), valveTag = byId('response-valve-tag');
  const command = byId('response-valve-command'), position = byId('response-valve-position');
  const windowLabel = byId('response-window'), changeMarker = byId('response-change-marker');
  const adjustment = byId('response-adjustment'), adjustmentTime = byId('response-adjustment-time');
  const deviation = byId('response-deviation');
  const connected = byId('response-connected');
  const signals = separatorSignals.map((signal) => ({
    ...signal,
    chart: byId(`response-separator-${signal.key}-chart`),
    current: byId(`response-separator-${signal.key}-value`),
    difference: byId(`response-separator-${signal.key}-delta`),
  }));
  const histories = new Map();
  let definitions = [], latest = null, selected = 'reactor_pressure', visible = false;

  const render = () => {
    if (!visible) return;
    const spec = definitions.find((entry) => entry.id === selected);
    const history = histories.get(selected);
    if (!spec || !history) return;
    const { points, change } = history;
    const last = points.at(-1);
    const n = (value) => reading(value, spec.unit);
    const delta = Number.isFinite(last.pv) && Number.isFinite(last.sp) ? last.pv - last.sp : null;
    const control = relatedControl[selected];
    const offset = latest.offsets?.[spec.pv];
    const tested = offset != null;
    values.innerHTML = `
      <div><span>PV 실측${tested ? ' (원계측)' : ''} · ${spec.pv}</span><b>${n(last.pv)} <small>${spec.unit}</small></b></div>
      <div><span>SP 목표</span><b>${n(last.sp)} <small>${spec.unit}</small></b></div>
      ${tested ? `<div class="response-test"><span>센서 시험 표시값 · 제어·편차 계산에 미사용</span><b>${n(latest.values[spec.pv])} <small>${spec.unit}</small></b>
        <span>시험 오프셋 ${offset > 0 ? '+' : ''}${n(offset)} ${spec.unit}</span></div>` : ''}`;

    adjustment.textContent = change ? `${n(change.from)} → ${n(change.to)} ${spec.unit}` : '이 실행에서 SP 변경 기록이 없습니다.';
    adjustment.toggleAttribute('data-changed', Boolean(change));
    adjustmentTime.hidden = !change;
    adjustmentTime.textContent = change ? `${elapsed(change.time)} 변경 · ${elapsed(latest.time_hours - change.time)} 경과` : '';
    deviation.textContent = Number.isFinite(delta) ? `${delta > 0 ? '+' : ''}${n(delta)} ${spec.unit}` : '—';

    const view = responseWindow(points);
    const mark = change && change.time >= view.start && change.time <= last.time ? change : null;
    windowLabel.textContent = `최근 ${view.minutes}분`;
    changeMarker.hidden = !mark;
    const markerLabel = mark ? `, SP 변경 시점 ${elapsed(mark.time)}` : '';
    pvChart.setAttribute('aria-label', `${spec.label} 추세, 실측 ${n(last.pv)} ${spec.unit}, 목표 ${n(last.sp)} ${spec.unit}${markerLabel}${tested ? `, 센서 시험 표시값 ${n(latest.values[spec.pv])} ${spec.unit}` : ''}`);
    drawResponseChart(pvChart, view, { actual: 'pv', target: 'sp', unit: spec.unit, change: mark, showTime: !control });

    valve.hidden = !control;
    if (control) {
      valveTag.textContent = control;
      command.textContent = Number.isFinite(last.command) ? `${reading(last.command)}%` : '—';
      position.textContent = Number.isFinite(last.position) ? `${reading(last.position)}%` : '—';
      valveChart.setAttribute('aria-label', `${control} 밸브 응답, 명령 ${command.textContent}, 실제 개도 ${position.textContent}${markerLabel}`);
      drawResponseChart(valveChart, view, { actual: 'position', target: 'command', change: mark, showTime: true, valve: true });
    }
    connected.hidden = selected !== 'reactor_pressure';
    if (!connected.hidden) renderSeparatorSignals(signals, view, last, change, mark);
  };

  select.addEventListener('change', () => { selected = select.value; onSelect(selected); });
  const observer = new ResizeObserver(render);
  observer.observe(pvChart);
  observer.observe(valveChart);
  for (const { chart } of signals) observer.observe(chart);
  return {
    init(items) {
      definitions = items;
      select.innerHTML = items.map((item) => option(item.id, `${item.label} · ${item.pv}`)).join('');
      select.value = selected;
    },
    selected: () => selected,
    enable(connected) { select.disabled = !connected; },
    show(enabled, id) {
      if (id && definitions.some((item) => item.id === id)) selected = id;
      select.value = selected;
      visible = enabled;
      render();
    },
    update(status) {
      if (status.run !== latest?.run) histories.clear();
      latest = status;
      const changes = latestSetpointChanges(status.events ?? []);
      for (const item of definitions) {
        const history = histories.get(item.id) ?? { points: [], change: null };
        updateHistory(history, status, item, changes.get(item.id));
        histories.set(item.id, history);
      }
      render();
    },
  };
}
