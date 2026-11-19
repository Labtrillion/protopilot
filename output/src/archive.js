import { byId, elapsed, option, reading } from './ui.js';
import { readColor } from './palette.js';

const chartColor = readColor('--plot-pv'), markerColor = readColor('--warning');

function draw(canvas, points, current) {
  const dpr = Math.min(devicePixelRatio, 2), width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const context = canvas.getContext('2d');
  context.scale(dpr, dpr);
  const values = points.map(([, value]) => value).filter(Number.isFinite);
  if (!values.length) return;
  const low = Math.min(...values), spread = Math.max(...values) - low;
  const first = points[0][0], span = points.at(-1)[0] - first;
  const x = (hours) => span ? 6 + (hours - first) / span * (width - 12) : width / 2;
  const y = (value) => spread ? height - 8 - (value - low) / spread * (height - 16) : height / 2;
  context.strokeStyle = chartColor;
  context.lineWidth = 1.5;
  context.beginPath();
  let started = false;
  for (const [hours, value] of points) {
    if (!Number.isFinite(value)) {
      started = false;
      continue;
    }
    context[started ? 'lineTo' : 'moveTo'](x(hours), y(value));
    started = true;
  }
  context.stroke();
  const [time, value] = current;
  if (Number.isFinite(value)) {
    context.fillStyle = markerColor;
    context.beginPath();
    context.arc(x(time), y(value), 4, 0, Math.PI * 2);
    context.fill();
  }
}

export function mountArchive(request, replay) {
  const details = byId('recording-archive'), run = byId('archived-run'), channel = byId('archived-channel');
  const preview = byId('archived-preview'), slider = byId('archived-frame'), canvas = byId('archived-chart');
  const replayButton = byId('archived-replay'), status = byId('archived-status');
  const files = byId('archived-links');
  let index = [], points = [], channels = new Map(), generation = 0;

  function render() {
    if (preview.hidden || !points.length) return;
    const current = points[Number(slider.value)];
    const [time, value] = current;
    const unit = channels.get(channel.value)?.unit ?? '';
    const label = `${elapsed(time)} · ${reading(value, unit)} ${unit}`;
    byId('archived-value').textContent = label;
    slider.setAttribute('aria-valuetext', label);
    draw(canvas, points, current);
  }

  function endpoint(part) {
    return `/api/recordings/${encodeURIComponent(run.value)}/${part}`;
  }

  async function select() {
    const token = ++generation;
    preview.hidden = files.hidden = true;
    if (!run.value) {
      status.textContent = '확인할 기록을 선택하세요.';
      return;
    }
    const meta = index.find((entry) => entry.run === run.value);
    const scenario = byId('archived-scenario');
    byId('archived-csv').href = endpoint('csv');
    byId('archived-json').href = endpoint('json');
    scenario.href = endpoint('scenario');
    scenario.hidden = replayButton.hidden = !meta.scenario;
    files.hidden = false;
    status.textContent = '기록을 읽는 중입니다.';
    try {
      const result = await request(`${endpoint('series')}?tag=${encodeURIComponent(channel.value)}`);
      if (token !== generation) return;
      points = result.points;
      slider.max = Math.max(0, points.length - 1);
      slider.value = slider.max;
      preview.hidden = !points.length;
      status.textContent = `${meta.sample.toLocaleString('ko-KR')} 샘플 · 미리보기 ${points.length}점`;
      render();
    } catch (error) {
      if (token === generation) status.textContent = error.message;
    }
  }

  async function refresh() {
    const token = ++generation;
    run.disabled = channel.disabled = true;
    run.innerHTML = '';
    preview.hidden = files.hidden = true;
    status.textContent = '기록 목록을 읽는 중입니다.';
    try {
      index = await request('/api/recordings');
      if (token !== generation) return;
      run.innerHTML = option('', '기록 선택') + index.map((entry) => option(entry.run, entry.run)).join('');
      run.disabled = channel.disabled = !index.length;
      status.textContent = index.length ? '확인할 기록을 선택하세요.' : '저장된 로컬 기록이 없습니다.';
    } catch (error) {
      if (token === generation) status.textContent = error.message;
    }
  }

  details.addEventListener('toggle', () => { if (details.open) refresh(); });
  run.addEventListener('change', select);
  channel.addEventListener('change', select);
  slider.addEventListener('input', render);
  new ResizeObserver(render).observe(canvas);
  replayButton.addEventListener('click', async () => {
    replayButton.disabled = true;
    try {
      await replay(await request(endpoint('scenario')));
    } catch (error) {
      status.textContent = error.message;
    } finally {
      replayButton.disabled = false;
    }
  });
  return {
    init(items) {
      channels = new Map(items.map((item) => [item.id, item]));
      channel.innerHTML = items.map((item) => option(item.id, `${item.id} · ${item.label}`)).join('');
      channel.value = 'XMEAS(7)';
    },
    refresh,
  };
}
