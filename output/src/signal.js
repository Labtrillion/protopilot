import { AREAS, CORE_PRESSURES, STREAMS, codeFor, streamMatchesTag } from './topology.js';
import { byId, elapsed, reading } from './ui.js';

const PRESSURE_TAGS = new Set(Object.values(CORE_PRESSURES).map(codeFor));
const LEVEL_TAGS = new Set([8, 12, 15].map(codeFor));
const TRACE_LENGTH = 60;

export function signalTag(signal) {
  return signal?.item.pv ?? signal?.item.id ?? '';
}

function signalValue({ kind, item }, status) {
  if (kind === 'setpoint') return status.setpoints[item.id];
  if (kind === 'mv') return status.manual[item.id] ?? status.values[item.id];
  if (kind === 'disturbance') return Number(status.disturbances.includes(item.id));
  return status.values[item.id];
}

function describe(signal, status, format) {
  const { kind, item } = signal;
  const tag = signalTag(signal);
  const paused = status.running ? '' : ' · 재개 후 물리 응답';
  const route = STREAMS.filter((line) => streamMatchesTag(line, tag));
  const routeNames = route.map((line) => `#${line.id}`).join(' · ');
  const offset = status.offsets[tag];
  const measured = status.values[tag];
  const native = status.native_values[tag];

  if (kind === 'mv') {
    const position = status.actuators?.[tag];
    const mode = tag in status.manual ? '수동' : '자동';
    const location = routeNames ? `${routeNames} 밸브` : '반응기 교반기';
    const actual = Number.isFinite(position) ? `실제 ${format(position)}` : '실제 위치 데이터 없음';
    const pending = tag in status.manual && status.manual[tag] !== measured;
    return [location, `${mode} 제어 · ${actual}${paused}`, pending ? '조작 명령 · 적용 대기' : '조작 명령'];
  }
  if (kind === 'setpoint') {
    const location = LEVEL_TAGS.has(tag) ? '설비 액위 목표선' : `${tag} 실측 추세`;
    return [location, `표시 PV ${format(measured)}${paused}`, 'SP 목표'];
  }
  if (kind === 'disturbance') return ['시뮬레이션 외란', `외란 상태가 모델 입력에 반영됩니다${paused}`, '적용 상태'];
  if (kind === 'limit') {
    const [low, high] = status.limits[tag] ?? [];
    return ['알람 기준', `하한 ${format(low)} / 상한 ${format(high)}`, '현재 계측'];
  }

  const location = PRESSURE_TAGS.has(tag) ? '설비 압력 표시' : LEVEL_TAGS.has(tag) ? '설비 액면' : routeNames ? `${routeNames} 배관 유량` : '설비 계측 표시';
  const offsetInfo = `원계측 ${format(native)} · 오프셋 ${offset > 0 ? '+' : ''}${format(offset)}`;
  if (offset != null) return [location, `${offsetInfo}${routeNames || LEVEL_TAGS.has(tag) ? ' · 모델은 원계측 사용' : ''}`, '센서 시험 표시값'];
  if (routeNames) return [location, '유량에 따라 화살표 속도가 변합니다.', '현재 계측'];
  if (item.period) return [location, `분석기 갱신 ${item.period} h · 지연 ${item.delay} h`, '현재 계측'];
  return [location, PRESSURE_TAGS.has(tag) || LEVEL_TAGS.has(tag) ? '' : '선택한 태그를 공정 화면에 표시합니다.', '현재 계측'];
}

function drawTrace(canvas, values, min, max) {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  const dpr = Math.min(2, devicePixelRatio);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext('2d');
  if (!ctx || !values.length) return;
  ctx.scale(dpr, dpr);
  const x = (i) => values.length === 1 ? width - 4 : 4 + i / (values.length - 1) * (width - 8);
  const y = (value) => min === max ? height / 2 : height - 5 - (value - min) / (max - min) * (height - 10);
  ctx.strokeStyle = '#2c414a';
  ctx.beginPath();
  for (const row of [5, height / 2, height - 5]) {
    ctx.moveTo(0, row);
    ctx.lineTo(width, row);
  }
  ctx.stroke();
  ctx.strokeStyle = ctx.fillStyle = '#f0cf82';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  let contiguous = false;
  values.forEach((value, i) => {
    if (!Number.isFinite(value)) {
      contiguous = false;
      return;
    }
    if (contiguous) ctx.lineTo(x(i), y(value));
    else ctx.moveTo(x(i), y(value));
    contiguous = true;
  });
  ctx.stroke();
  if (contiguous) {
    ctx.beginPath();
    ctx.arc(x(values.length - 1), y(values.at(-1)), 2.6, 0, Math.PI * 2);
    ctx.fill();
  }
}

