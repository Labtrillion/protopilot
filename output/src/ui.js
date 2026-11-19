import { AREAS, CIRCUITS, MEDIA, STREAMS, codeFor } from './topology.js';
import { readColor } from './palette.js';
import { SAMPLES } from './schematic.js';

export const byId = (id) => document.getElementById(id);
export const escape = (text = '') => String(text).replace(/[&<>"']/g, (char) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const number = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 });
const fine = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 5 });
export function reading(value, unit) {
  if (!Number.isFinite(value)) return '—';
  return (unit === 'mol fraction' || (Math.abs(value) < 1 && value !== 0) ? fine : number).format(value);
}
export function pressureReading(status, tag) {
  const value = reading(status.values[tag], 'kPa(g)');
  const alarm = status.alarms.find((item) => item.tag === tag);
  const state = [alarm && '알람', tag in status.offsets && '시험'].filter(Boolean).join(' · ');
  return { value, alarm, state };
}
export function flowReading(status, stream) {
  const tag = codeFor(stream.tag);
  return `${reading(status.values[tag])} ${stream.media === 'gas' ? 'kscm/h' : 'm³/h'}${tag in status.offsets ? ' · 시험' : ''}`;
}
export function elapsed(hours) {
  const seconds = Math.round((hours ?? 0) * 3600);
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map((value) => String(value).padStart(2, '0')).join(':');
}

const icons = {
  play: '<path d="m8 5 12 7-12 7z"/>', pause: '<path d="M9 5H6v14h3zm9 0h-3v14h3z"/>',
  reset: '<path d="M3 12a9 9 0 1 1 3 6.7M3 12V5m0 7h7"/>',
  sliders: '<path d="M4 7h7m4 0h5M4 17h3m4 0h9"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  flow: '<path d="M3 8h17m-4-4 4 4-4 4M21 16H4m4-4-4 4 4 4"/>',
  gauge: '<path d="M4 18a9 9 0 1 1 16 0zm8-4 4-5"/>',
  scan: '<path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3m0 8v3a2 2 0 0 1-2 2h-3m-8 0H5a2 2 0 0 1-2-2v-3m9-7v6m-3-3h6"/>',
  diagram: '<rect x="3" y="4" width="6" height="5" rx="1"/><rect x="15" y="15" width="6" height="5" rx="1"/><path d="M6 9v8h9m-3-3 3 3-3 3M9 6h9v6"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5zm-9 9 9 5 9-5m-18 4 9 5 9-5"/>',
  tag: '<path d="m3 4 8-1 10 10-8 8L3 11z"/><circle cx="8" cy="8" r="1"/>',
  box: '<path d="m12 3 9 5-9 5-9-5zm-9 5v8l9 5 9-5V8m-9 5v8"/>',
  activity: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>',
  search: '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 5 5"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 12a2 2 0 0 0 4 0"/>',
  bolt: '<path d="m13 2-9 12h7l-1 8 10-12h-8z"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  arrow: '<path d="M6 18 18 6M9 6h9v9"/>',
};
export function icon(name) {
  return `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${icons[name] ?? ''}</svg>`;
}
export function initIcons() {
  for (const el of document.querySelectorAll('[data-icon]')) el.insertAdjacentHTML('afterbegin', icon(el.dataset.icon));
}
export const option = (id, label) => `<option value="${escape(id)}">${escape(label)}</option>`;

export function legend(circuit) {
  const media = circuit ? Object.keys(MEDIA).filter((kind) => STREAMS.some((line) => line.circuit === circuit && line.media === kind)) : Object.keys(MEDIA);
  byId('legend').innerHTML = media.map((key) => {
    const { colorToken, name } = MEDIA[key];
    return `<span><i style="--medium:var(${colorToken})"></i>${name}</span>`;
  }).join('');
}

const trendColor = readColor('--plot-sparkline');
const channelTrend = document.createElement('canvas');
channelTrend.className = 'channel-trend';
channelTrend.width = 88;
channelTrend.height = 20;
channelTrend.setAttribute('role', 'img');
channelTrend.setAttribute('aria-label', '선택한 계측의 최근 추세');
channelTrend.title = '최근 수신값 추세';

