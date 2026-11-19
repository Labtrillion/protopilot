import { AREAS, FEED_TAGS, codeFor } from './topology.js';
import { mountPlantView } from './plant-view.js';
import { mountResponse } from './response.js';
import { mountSetpointControls, setupControls } from './controls.js';
import { mountScenario } from './scenario.js';
import {
  byId, elapsed, icon, initIcons, option, renderCircuitRoute, renderInspector, renderRows,
} from './ui.js';

// Representative closed loop per unit; used only when the server defines it for that unit.
const REPRESENTATIVE_LOOP = { reactor: 'reactor_pressure', separator: 'separator_level', stripper: 'stripper_level' };
// Channel tab first screen: gas pressure, level, temperature, inlet/product/purge flow of the main units.
const PRIMARY_TAGS = [7, 13, 16, 8, 12, 15, 9, 11, 18, 6, 17, 10].map(codeFor);
const FEED_FLOW_TAGS = new Set(Object.values(FEED_TAGS));
const TREND_LENGTH = 60;

const channels = new Map();
const recentReadings = [];
const reset = byId('reset'), run = byId('run'), speed = byId('speed');
const connection = byId('connection');
const pageId = crypto.randomUUID();
let plantView, openControls, status, noticeTimer, pageRecorded = false;
let setpoints = [], telemetryView = 'values';
let focusedArea = 'plant', selection, connected = false;
let visibleChannels = [], channelScope = 'primary', explicitTag = '';

const response = mountResponse(trackSetpoint);
let latestSetpointChange = 0;
const setpointControls = mountSetpointControls((id, value) => {
  const change = ++latestSetpointChange;
  const runId = status.run;
  const previousSelection = selection, previousArea = focusedArea, previousView = telemetryView;
  return doCommand({ op: 'setpoint', id, value }, () => {
    if (change === latestSetpointChange && status.run === runId && selection === previousSelection
      && focusedArea === previousArea && telemetryView === previousView) trackSetpoint(id);
  });
});
const scenario = mountScenario(request, async (body) => {
  try {
    return await command(body);
  } catch (error) {
    if (body.op === 'controller') note(error.message, 'danger');
    else throw error;
  }
}, () => {
  explicitTag = '';
  follow({ kind: 'channel', item: channels.get('XMEAS(7)') });
  focus('plant', '', true);
  note('시나리오를 불러왔습니다. 실행을 누르면 처음부터 재현합니다.');
});

const loopForTag = (tag) => setpoints.find((item) => item.pv === tag);
// A general tag keeps an open SP view only when its loop belongs to the unit being viewed
// (e.g. XMEAS(17) or XMEAS(40) tracked under T-101 must not keep a product-area loop open).
const unitLoop = (tag, area) => {
  const loop = loopForTag(tag);
  return loop?.area === area ? loop : null;
};
function loopFor(area, tag = '') {
  const own = loopForTag(tag);
  if (own && own.area === area) return own;
  return setpoints.find((item) => item.id === REPRESENTATIVE_LOOP[area] && item.area === area)
    ?? setpoints.find((item) => item.area === area);
}
const channelSelection = (channel) => ({ kind: channel.group === 'manipulated_variables' ? 'mv' : 'channel', item: channel });
const tagForSelection = (target) => target?.item.pv ?? target?.item.id ?? '';

function activeSetpoint() {
  return selection?.kind === 'setpoint' ? selection.item
    : loopForTag(tagForSelection(selection))
      ?? (focusedArea === 'plant' ? setpoints.find((item) => item.id === 'reactor_pressure') : loopFor(focusedArea))
      ?? setpoints.find((item) => item.id === response.selected()) ?? setpoints[0];
}

function showTelemetry(view, setpoint) {
  // Leaving the SP view keeps tracking the loop's measured PV instead of a stale SP selection.
  if (view === 'values' && selection?.kind === 'setpoint' && channels.has(selection.item.pv)) {
    follow(channelSelection(channels.get(selection.item.pv)));
  }
  telemetryView = view;
  const show = view === 'response';
  byId('channel-view').hidden = show;
  byId('response-view').hidden = !show;
  byId('tab-values').setAttribute('aria-pressed', !show);
  byId('tab-response').setAttribute('aria-pressed', show);
  response.show(show, setpoint);
}

function renderChannels() {
  renderRows(visibleChannels, status, tagForSelection(selection), recentReadings);
}

// With the SP tab open the central tracking and the response loop are the same loop;
// a signal without a loop returns the panel to the channel view.
function syncResponse(loop) {
  if (telemetryView !== 'response') return;
  if (loop) {
    follow({ kind: 'setpoint', item: loop });
    response.show(true, loop.id);
  } else showTelemetry('values');
}