export function mountSignal() {
  const nodes = Object.fromEntries(['frame', 'detail', 'tag', 'name', 'value-label', 'value', 'previous', 'trace-count', 'trace-range',
    'chart', 'location', 'effect', 'response', 'locate'].map((name) => [name, byId(`signal-${name}`)]));
  const stage = byId('stage-signal'), stageTag = byId('stage-signal-tag'), stageValue = byId('stage-signal-value');
  const history = [];
  let selection, status, connected;

  function render() {
    nodes.frame.textContent = status ? `${connected && !status.error ? '샘플' : '마지막 샘플'} #${status.sample.toLocaleString('ko-KR')} · ${elapsed(status.time_hours)}` : '로컬 샘플 연결 중';
    stage.hidden = nodes.detail.hidden = !selection || !status;
    if (!selection || !status) return;

    const { item, kind } = selection;
    const tag = signalTag(selection);
    const format = (value) => {
      if (kind === 'disturbance') return value === 1 ? '적용' : value === 0 ? '해제' : '—';
      return Number.isFinite(value) ? `${reading(value, item.unit)}${item.unit ? ` ${item.unit}` : ''}` : '—';
    };
    const values = history.map((frame) => signalValue(selection, frame));
    const current = signalValue(selection, status);
    const previous = values.at(-2);
    const [location, effect, label] = describe(selection, status, format);
    const delta = current - previous;
    const signed = format(delta);
    const sameReading = format(current) === format(previous);
    const alarming = status.alarms.some((alarm) => alarm.tag === tag);
    const code = AREAS[item.area].code;
    nodes.tag.textContent = tag;
    nodes.name.textContent = item.label;
    nodes['value-label'].textContent = alarming ? `${label} · 경보` : label;
    nodes.value.textContent = format(current);
    const change = Number.isFinite(current) && !sameReading && kind !== 'disturbance' ? ` · ${delta > 0 ? '+' : ''}${signed}` : '';
    nodes.previous.textContent = Number.isFinite(previous) ? `직전 ${format(previous)}${change}` : '직전 수신값 없음';
    nodes.location.textContent = `${code} · ${location}`;
    nodes.effect.textContent = effect;
    nodes.effect.hidden = !effect;
    nodes.response.hidden = kind !== 'setpoint';
    nodes.locate.hidden = item.area === 'plant';
    stage.classList.toggle('alarming', alarming);
    stageTag.textContent = `${code} · ${tag}${kind === 'setpoint' ? ' SP' : kind === 'mv' ? ' 명령' : ''}`;
    stageValue.textContent = format(current);

    const numeric = values.filter(Number.isFinite);
    const min = Math.min(...numeric), max = Math.max(...numeric);
    nodes['trace-count'].textContent = `${values.length}점`;
    nodes['trace-range'].textContent = min === max ? format(min) : numeric.length ? `${format(min)} ~ ${format(max)}` : '—';
    drawTrace(nodes.chart, values, min, max);
  }

  new ResizeObserver(render).observe(byId('signal-trace'));
  return {
    select(next) { selection = next; render(); },
    update(next, online) {
      if (next && next.run !== status?.run) history.length = 0;
      if (next && next !== status) {
        const change = next.events[0];
        const newChange = change?.kind === 'change' && change.id !== status?.events[0]?.id;
        if (next.sample !== status?.sample || !history.length || newChange) {
          history.push(next);
          if (history.length > TRACE_LENGTH) history.shift();
        }
      }
      status = next;
      connected = online;
      render();
    },
  };
}
