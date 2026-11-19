import { AREAS, codeFor } from './topology.js';
import { mountPlantView } from './plant-view.js';
import { mountResponse } from './response.js';
import { setupControls } from './controls.js';
import { mountSignal, signalTag } from './signal.js';
import {
  byId, elapsed, icon, initIcons, option, renderAlarms, renderInspector, renderKpis, renderRows, wallTime,
} from './ui.js';

const channels = new Map();
const trends = new Map();
const reset = byId('reset'), run = byId('run'), speed = byId('speed'), select = byId('area-select');
const connection = byId('connection'), soundBtn = byId('sound');
const pageId = crypto.randomUUID();
let plantView, openControls, status, audio, buzzer = false, noticeTimer, pageRecorded = false;
let circuit = '', setpoints = [], telemetryView = 'values';
let focusedArea = 'plant', selection, alarmTab = 'active', connected = false;
let seenAlarms = new Set(), visibleChannels = [];

const signal = mountSignal();
const response = mountResponse((id) => openControls?.(id), (id) => trackSetpoint(id));

function showTelemetry(view, setpoint) {
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
  const configured = chosen?.kind === 'setpoint' ? chosen.item : setpoints.find((item) => item.area === focusedArea);
  renderInspector(focusedArea, status, channels, circuit, configured, chosen?.kind === 'mv' ? chosen.item : null, signalTag(selection));
}

function follow(next) {
  const tag = signalTag(next);
  selection = next;
  signal.select(next);
  plantView?.track(next.item.area, tag);
  for (const button of byId('kpis').children) button.toggleAttribute('data-tracked', button.dataset.tag === tag);
}

function focus(area, tag = '', resetCamera = false, next) {
  if (!(area in AREAS)) return;
  const channel = channels.get(tag || codeFor(AREAS[area].tags[0]));
  if (next) follow(next);
  else if (channel && (tag || area !== 'plant')) {
    follow({ kind: channel.group === 'manipulated_variables' ? 'mv' : 'channel', item: channel });
  }
  focusedArea = area;
  select.value = area;
  plantView?.focus(area, resetCamera, tag);
  inspector();
  renderRows(visibleChannels, status, signalTag(selection));
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

function note(message, level = 'ok') {
  const box = byId('notice');
  box.textContent = message;
  box.dataset.level = level;
  box.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { box.hidden = true; }, 4500);
}

function ringAlarm() {
  if (!buzzer || audio?.state !== 'running') return;
  [0, 0.19].forEach((delay) => {
    const oscillator = audio.createOscillator(), gain = audio.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.value = delay ? 650 : 850;
    const start = audio.currentTime + delay;
    gain.gain.setValueAtTime(0.001, start);
    gain.gain.exponentialRampToValueAtTime(0.2, start + 0.016);
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.13);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start(start);
    oscillator.stop(start + 0.14);
  });
}

function filter() {
  const group = byId('channel-group').value;
  const area = byId('channel-area').value;
  const query = byId('search').value.toLowerCase().replace(/[\s()]/g, '');
  visibleChannels = [...channels.values()].filter((ch) => {
    const text = `${ch.id} ${ch.name} ${ch.label} ${ch.description} ${AREAS[ch.area].code}`.toLowerCase().replace(/[\s()]/g, '');
    return (!group || group === ch.group) && (!area || area === ch.area) && text.includes(query);
  });
  byId('matches').textContent = `${visibleChannels.length}개`;
  renderRows(visibleChannels, status, signalTag(selection));
}

function renderConnection() {
  const kind = !connected || status?.error ? 'offline' : status?.shutdown.terminated ? 'trip' : status?.running ? 'live' : 'paused';
  const labels = { offline: '연결 끊김', trip: '모델 트립', live: '시뮬레이션 실행 중', paused: '일시정지' };
  connection.className = `status ${kind}`;
  connection.innerHTML = `<i></i> ${status?.error ?? labels[kind]}`;
  run.disabled = !connected || !!status?.error || !!status?.shutdown.terminated;
  reset.disabled = !connected;
  byId('open-controls').disabled = !connected;
  byId('pressure-test').disabled = !connected;
  byId('clear-test').disabled = !connected;
  byId('tab-response').disabled = !setpoints.length;
  response.enable(connected);
  byId('record-light').classList.toggle('online', connected && !status?.error);
  byId('record-status').textContent = connected && !status?.error && status ? `로컬 기록 · ${status.sample.toLocaleString('ko-KR')} 샘플` : '로컬 기록 연결 확인';
}