function closeResponse() {
  if (telemetryView === 'response') showTelemetry('values');
  if (!filtersActive()) setScope('primary');
}

// Track a tag in the current unit without moving the camera; a part of another unit
// (e.g. clicked in the process overview) selects that unit as well.
function trackTag(tag, area = '') {
  const channel = channels.get(tag);
  if (!channel) return;
  if (area && area !== focusedArea) {
    focus(area, tag);
    return;
  }
  explicitTag = tag;
  follow(channelSelection(channel));
  syncResponse(unitLoop(tag, focusedArea));
  renderChannels();
}

function follow(next) {
  selection = next;
  plantView?.track(next.item.area, tagForSelection(next));
}

function focus(area, tag = '', resetCamera = false, next) {
  if (!(area in AREAS)) return;
  const moved = area !== focusedArea;
  const channel = channels.get(tag || codeFor(AREAS[area].tags[0]));
  if (next) follow(next);
  // The process overview tracks reactor pressure (AREAS.plant.tags[0]) unless the user picked a tag.
  else if (channel && (tag || area !== 'plant' || !explicitTag)) follow(channelSelection(channel));
  explicitTag = tag || (next ? tagForSelection(next) : area === 'plant' ? explicitTag : '');
  focusedArea = area;
  if (moved) renderInspector(area);
  if (moved || resetCamera || tag.startsWith('XMV(') || FEED_FLOW_TAGS.has(tag)) {
    plantView?.focus(area, resetCamera, tag);
  }
  if (next?.kind !== 'setpoint' && telemetryView === 'response') {
    if (area === 'plant') closeResponse();
    else syncResponse(tag ? unitLoop(tag, area) : loopFor(area));
  }
  renderChannels();
}

function trackSetpoint(id) {
  const item = setpoints.find((entry) => entry.id === id);
  if (!item) return;
  focus(item.area, item.pv, false, { kind: 'setpoint', item });
  showTelemetry('response', id);
}

function note(message, level = 'ok') {
  const box = byId('notice');
  box.textContent = message;
  box.dataset.level = level;
  box.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { box.hidden = true; }, 4500);
}

const filtersActive = () => Boolean(byId('search').value.trim() || byId('channel-group').value || byId('channel-area').value);

function setScope(scope) {
  channelScope = scope;
  filter();
}

function filter() {
  const group = byId('channel-group').value;
  const area = byId('channel-area').value;
  const query = byId('search').value.toLowerCase().replace(/[\s()]/g, '');
  // Any search or filter always looks across every channel, never only the primary summary.
  const primary = channelScope === 'primary' && !filtersActive();
  visibleChannels = primary ? PRIMARY_TAGS.map((tag) => channels.get(tag)).filter(Boolean) : [...channels.values()].filter((ch) => {
    const text = `${ch.id} ${ch.name} ${ch.label} ${ch.description} ${AREAS[ch.area].code}`.toLowerCase().replace(/[\s()]/g, '');
    return (!group || group === ch.group) && (!area || area === ch.area) && text.includes(query);
  });
  byId('scope-primary').setAttribute('aria-pressed', primary);
  byId('scope-all').setAttribute('aria-pressed', !primary);
  byId('matches').textContent = primary ? `주요 ${visibleChannels.length}개` : `${visibleChannels.length} / ${channels.size}개`;
  renderChannels();
}

function renderConnection() {
  scenario.update(status, connected);
  setpointControls.update(status, connected);
  const kind = !connected || status?.error ? 'offline' : status?.shutdown.terminated ? 'trip' : status?.running ? 'live' : 'paused';
  const labels = { offline: '연결 끊김', trip: '모델 트립', live: '시뮬레이션 실행 중', paused: '일시정지' };
  connection.className = `status ${kind}`;
  connection.innerHTML = `<i></i> ${status?.error ?? labels[kind]}`;
  run.disabled = !connected || !!status?.error || !!status?.shutdown.terminated;
  reset.disabled = !connected;
  byId('open-controls').disabled = !connected;
  byId('tab-response').disabled = !setpoints.length;
  response.enable(connected);
}

function receive(next) {
  const previous = status;
  const runChanged = previous?.run !== next.run;
  const sampled = runChanged || previous?.sample !== next.sample;
  status = next;
  connected = true;
  if (runChanged) recentReadings.length = 0;
  if (sampled) {
    recentReadings.push(next.values);
    if (recentReadings.length > TREND_LENGTH) recentReadings.shift();
  }
  renderConnection();
  byId('clock').textContent = elapsed(status.time_hours);
  if (previous?.running !== status.running) run.innerHTML = status.running ? `${icon('pause')} 일시정지` : `${icon('play')} 실행`;
  if (document.activeElement !== speed) speed.value = status.speed;
  renderChannels();
  plantView?.update(status);
  response.update(status);
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.timeout(3500), ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '서버 요청 실패');
  return data;
}

