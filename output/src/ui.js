import { AREAS, CIRCUITS, CORE_PRESSURES, MEDIA, STREAMS, codeFor } from './topology.js';

export const byId = (id) => document.getElementById(id);
export const escape = (text = '') => String(text).replace(/[&<>"']/g, (char) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const number = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 });
const fine = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 5 });
export function reading(value, unit) {
  if (!Number.isFinite(value)) return '—';
  return (unit === 'mol fraction' || (Math.abs(value) < 1 && value !== 0) ? fine : number).format(value);
}
export function elapsed(hours) {
  const seconds = Math.round((hours ?? 0) * 3600);
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map((value) => String(value).padStart(2, '0')).join(':');
}
const time = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
export const wallTime = (stamp) => time.format(new Date(stamp));

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
  muted: '<path d="M3 9h4l5-4v14l-5-4H3zm13 0 5 6m0-6-5 6"/>',
  sound: '<path d="M3 9h4l5-4v14l-5-4H3zm13 0a4 4 0 0 1 0 6m2.5-8.5a7.7 7.7 0 0 1 0 11"/>',
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

export function legend(circuit, interiors) {
  const media = circuit ? Object.keys(MEDIA).filter((kind) => STREAMS.some((line) => line.circuit === circuit && line.media === kind)) : Object.keys(MEDIA);
  byId('legend').innerHTML = media.map((key) => {
    const { color, name } = MEDIA[key];
    return `<span><i style="--medium:${color}"></i>${name}</span>`;
  }).join('') + (interiors ? '<span><i class="level-key"></i>액위 SP</span>' : '');
}

export function renderPressures(status, enabled, tracked = '') {
  const root = byId('pressure-overview');
  root.hidden = !enabled;
  if (!enabled) return;
  if (!root.children.length) root.innerHTML = Object.entries(CORE_PRESSURES).map(([area, tag]) =>
    `<button type="button" data-pressure-tag="${codeFor(tag)}" data-area="${area}" title="${AREAS[area].name} · ${codeFor(tag)}">
      <span>${AREAS[area].code}</span><b>—</b></button>`).join('');
  for (const row of root.children) {
    const tag = row.dataset.pressureTag;
    const alarming = status?.alarms.some((alarm) => alarm.tag === tag);
    const value = status?.values[tag];
    const formatted = reading(value, 'kPa(g)');
    row.querySelector('b').innerHTML = `${formatted}<small>kPa(g)</small>`;
    row.classList.toggle('alarming', alarming);
    row.toggleAttribute('data-tracked', tag === tracked);
    row.setAttribute('aria-current', tag === tracked);
  }
}

function routeDetails(circuit) {
  if (!circuit) return '';
  const route = CIRCUITS[circuit];
  const paths = route.paths.map((path) => `<li>${path.map((item) => `<span>${escape(item)}</span>`).join('<i aria-hidden="true">→</i>')}</li>`).join('');
  return `<section class="route-inspector" style="--route-color:${route.color}">
    <h3>${route.title} 경로</h3><ul>${paths}</ul><p>${route.info}</p></section>`;
}

function drawTrend(canvas, values, index) {
  const ctx = canvas.getContext('2d');
  const series = values.filter(Number.isFinite);
  const { width: w, height: h } = canvas;
  ctx.clearRect(0, 0, w, h);
  if (series.length < 2) return;
  const min = Math.min(...series), max = Math.max(...series), spread = max - min || 1;
  ctx.strokeStyle = ['#69d1c9', '#91b9f2', '#e5ba7b', '#69d1c9'][index];
  ctx.lineWidth = 2;
  ctx.beginPath();
  series.forEach((value, i) => ctx.lineTo(i * (w - 2) / (series.length - 1) + 1, h - 4 - (value - min) / spread * (h - 8)));
  ctx.stroke();
}

const kpis = [[7, '반응기 압력'], [9, '반응기 온도'], [6, '반응기 입구 유량'], [17, '제품 유량']];
export function renderKpis(status, metadata, trends, tracked) {
  const root = byId('kpis');
  if (!root.children.length) root.innerHTML = kpis.map(([i, label]) =>
    `<button type="button" data-tag="${codeFor(i)}"><span>${label}</span><b>—</b><small></small><canvas width="88" height="34" aria-hidden="true"></canvas></button>`).join('');
  [...root.children].forEach((card, i) => {
    const tag = card.dataset.tag;
    const { unit } = metadata.get(tag);
    card.querySelector('b').textContent = reading(status.values[tag], unit);
    card.querySelector('small').textContent = unit;
    card.classList.toggle('alarming', status.alarms.some((event) => event.tag === tag));
    card.toggleAttribute('data-tracked', tag === tracked);
    drawTrend(card.querySelector('canvas'), trends.get(tag) ?? [], i);
  });
}