function receive(next) {
  const previous = status;
  const runChanged = previous?.run !== next.run;
  const sampled = runChanged || previous?.sample !== next.sample;
  status = next;
  connected = true;
  if (runChanged) {
    trends.clear();
    seenAlarms = new Set();
  }
  if (sampled) {
    for (const tag of ['XMEAS(7)', 'XMEAS(9)', 'XMEAS(6)', 'XMEAS(17)']) {
      const values = trends.get(tag) ?? [];
      values.push(status.values[tag]);
      if (values.length > 70) values.shift();
      trends.set(tag, values);
    }
  }
  const newAlarm = status.alarms.filter((alarm) => !seenAlarms.has(alarm.id)).sort((a, b) => b.id - a.id)[0];
  seenAlarms = new Set(status.alarms.map((alarm) => alarm.id));
  if (newAlarm) {
    plantView?.reveal(newAlarm.tag);
    focus(newAlarm.area, newAlarm.tag);
    alarmTab = 'active';
    byId('active-tab').setAttribute('aria-pressed', 'true');
    byId('history-tab').setAttribute('aria-pressed', 'false');
    ringAlarm();
    note(newAlarm.label, newAlarm.kind === 'trip' ? 'danger' : 'warning');
  }
  renderConnection();
  byId('clock').textContent = elapsed(status.time_hours);
  byId('footer-time').textContent = `마지막 샘플 ${wallTime(status.time_utc)} KST · TEP Studio`;
  if (previous?.running !== status.running) run.innerHTML = status.running ? `${icon('pause')} 일시정지` : `${icon('play')} 실행`;
  if (document.activeElement !== speed) speed.value = status.speed;
  byId('clear-test').hidden = !('XMEAS(7)' in status.offsets);
  if (sampled) renderKpis(status, channels, trends, signalTag(selection));
  renderRows(visibleChannels, status, signalTag(selection));
  inspector();
  renderAlarms(status, alarmTab, channels);
  plantView?.update(status);
  response.update(status);
  signal.update(status, connected);
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
  focus(ch.area, tag);
  if (navigate) {
    if (telemetryView !== 'values') showTelemetry('values');
    byId('channel-group').value = '';
    byId('channel-area').value = '';
    byId('search').value = tag;
    filter();
    byId('rows').firstElementChild?.scrollIntoView({ block: 'nearest' });
  }
}

function attachEvents() {
  run.addEventListener('click', () => doCommand({ op: 'run', enabled: !status.running }));
  reset.addEventListener('click', () => doCommand({ op: 'reset' }, () => {
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
  byId('tab-values').addEventListener('click', () => showTelemetry('values'));
  byId('tab-response').addEventListener('click', () => {
    showTelemetry('response', setpoints.find((item) => item.pv === signalTag(selection))?.id);
  });
  byId('signal-locate').addEventListener('click', () => {
    if (!selection) return;
    const tag = signalTag(selection);
    plantView.reveal(tag);
    focus(selection.item.area, tag, false, selection);
    byId('stage').scrollIntoView({ block: 'nearest' });
  });
  byId('signal-response').addEventListener('click', () => trackSetpoint(selection?.item.id, true));
  byId('inspector-content').addEventListener('click', (event) => {
    const responseButton = event.target.closest('[data-response]');
    if (responseButton) trackSetpoint(responseButton.dataset.response, true);
    const controlButton = event.target.closest('[data-control]');
    if (controlButton) openControls?.(controlButton.dataset.control);
  });
  select.addEventListener('change', () => focus(select.value));
  byId('search').addEventListener('input', filter);
  byId('channel-group').addEventListener('change', filter);
  byId('channel-area').addEventListener('change', filter);
  for (const id of ['inspector-content', 'kpis', 'rows']) {
    byId(id).addEventListener('click', (event) => {
      const target = event.target.closest(id === 'rows' ? 'tr[data-tag]' : 'button[data-tag]');
      if (!target) return;
      selectTag(target.dataset.tag, id === 'inspector-content');
      const detail = byId('signal-detail');
      const bounds = detail.getBoundingClientRect();
      if (id !== 'inspector-content' && (bounds.top < 0 || bounds.bottom > innerHeight)) {
        byId('signal-heading').focus({ preventScroll: true });
        detail.scrollIntoView({ block: 'nearest' });
      }
    });
  }
  for (const [id, tab] of [['active-tab', 'active'], ['history-tab', 'history']]) {
    byId(id).addEventListener('click', () => {
      alarmTab = tab;
      byId('active-tab').setAttribute('aria-pressed', tab === 'active');
      byId('history-tab').setAttribute('aria-pressed', tab === 'history');
      renderAlarms(status, alarmTab, channels);
    });
  }
  byId('events').addEventListener('click', (event) => {
    const item = event.target.closest('[data-event-area]');
    if (item) focus(item.dataset.eventArea, item.dataset.eventTag);
    const ack = event.target.closest('[data-ack]');
    if (ack) doCommand({ op: 'ack', id: +ack.dataset.ack });
  });
  byId('pressure-test').addEventListener('click', () => doCommand({ op: 'pressure_test' }, () => {
    plantView?.showPressure();
    focus('reactor', 'XMEAS(7)');
  }));
  byId('clear-test').addEventListener('click', () => doCommand({ op: 'offset', id: 'XMEAS(7)', value: 0 }, () => selectTag('XMEAS(7)')));
  soundBtn.addEventListener('click', async () => {
    if (buzzer) {
      buzzer = false;
    } else {
      try {
        audio ??= new AudioContext();
        await audio.resume();
        buzzer = true;
      } catch {
        note('브라우저에서 알람 소리를 재생할 수 없습니다.', 'danger');
      }
    }
    soundBtn.setAttribute('aria-pressed', buzzer);
    soundBtn.innerHTML = icon(buzzer ? 'sound' : 'muted');
    soundBtn.title = buzzer ? '알람 소리 끄기' : '알람 소리 켜기';
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
      response.init(setpoints);
      openControls = setupControls(schema, () => status, command, (kind, item) => {
        const next = { kind, item };
        const tag = signalTag(next);
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
    signal.update(status, connected);
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
});
poll();