async function command(body, onSuccess) {
  const result = await request('/api/command', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!status || result.version >= status.version) receive(result);
  onSuccess?.(result);
}
const doCommand = (body, callback) => command(body, callback).catch((error) => note(error.message, 'danger'));

function selectTag(tag) {
  const ch = channels.get(tag);
  if (ch) focus(ch.area, tag);
}

function attachEvents() {
  // Chrome can match :focus-visible on a select clicked with a pointer.
  const root = document.documentElement;
  document.addEventListener('pointerdown', () => root.setAttribute('data-pointer-focus', ''), true);
  document.addEventListener('keydown', (event) => {
    const selectNavigation = ['ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key) && event.target.closest?.('select');
    if (event.key === 'Tab' || selectNavigation) {
      root.removeAttribute('data-pointer-focus');
    }
  }, true);

  run.addEventListener('click', () => doCommand({ op: 'run', enabled: !status.running }));
  reset.addEventListener('click', () => doCommand({ op: 'reset' }, () => {
    explicitTag = '';
    follow({ kind: 'channel', item: channels.get('XMEAS(7)') });
    focus('plant', '', true);
    note('Mode 1 새 실행을 생성했습니다.');
  }));
  speed.addEventListener('change', () => doCommand({ op: 'speed', value: +speed.value }));
  byId('open-controls').addEventListener('click', () => openControls());
  byId('overview').addEventListener('click', () => {
    plantView.circuit('', false);
    focus('plant', '', true);
  });
  byId('tab-values').addEventListener('click', () => {
    showTelemetry('values');
    renderChannels();
  });
  // Direct SP tab entry uses the same path as every other SP shortcut, so unit, tag and loop match.
  byId('tab-response').addEventListener('click', () => {
    const loop = activeSetpoint();
    if (loop) trackSetpoint(loop.id);
  });
  byId('scope-primary').addEventListener('click', () => {
    for (const id of ['search', 'channel-group', 'channel-area']) byId(id).value = '';
    setScope('primary');
  });
  byId('scope-all').addEventListener('click', () => setScope('all'));
  // Active search/filters always look across all channels; clearing them returns to the chosen scope.
  byId('search').addEventListener('input', filter);
  byId('channel-group').addEventListener('change', filter);
  byId('channel-area').addEventListener('change', (event) => {
    const area = event.currentTarget.value;
    if (area && area !== focusedArea) focus(area);
    filter();
  });
  byId('rows').addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-tag]');
    if (row) selectTag(row.dataset.tag);
  });
}

async function poll() {
  try {
    if (!channels.size) {
      const schema = await request('/api/schema');
      schema.channels.forEach((ch) => channels.set(ch.id, ch));
      byId('channel-count').textContent = schema.channels.length;
      byId('channel-group').insertAdjacentHTML('beforeend', schema.groups.map((g) => option(g.id, `${g.label} · ${g.count}`)).join(''));
      setpoints = schema.setpoints;
      setpointControls.init(setpoints);
      scenario.init(schema.controller_flags ?? {}, schema.channels);
      response.init(setpoints);
      openControls = setupControls(schema, () => status, command, (kind, item) => {
        const next = { kind, item };
        const tag = tagForSelection(next);
        plantView.reveal(tag);
        focus(item.area, tag, false, next);
        if (kind === 'setpoint') showTelemetry('response', item.id);
      });
      follow({ kind: 'channel', item: channels.get('XMEAS(7)') });
      filter();
    }
    if (!pageRecorded) {
      await command({ op: 'new_recording', page_id: pageId });
      pageRecorded = true;
    }
    const next = await request('/api/live');
    if (!status || next.version > status.version || !connected || next.run !== status.run) receive(next);
  } catch {
    connected = false;
    renderConnection();
  } finally {
    setTimeout(poll, connected ? 430 : 1400);
  }
}

initIcons();
byId('channel-area').insertAdjacentHTML('beforeend', Object.entries(AREAS).filter(([key]) => key !== 'plant').map(([key, area]) => option(key, area.name)).join(''));
renderInspector(focusedArea);
attachEvents();
plantView = mountPlantView({
  selectArea: focus,
  selectFeed: (tag) => {
    plantView.reveal(tag);
    selectTag(tag);
  },
  selectValve: (tag) => {
    const channel = channels.get(tag);
    if (channel) { focus(channel.area, tag); openControls?.(tag); }
  },
  onCircuit: renderCircuitRoute,
  trackTag,
});
poll();
