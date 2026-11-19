import { elapsed, reading } from './ui.js';
import { readColor } from './palette.js';

const COLORS = {
  actual: readColor('--plot-pv'), target: readColor('--response-target'),
  grid: readColor('--plot-grid'), label: readColor('--muted'), change: readColor('--tracked'),
};
const WINDOW_HOURS = 0.5;

export function responseWindow(points) {
  const time = points.at(-1)?.time ?? 0;
  let start = Math.max(0, time - WINDOW_HOURS);
  let count = 0, lastTime;
  // Fast playback can leave only a few distinct samples inside 30 minutes.
  for (let i = points.length - 1; i >= 0; i--) {
    const sampleTime = points[i].time;
    if (sampleTime === lastTime) continue;
    lastTime = sampleTime;
    if (++count === 5) {
      start = Math.min(start, sampleTime);
      break;
    }
  }
  return {
    start, end: time,
    minutes: Math.max(30, Math.ceil((time - start) * 60 - 1e-6)),
    points: points.filter((point) => point.time >= start),
  };
}

function drawLine(ctx, points, key, x, y, stepped) {
  ctx.strokeStyle = ctx.fillStyle = stepped ? COLORS.target : COLORS.actual;
  ctx.lineWidth = stepped ? 1.4 : 2;
  ctx.setLineDash(stepped ? [5, 4] : []);
  ctx.beginPath();
  let previous;
  for (const point of points) {
    if (!Number.isFinite(point[key])) {
      previous = undefined;
      continue;
    }
    const xx = x(point.time), yy = y(point[key]);
    if (previous) {
      if (stepped) ctx.lineTo(xx, y(previous[key]));
      ctx.lineTo(xx, yy);
    } else ctx.moveTo(xx, yy);
    previous = point;
  }
  ctx.stroke();
  if (previous) {
    ctx.beginPath();
    ctx.arc(x(previous.time), y(previous[key]), 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

export function drawResponseChart(canvas, { points, start, end }, { actual, target, unit, change, showTime, valve = false, compact = false }) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  const dpr = Math.min(2, devicePixelRatio);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.font = '11px ui-monospace, monospace';
  ctx.fillStyle = COLORS.label;

  const left = 64, right = w - 13, top = 10, bottom = h - (showTime ? 23 : 10);
  let targets = target ? points : [];
  if (change && target === 'sp') {
    const index = points.findIndex(({ time }) => time >= change.time);
    if (index !== -1) {
      targets = [...points.slice(0, index), { time: change.time, sp: change.from },
        { time: change.time, sp: change.to }, ...points.slice(index)];
    }
  }
  const values = [...points.map((point) => point[actual]), ...targets.map((point) => point[target])].filter(Number.isFinite);
  if (!values.length) {
    ctx.fillText(points.length ? '표시할 계측값이 없습니다.' : '모델 데이터 연결 중', 10, h / 2);
    return;
  }

  const max = Math.max(...values), min = Math.min(...values);
  const pad = Math.max((max - min) * 0.13, valve ? 1 : Math.max(Math.abs(max) * 0.00005, 0.022));
  const lo = valve ? Math.min(min, Math.max(0, min - pad)) : min - pad;
  const hi = valve ? Math.max(max, Math.min(100, max + pad)) : max + pad;
  const span = end - start || 1;
  const x = (time) => left + (time - start) / span * (right - left);
  const y = (value) => bottom - (value - lo) / (hi - lo) * (bottom - top);
  const ticks = compact ? 2 : valve ? 3 : 4;
  ctx.textAlign = 'right';
  ctx.strokeStyle = COLORS.grid;
  for (let i = 0; i < ticks; i++) {
    const value = lo + i / (ticks - 1) * (hi - lo);
    const yy = y(value);
    ctx.beginPath();
    ctx.moveTo(left, yy);
    ctx.lineTo(right, yy);
    ctx.stroke();
    ctx.fillText(valve || unit === '%' ? reading(value) + '%' : reading(value, unit), left - 7, yy + 3);
  }
  if (showTime) {
    ctx.textAlign = 'left';
    ctx.fillText(elapsed(start), left, h - 6);
    if (end > start) {
      ctx.textAlign = 'right';
      ctx.fillText(elapsed(end), right, h - 6);
    }
  }
  if (change) {
    const mark = x(change.time);
    ctx.strokeStyle = COLORS.change;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    ctx.moveTo(mark, top);
    ctx.lineTo(mark, bottom);
    ctx.stroke();
  }
  if (target) drawLine(ctx, targets, target, x, y, true);
  drawLine(ctx, points, actual, x, y, false);
}