function renderChannelTrend(cell, series) {
  if (!cell || series.length < 2) {
    channelTrend.remove();
    return;
  }
  if (channelTrend.parentElement !== cell) cell.append(channelTrend);
  const ctx = channelTrend.getContext('2d');
  const { width: w, height: h } = channelTrend;
  ctx.clearRect(0, 0, w, h);
  const min = Math.min(...series), max = Math.max(...series), spread = max - min;
  ctx.strokeStyle = trendColor;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  series.forEach((value, i) => {
    const x = 2 + i * (w - 4) / (series.length - 1);
    const y = spread ? h - 3 - (value - min) / spread * (h - 6) : h / 2;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

export function renderPipeDetails(areaKey, circuit) {
  const root = byId('pipe-details-content');
  const view = circuit || areaKey;
  if (root.dataset.view === view) return;
  root.dataset.view = view;
  const title = byId('pipe-details-title');
  const route = CIRCUITS[circuit];
  title.textContent = route ? `${route.title} 경로` : '연결 배관';
  if (route) {
    const paths = route.paths.map((path) => `<li>${path.map((item) => `<span>${escape(item)}</span>`).join('<i aria-hidden="true">→</i>')}</li>`).join('');
    root.innerHTML = `<ul class="circuit-paths" role="list">${paths}</ul><p class="circuit-note">${escape(route.info)}</p>`;
    return;
  }
  if (areaKey === 'plant') {
    root.innerHTML = '<p class="pipe-empty">공정도나 실시간 계측에서 설비를 선택하세요.</p>';
    return;
  }
  const { code, name } = AREAS[areaKey];
  const sampleId = SAMPLES[areaKey]?.[0];
  const streams = STREAMS.filter(({ id, connectedAreas }) => connectedAreas.includes(areaKey) || id === sampleId);
  const routes = streams.map(({ id, media, from, to }) => {
    const path = id === sampleId ? `시료 채취점 → ${code} 시료 입구` : `${from} → ${to}`;
    return `<li class="inspect-stream"><span class="line-id"><i aria-hidden="true" style="--medium:var(${MEDIA[media].colorToken})"></i>${escape(id)}</span><span>${escape(path)}</span></li>`;
  }).join('');
  root.innerHTML = `<p class="pipe-equipment"><code>${escape(code)}</code><span>${escape(name)}</span></p>
    ${routes ? `<ul class="inspect-streams" role="list">${routes}</ul>` : '<p class="pipe-empty">표시할 연결 배관이 없습니다.</p>'}`;
}

export function renderRows(channels, status, selected, recentReadings) {
  const holder = byId('rows');
  if (!channels.length) {
    holder.innerHTML = '<tr><td colspan="3" class="empty-cell">검색 조건에 해당하는 채널이 없습니다.</td></tr>';
    return;
  }
  if (holder.children.length !== channels.length || channels.some((channel, i) => holder.children[i].dataset.tag !== channel.id)) {
    holder.innerHTML = channels.map((ch) => `<tr data-tag="${escape(ch.id)}">
      <td class="tag-cell"><button type="button" data-tag="${escape(ch.id)}" title="${escape(ch.description)}">
        <span class="channel-name">${escape(ch.label)}</span><span class="channel-id"><code>${escape(ch.id)}</code> <span>${AREAS[ch.area].code}</span></span>
      </button></td><td class="right numeric"><span></span><small class="channel-unit">${escape(ch.unit)}</small></td>
      <td class="channel-state"></td></tr>`).join('');
  }
  const active = new Set((status?.alarms ?? []).map((alarm) => alarm.tag));
  let trendCell;
  channels.forEach((ch, i) => {
    const tr = holder.children[i];
    const isAlarm = active.has(ch.id);
    const isOffset = ch.id in (status?.offsets ?? {});
    const isSelected = selected === ch.id;
    const manual = ch.id in (status?.manual ?? {});
    tr.classList.toggle('selected-row', isSelected);
    tr.cells[0].firstElementChild.setAttribute('aria-current', isSelected);
    if (isSelected) trendCell = tr.cells[1];
    tr.classList.toggle('alarm-row', isAlarm);
    tr.classList.toggle('tested-row', isOffset);
    const [value, unit] = tr.cells[1].children;
    value.textContent = reading(status?.values[ch.id], ch.unit);
    unit.textContent = `${ch.unit ?? ''}${isOffset ? `${ch.unit ? ' · ' : ''}시험` : ''}`;
    tr.cells[1].title = isOffset ? `센서 시험 표시값 · 원계측 ${reading(status.native_values?.[ch.id], ch.unit)} ${ch.unit ?? ''} · 오프셋 ${reading(status.offsets[ch.id])}` : '';
    const pending = manual && status.manual[ch.id] !== status.values[ch.id];
    const base = status?.limited_manual?.includes(ch.id) ? '보호 제어 제한' : pending ? '명령 대기' : manual ? '수동' : ch.period ? `${ch.period} h 샘플` : ch.group.endsWith('monitors') ? '진단' : '정상';
    const flags = [isAlarm && '경보', isOffset && '시험', isSelected && '추적'].filter(Boolean);
    // Manual / pending-command state stays visible next to alarm, test and tracking flags.
    tr.cells[2].textContent = (flags.length && !manual ? flags : [...flags, base]).join(' · ');
    if (ch.period) tr.cells[2].title = `갱신 ${ch.period} h / 지연 ${ch.delay} h`;
  });
  renderChannelTrend(trendCell, trendCell ? recentReadings.map((values) => values[selected]).filter(Number.isFinite) : []);
}

