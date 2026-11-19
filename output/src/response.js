import { byId, option, reading } from './ui.js';

const relatedControl = {
  reactor_pressure: 'XMV(6)', reactor_temperature: 'XMV(10)', reactor_level: 'XMV(11)',
  separator_level: 'XMV(7)', stripper_level: 'XMV(8)', production_rate: 'XMV(8)', pct_g: 'XMV(2)',
};
const COLORS = { pv: '#7be6d8', sp: '#a7bbff', grid: '#314550', label: '#a9bcc7' };
const CHANGES = 360;

function draw(canvas, points, unit) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  const dpr = Math.min(2, devicePixelRatio);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const left = 58, right = w - 13, top = 10, bottom = h - 25;
  ctx.font = '11px ui-monospace, monospace';
  ctx.fillStyle = COLORS.label;
  if (!points.length) {
    ctx.fillText('모델 데이터 연결 중', left, h / 2);
    return;
  }
  const values = points.flatMap(({ pv, sp }) => [pv, sp]).filter(Number.isFinite);
  if (!values.length) {
    ctx.fillText('표시할 계측값이 없습니다.', left, h / 2);
    return;
  }
  const max = Math.max(...values), min = Math.min(...values);
  const pad = Math.max(Math.abs(max) * 0.0005, (max - min) * 0.13, 0.022);
  const hi = max + pad, lo = min - pad;
  const begin = points[0].time, span = Math.max(points.at(-1).time - begin, 0.02);
  const x = (time) => left + (time - begin) / span * (right - left);
  const y = (value) => bottom - (value - lo) / (hi - lo) * (bottom - top);
  ctx.textAlign = 'right';
  for (let i = 0; i < 4; i++) {
    const value = lo + i / 3 * (hi - lo);
    const yy = y(value);
    ctx.strokeStyle = COLORS.grid;
    ctx.beginPath();
    ctx.moveTo(left, yy);
    ctx.lineTo(right, yy);
    ctx.stroke();
    ctx.fillText(reading(value, unit), left - 7, yy + 3);
  }
  ctx.textAlign = 'left';
  ctx.fillText(`${begin.toFixed(2)} h`, left, h - 7);
  ctx.textAlign = 'right';
  ctx.fillText(`${(begin + span).toFixed(2)} h`, right, h - 7);
  for (const key of ['sp', 'pv']) {
    ctx.strokeStyle = COLORS[key];
    ctx.lineWidth = key === 'pv' ? 2 : 1.4;
    ctx.setLineDash(key === 'sp' ? [5, 4] : []);
    ctx.beginPath();
    let last = null;
    for (const point of points) {
      if (!Number.isFinite(point[key])) {
        last = null;
        continue;
      }
      const xx = x(point.time), yy = y(point[key]);
      if (!last) ctx.moveTo(xx, yy);
      else {
        if (key === 'sp') ctx.lineTo(xx, y(last.sp));
        ctx.lineTo(xx, yy);
      }
      last = point;
    }
    ctx.stroke();
    if (last) {
      ctx.fillStyle = COLORS[key];
      ctx.beginPath();
      ctx.arc(x(last.time), y(last[key]), 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

export function mountResponse(onSetpoint, onSelect) {
  const select = byId('response-select'), adjust = byId('response-adjust');
  const label = byId('response-values'), canvas = byId('response-chart');
  const histories = new Map();
  let definitions = [], latest = null, selected = 'reactor_pressure', visible = false;
  const render = () => {
    if (!visible) return;
    const spec = definitions.find((entry) => entry.id === selected);
    const points = histories.get(selected) ?? [];
    const last = points.at(-1);
    if (spec) {
      const n = (v) => reading(v, spec.unit);
      const delta = Number.isFinite(last?.pv) && Number.isFinite(last?.sp) ? last.pv - last.sp : null;
      const control = relatedControl[selected];
      const command = latest?.manual?.[control] ?? latest?.values[control];
      label.innerHTML = `
        <div><span>PV 실측 · ${spec.pv}</span><b>${n(last?.pv)} <small>${spec.unit}</small></b></div>
        <div><span>SP 목표</span><b>${n(last?.sp)} <small>${spec.unit}</small></b></div>
        <div><span>편차 (PV − SP)</span><b>${delta > 0 ? '+' : ''}${n(delta)} <small>${spec.unit}</small></b></div>
        <div><span>${control} · 실제 / 명령</span><b>${reading(latest?.actuators?.[control])}% <small>/ ${reading(command)}%</small></b></div>`;
      canvas.setAttribute('aria-label', `${spec.label} 추세, 실측 ${n(last?.pv)} ${spec.unit}, 목표 ${n(last?.sp)} ${spec.unit}`);
    }
    draw(canvas, points, spec?.unit);
  };
  select.addEventListener('change', () => { selected = select.value; onSelect(selected); });
  adjust.addEventListener('click', () => onSetpoint(selected));
  new ResizeObserver(render).observe(canvas.parentElement);
  return {
    init(items) {
      definitions = items;
      select.innerHTML = items.map((item) => option(item.id, `${item.label} · ${item.pv}`)).join('');
      select.value = selected;
    },
    enable(connected) { select.disabled = !connected; adjust.disabled = !connected; },
    show(enabled, id) {
      if (id && definitions.some((item) => item.id === id)) selected = id;
      select.value = selected;
      visible = enabled;
      render();
    },
    update(status) {
      if (status.run !== latest?.run) histories.clear();
      latest = status;
      for (const item of definitions) {
        const series = histories.get(item.id) ?? [];
        const before = series.at(-1);
        const point = { time: status.time_hours, sample: status.sample, pv: status.values[item.pv], sp: status.setpoints[item.id] };
        if (!before || before.sample !== point.sample || before.sp !== point.sp) {
          series.push(point);
          if (series.length > CHANGES) series.shift();
        }
        histories.set(item.id, series);
      }
      render();
    },
  };
}
