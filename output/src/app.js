import { AREAS, codeFor } from './topology.js';
import { mountPlantView } from './plant-view.js';
import { mountResponse } from './response.js';
import { setupControls } from './controls.js';
import { mountScenario } from './scenario.js';
import { mountAlarmResponse } from './alarm-response.js';
import {
  byId, elapsed, icon, initIcons, option, renderAlarms, renderInspector, renderRows,
  unacknowledgedAlarms,
} from './ui.js';

// Representative closed loop per unit; used only when the server defines it for that unit.
const REPRESENTATIVE_LOOP = { reactor: 'reactor_pressure', separator: 'separator_level', stripper: 'stripper_level' };
// Channel tab first screen: gas pressure, level, temperature, inlet/product/purge flow of the main units.
const PRIMARY_TAGS = [7, 13, 16, 8, 12, 15, 9, 11, 18, 6, 17, 10].map(codeFor);
const TREND_LENGTH = 60;

const channels = new Map();
const recentReadings = [];
const reset = byId('reset'), run = byId('run'), speed = byId('speed'), select = byId('area-select');
const connection = byId('connection');
const pageId = crypto.randomUUID();
let plantView, openControls, status, noticeTimer, pageRecorded = false;
let circuit = '', setpoints = [], telemetryView = 'values';
let focusedArea = 'plant', selection, alarmTab = 'active', connected = false;
let seenAlarms = new Set(), visibleChannels = [];
let channelScope = 'primary', returnPoint = null, explicitTag = '';