export function renderInspector(areaKey, status, metadata, circuit = '', setpoint = null, control = null, tracked = '') {
  const area = AREAS[areaKey];
  const values = status?.values ?? {};
  const root = byId('inspector-content');
  const panelKey = [areaKey, circuit, setpoint?.id, control?.id, metadata.size].join('/');
  if (root.dataset.area !== panelKey) {
    const rows = [...area.tags.map(codeFor), ...(area.extra ?? [])].map((tag) => {
      const channel = metadata.get(tag);
      return `<button class="inspect-value" type="button" data-tag="${tag}" title="${tag} · ${escape(channel?.label ?? tag)}">
        <span>${escape(channel?.label ?? tag)}</span><b><span>—</span> <small>${escape(channel?.unit ?? '')}</small></b></button>`;
    }).join('');
    const streams = STREAMS.filter((stream) => circuit ? stream.circuit === circuit : stream.area === areaKey && areaKey !== 'plant');
    const flows = streams.map((stream) => {
      const tag = codeFor(stream.tag);
      return `<div class="inspect-stream"><span class="line-id"><i style="--medium:${MEDIA[stream.media].color}"></i>${stream.id}</span>
        <span><b>${escape(stream.from)} → ${escape(stream.to)}</b>${tag ? `<small data-flow-tag="${tag}"></small>` : '<small>흐름 방향</small>'}</span></div>`;
    }).join('');
    root.innerHTML = `${routeDetails(circuit)}<div class="inspector-label"><code>${area.code}</code><span>${area.en}</span></div>
      ${setpoint ? `<button type="button" class="inspect-setting" data-response="${setpoint.id}"><span>${escape(setpoint.label)} · SP 응답</span><b></b></button>` : ''}
      ${control ? `<button type="button" class="inspect-setting" data-control="${control.id}"><span>${escape(control.label)}</span><b></b></button>` : ''}
      <div class="inspect-values">${rows}</div>${flows ? `<h3>연결 배관</h3><div class="inspect-streams">${flows}</div>` : ''}`;
    root.dataset.area = panelKey;
    root.scrollTop = 0;
  }
  if (setpoint) root.querySelector('[data-response] b').textContent = `${reading(status?.setpoints?.[setpoint.id])} ${setpoint.unit}`;
  if (control) {
    const current = status?.actuators?.[control.id];
    const command = status?.manual[control.id] ?? values[control.id];
    root.querySelector('[data-control] b').textContent = `실제 ${reading(current)}% · 명령 ${reading(command)}%`;
  }
  for (const button of root.querySelector('.inspect-values').children) {
    const tag = button.dataset.tag;
    button.querySelector('b span').textContent = reading(values[tag], metadata.get(tag)?.unit);
    button.toggleAttribute('data-tracked', tag === tracked);
    button.setAttribute('aria-current', tag === tracked);
  }
  for (const flow of root.querySelectorAll('[data-flow-tag]')) {
    const tag = flow.dataset.flowTag;
    const unit = metadata.get(tag)?.unit;
    flow.textContent = `${reading(values[tag], unit)}${unit ? ` ${unit}` : ''}`;
  }
}

export function renderRows(channels, status, selected) {
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
  channels.forEach((ch, i) => {
    const tr = holder.children[i];
    const isAlarm = active.has(ch.id);
    const isOffset = ch.id in (status?.offsets ?? {});
    const manual = ch.id in (status?.manual ?? {});
    tr.classList.toggle('selected-row', selected === ch.id);
    if (selected === ch.id) tr.cells[0].firstElementChild.setAttribute('aria-current', 'true');
    else tr.cells[0].firstElementChild.removeAttribute('aria-current');
    tr.classList.toggle('alarm-row', isAlarm);
    tr.cells[1].firstElementChild.textContent = reading(status?.values[ch.id], ch.unit);
    const pending = manual && status.manual[ch.id] !== status.values[ch.id];
    const state = isAlarm ? '경보' : isOffset ? '시험' : pending ? '명령 대기' : manual ? '수동' : ch.period ? `${ch.period} h 샘플` : ch.group.endsWith('monitors') ? '진단' : '정상';
    tr.cells[2].textContent = state;
    if (ch.period) tr.cells[2].title = `갱신 ${ch.period} h / 지연 ${ch.delay} h`;
  });
}

const KIND = { warning: '경고', trip: '트립', change: '조작', clear: '복구', ack: '확인' };
export function renderAlarms(status, tab, metadata) {
  const events = tab === 'active' ? status?.alarms : status?.events;
  const panel = byId('events');
  const count = status?.alarms.length ?? 0;
  byId('alarm-count').textContent = count;
  byId('alarm-count').classList.toggle('alarming', count > 0);
  const focused = document.activeElement;
  const focusKey = panel.contains(focused) && (focused.dataset.ack ?? focused.dataset.eventId);
  const focusAttribute = focused.dataset.ack ? 'data-ack' : 'data-event-id';
  const items = !events?.length ? `<div class="alarm-empty">${icon('check')}<b>${tab === 'active' ? '활성 알람 없음' : '기록된 이벤트 없음'}</b>
      <span>${tab === 'active' ? '새 알람이 발생하면 관련 설비로 이동합니다.' : '공정 조작과 알람 내역이 여기에 표시됩니다.'}</span></div>`
    : events.map((e) => {
      const unit = metadata.get(e.tag)?.unit;
      const detail = [e.tag, e.value == null ? '' : `${reading(e.value, unit)} ${unit ?? ''}`,
        e.limit == null ? '' : `기준 ${reading(e.limit)}`, e.source].filter(Boolean).join(' · ');
      return `<article class="event ${e.kind}"><button type="button" class="event-open" data-event-id="${e.id}" data-event-area="${e.area}" data-event-tag="${escape(e.tag ?? '')}">
        <span class="event-head"><span class="event-kind">${KIND[e.kind]}</span><time datetime="${e.time_utc}">${wallTime(e.time_utc)}</time></span>
        <span class="event-title">${escape(e.label)}${icon('arrow')}</span><span class="event-detail">${escape(detail)}</span>
        </button>
        ${tab === 'active' ? `<button type="button" class="ack" data-ack="${e.id}" ${e.ack ? 'disabled' : ''}>${e.ack ? '확인됨' : '알람 확인'}</button>` : ''}
      </article>`;
    }).join('');
  if (panel.dataset.html !== items) {
    panel.innerHTML = items;
    panel.dataset.html = items;
    if (focusKey) panel.querySelector(`[${focusAttribute}="${focusKey}"]:not(:disabled)`)?.focus({ preventScroll: true });
  }
}