const response = mountResponse((id) => openControls?.(id), (id) => trackSetpoint(id));
const alarmResponse = mountAlarmResponse(request);
const scenario = mountScenario(request, async (body) => {
  try {
    return await command(body);
  } catch (error) {
    if (body.op === 'controller') note(error.message, 'danger');
    else throw error;
  }
}, () => {
  clearReturn();
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

function inspector() {
  const chosen = selection?.item.area === focusedArea ? selection : null;
  const tag = tagForSelection(selection);
  const configured = chosen?.kind === 'setpoint' ? chosen.item : loopFor(focusedArea, tag);
  renderInspector(focusedArea, status, channels, circuit, configured, chosen?.kind === 'mv' ? chosen.item : null, tag);
}

function renderChannels() {
  renderRows(visibleChannels, status, tagForSelection(selection), recentReadings);
}

function refresh() {
  inspector();
  renderChannels();
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
  refresh();
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
  select.value = area;
  // Re-tracking keeps the camera in place while explicit navigation can reframe it.
  if (moved || resetCamera || tag.startsWith('XMV(')) plantView?.focus(area, resetCamera, tag);
  if (next?.kind !== 'setpoint' && telemetryView === 'response') {
    if (area === 'plant') closeResponse();
    else syncResponse(tag ? unitLoop(tag, area) : loopFor(area));
  }
  refresh();
}

function trackSetpoint(id, jump = false) {
  const item = setpoints.find((entry) => entry.id === id);
  if (!item) return;
  focus(item.area, item.pv, false, { kind: 'setpoint', item });
  showTelemetry('response', id);
  if (jump) {
    byId('response-select').focus({ preventScroll: true });
    byId('response-view').scrollIntoView({ block: 'nearest' });
  }
}

function note(message, level = 'ok', alarmId = '') {
  const box = byId('notice');
  box.textContent = message;
  box.dataset.level = level;
  box.dataset.alarmId = alarmId;
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

// Return point for alarm investigation: the first original position survives consecutive alarm visits.
function snapshot() {
  return {
    area: focusedArea, selection, view: telemetryView, loop: response.selected(), scope: channelScope, explicitTag,
    filters: ['search', 'channel-group', 'channel-area'].map((id) => [id, byId(id).value]),
    plant: plantView?.snapshot(),
  };
}

function renderReturn() {
  const button = byId('return-back');
  button.hidden = !returnPoint;
  if (!returnPoint) return;
  const tag = tagForSelection(returnPoint.selection);
  byId('return-label').textContent = `이전 위치로 돌아가기 · ${AREAS[returnPoint.area].code}${tag ? ` · ${tag}` : ''}`;
  button.title = `${AREAS[returnPoint.area].name}, ${returnPoint.view === 'response' ? 'SP 응답' : '채널'} 보기, ${returnPoint.plant?.view === '2d' ? '2D' : '3D'}${returnPoint.plant?.flags.internals ? ' 내부' : ''} 화면으로 복원`;
}

function rememberReturn(saved = snapshot()) {
  returnPoint ??= saved;
  renderReturn();
}

function clearReturn() {
  returnPoint = null;
  renderReturn();
}

function restoreReturn() {
  const saved = returnPoint;
  if (!saved) return;
  returnPoint = null;
  focusedArea = saved.area;
  select.value = saved.area;
  if (saved.selection) follow(saved.selection);
  plantView?.focus(saved.area, false, tagForSelection(saved.selection));
  plantView?.restore(saved.plant);
  for (const [id, value] of saved.filters) byId(id).value = value;
  channelScope = saved.scope;
  explicitTag = saved.explicitTag;
  showTelemetry(saved.view, saved.loop);
  filter();
  inspector();
  renderReturn();
  // The return button hides itself, so keyboard focus moves to the unit selector.
  select.focus({ preventScroll: true });
  note('알람 조사 전 위치로 돌아왔습니다.');
}

// Alarm investigation is always a user action: an alarm item or the pressure test button.
function investigate(area, tag, saved) {
  rememberReturn(saved);
  if (tag) plantView?.reveal(tag);
  focus(area, tag);
}

function markNewAlarm(on) {
  const tab = byId('active-tab');
  const show = on && alarmTab !== 'active';
  if (tab.hasAttribute('data-new') === show) return;
  tab.toggleAttribute('data-new', show);
  tab.textContent = show ? '미확인 알람 · 새 알람' : '미확인 알람';
}

function renderConnection() {
  scenario.update(status, connected);
  const kind = !connected || status?.error ? 'offline' : status?.shutdown.terminated ? 'trip' : status?.running ? 'live' : 'paused';
  const labels = { offline: '연결 끊김', trip: '모델 트립', live: '시뮬레이션 실행 중', paused: '일시정지' };
  connection.className = `status ${kind}`;
  connection.innerHTML = `<i></i> ${status?.error ?? labels[kind]}`;
  run.disabled = !connected || !!status?.error || !!status?.shutdown.terminated;
  reset.disabled = !connected;
  byId('open-controls').disabled = !connected;
  byId('pressure-test').disabled = !connected;
  byId('cooling-demo').disabled = !connected;
  byId('clear-test').disabled = !connected;
  byId('tab-response').disabled = !setpoints.length;
  response.enable(connected);
}

function receive(next) {
  const previous = status;
  const runChanged = previous?.run !== next.run;
  const sampled = runChanged || previous?.sample !== next.sample;
  status = next;
  connected = true;
  if (runChanged) {
    recentReadings.length = 0;
    seenAlarms = new Set();
  }
  if (sampled) {
    recentReadings.push(next.values);
    if (recentReadings.length > TREND_LENGTH) recentReadings.shift();
  }
  const pending = unacknowledgedAlarms(status);
  const newAlarm = pending.filter((alarm) => !seenAlarms.has(alarm.id)).sort((a, b) => b.id - a.id)[0];
  seenAlarms = new Set(status.alarms.map((alarm) => alarm.id));
  const notice = byId('notice');
  if (!notice.hidden && notice.dataset.alarmId && !pending.some((alarm) => String(alarm.id) === notice.dataset.alarmId)) {
    notice.hidden = true;
  }
  if (!pending.length) markNewAlarm(false);
  if (newAlarm) {
    // Incoming alarms notify only; the user's unit, SP, tracking, interior and filters stay as they are.
    markNewAlarm(true);
    const code = AREAS[newAlarm.area]?.code;
    note(`${code ? `${code} · ` : ''}${newAlarm.label} — 알람 항목을 선택하면 해당 설비로 이동합니다.`, newAlarm.kind === 'trip' ? 'danger' : 'warning', newAlarm.id);
  }
  renderConnection();
  byId('clock').textContent = elapsed(status.time_hours);
  if (previous?.running !== status.running) run.innerHTML = status.running ? `${icon('pause')} 일시정지` : `${icon('play')} 실행`;
  if (document.activeElement !== speed) speed.value = status.speed;
  byId('clear-test').hidden = !('XMEAS(7)' in status.offsets);
  renderChannels();
  inspector();
  renderAlarms(status, alarmTab, channels);
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

function selectTag(tag, navigate = false) {
  const ch = channels.get(tag);
  if (!ch) return;
  if (navigate && telemetryView === 'response') showTelemetry('values');
  focus(ch.area, tag);
  if (navigate) {
    // Reveal the row; only widen the current filter when the row is not already listed.
    if (!visibleChannels.includes(ch)) {
      byId('channel-group').value = '';
      byId('channel-area').value = '';
      byId('search').value = tag;
      filter();
    }
    const button = byId('rows').querySelector(`button[data-tag="${CSS.escape(tag)}"]`);
    button?.focus({ preventScroll: true });
    button?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
}

function attachEvents() {
  run.addEventListener('click', () => doCommand({ op: 'run', enabled: !status.running }));
  reset.addEventListener('click', () => doCommand({ op: 'reset' }, () => {
    clearReturn();
    explicitTag = '';
    follow({ kind: 'channel', item: channels.get('XMEAS(7)') });
    focus('plant', '', true);
    note('Mode 1 새 실행을 생성했습니다.');
  }));
  speed.addEventListener('change', () => doCommand({ op: 'speed', value: +speed.value }));
  byId('open-controls').addEventListener('click', () => openControls());
  byId('overview').addEventListener('click', () => {
    clearReturn();
    plantView.circuit('', false);
    focus('plant', '', true);
  });
  byId('return-back').addEventListener('click', restoreReturn);
  byId('tab-values').addEventListener('click', () => {
    showTelemetry('values');
    refresh();
  });
  // Direct SP tab entry uses the same path as every other SP shortcut, so unit, tag and loop match.
  byId('tab-response').addEventListener('click', () => {
    const tag = tagForSelection(selection);
    const loop = selection?.kind === 'setpoint' ? selection.item
      : loopForTag(tag) ?? (focusedArea === 'plant' ? setpoints.find((item) => item.id === 'reactor_pressure') : loopFor(focusedArea))
        ?? setpoints.find((item) => item.id === response.selected()) ?? setpoints[0];
    if (loop) trackSetpoint(loop.id);
  });
  byId('scope-primary').addEventListener('click', () => {
    for (const id of ['search', 'channel-group', 'channel-area']) byId(id).value = '';
    setScope('primary');
  });
  byId('scope-all').addEventListener('click', () => setScope('all'));
  byId('inspector-content').addEventListener('click', (event) => {
    const channelButton = event.target.closest('button[data-tag]');
    if (channelButton) selectTag(channelButton.dataset.tag, true);
    const responseButton = event.target.closest('[data-response]');
    if (responseButton) trackSetpoint(responseButton.dataset.response, true);
    const controlButton = event.target.closest('[data-control]');
    if (controlButton) openControls?.(controlButton.dataset.control);
  });
  select.addEventListener('change', () => focus(select.value));
  // Active search/filters always look across all channels; clearing them returns to the chosen scope.
  for (const [id, type] of [['search', 'input'], ['channel-group', 'change'], ['channel-area', 'change']]) {
    byId(id).addEventListener(type, filter);
  }
  byId('rows').addEventListener('click', (event) => {
    const row = event.target.closest('tr[data-tag]');
    if (row) selectTag(row.dataset.tag);
  });
  for (const [id, tab] of [['active-tab', 'active'], ['history-tab', 'history']]) {
    byId(id).addEventListener('click', () => {
      alarmTab = tab;
      if (tab === 'active') markNewAlarm(false);
      byId('active-tab').setAttribute('aria-pressed', tab === 'active');
      byId('history-tab').setAttribute('aria-pressed', tab === 'history');
      renderAlarms(status, alarmTab, channels);
    });
  }
  byId('events').addEventListener('click', (event) => {
    const compare = event.target.closest('[data-response-alarm]');
    if (compare && connected) {
      const id = Number(compare.dataset.responseAlarm);
      const alarm = status.alarms.find((item) => item.id === id) ?? status.events.find((item) => item.id === id);
      if (alarm) alarmResponse.open(status.run, alarm);
      return;
    }
    const item = event.target.closest('[data-event-area]');
    if (item) investigate(item.dataset.eventArea, item.dataset.eventTag);
    const ack = event.target.closest('[data-ack]');
    if (ack) doCommand({ op: 'ack', id: +ack.dataset.ack });
  });
  byId('cooling-demo').addEventListener('click', () => doCommand({ op: 'cooling_demo' }, () => {
    clearReturn();
    byId('active-tab').click();
    investigate('reactor', 'XMEAS(9)');
    note('냉각 이상 데모를 시작했습니다. 알람의 대응 비교를 누르세요.');
  }));
  byId('pressure-test').addEventListener('click', () => {
    const saved = snapshot();
    doCommand({ op: 'pressure_test' }, () => {
      plantView?.showPressure();
      investigate('reactor', 'XMEAS(7)', saved);
    });
  });
  // Clears the pressure alarm test (XMEAS(7)) only, without moving the investigation view.
  byId('clear-test').addEventListener('click', () => doCommand({ op: 'offset', id: 'XMEAS(7)', value: 0 }, () => {
    note('반응기 압력 센서 시험을 해제했습니다.');
  }));
}

async function poll() {
  try {
    if (!channels.size) {
      const schema = await request('/api/schema');
      schema.channels.forEach((ch) => channels.set(ch.id, ch));
      byId('channel-count').textContent = schema.channels.length;
      byId('channel-group').insertAdjacentHTML('beforeend', schema.groups.map((g) => option(g.id, `${g.label} · ${g.count}`)).join(''));
      setpoints = schema.setpoints;
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
      inspector();
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
select.innerHTML = Object.entries(AREAS).map(([key, area]) => option(key, key === 'plant' ? '공정 전체' : `${area.code} · ${area.name}`)).join('');
select.value = focusedArea;
byId('channel-area').insertAdjacentHTML('beforeend', Object.entries(AREAS).filter(([key]) => key !== 'plant').map(([key, area]) => option(key, area.name)).join(''));
inspector();
renderAlarms(status, alarmTab, channels);
attachEvents();
plantView = mountPlantView({
  selectArea: focus,
  selectValve: (tag) => {
    const channel = channels.get(tag);
    if (channel) { focus(channel.area, tag); openControls?.(tag); }
  },
  onCircuit: (id) => { circuit = id; inspector(); },
  trackTag,
});
poll();
