import { AREAS } from './topology.js';
import { mountScene } from './scene.js';
import { setupControls } from './controls.js';
import {
  byId, elapsed, icon, initIcons, legend, option, renderAlarms, renderInspector, renderKpis, renderRows, wallTime,
} from './ui.js';

const channels = new Map();
const trends = new Map();
const reset = byId('reset'), run = byId('run'), speed = byId('speed'), select = byId('area-select');
const connection = byId('connection'), soundBtn = byId('sound');
let scene, openControls, status, audio, buzzer = false, noticeTimer;
let mode = 'flow', focusedArea = 'plant', selectedTag = '', alarmTab = 'active', connected = false;
let seenAlarms = new Set(), visibleChannels = [];

function focus(area, tag = '', resetCamera = false) {
  if (!(area in AREAS)) return;
  focusedArea = area;
  selectedTag = tag;
  select.value = area;
  scene?.go(area, resetCamera, tag);
  renderInspector(area, status, channels);
  renderRows(visibleChannels, status, selectedTag);
}

function changeView(view) {
  mode = view;
  byId('view-flow').setAttribute('aria-pressed', view === 'flow');
  byId('view-pressure').setAttribute('aria-pressed', view === 'pressure');
  scene?.mode(view);
  legend(view);
}

function note(message, isError = false) {
  const box = byId('notice');
  box.textContent = message;
  box.classList.toggle('error', isError);
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
  renderRows(visibleChannels, status, selectedTag);
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
    focus(newAlarm.area, newAlarm.tag);
    alarmTab = 'active';
    byId('active-tab').setAttribute('aria-pressed', 'true');
    byId('history-tab').setAttribute('aria-pressed', 'false');
    ringAlarm();
    note(newAlarm.label, true);
  }
  renderConnection();
  byId('clock').textContent = elapsed(status.time_hours);
  byId('footer-time').textContent = `마지막 샘플 ${wallTime(status.time_utc)} KST · TEP Studio`;
  if (previous?.running !== status.running) run.innerHTML = status.running ? `${icon('pause')} 일시정지` : `${icon('play')} 실행`;
  if (document.activeElement !== speed) speed.value = status.speed;
  byId('clear-test').hidden = !('XMEAS(7)' in status.offsets);
  if (sampled) renderKpis(status, channels, trends);
  renderRows(visibleChannels, status, selectedTag);
  renderInspector(focusedArea, status, channels);
  renderAlarms(status, alarmTab, channels);
  scene?.updateVisuals(status);
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.timeout(3500), ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '서버 요청 실패');
  return data;
}

async function command(body, beforeReceive) {
  const result = await request('/api/command', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  beforeReceive?.();
  if (!status || result.version >= status.version) receive(result);
}
const doCommand = (body, callback) => command(body, callback).catch((error) => note(error.message, true));

function selectTag(tag, navigate = false) {
  const ch = channels.get(tag);
  if (!ch) return;
  focus(ch.area, tag);
  if (navigate) {
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
    focus('plant', '', true);
    note('Mode 1 새 실행을 생성했습니다.');
  }));
  speed.addEventListener('change', () => doCommand({ op: 'speed', value: +speed.value }));
  byId('open-controls').addEventListener('click', () => openControls());
  byId('view-flow').addEventListener('click', () => changeView('flow'));
  byId('view-pressure').addEventListener('click', () => changeView('pressure'));
  byId('overview').addEventListener('click', () => focus('plant', '', true));
  byId('toggle-tags').addEventListener('click', (event) => {
    const button = event.currentTarget;
    const enabled = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', enabled);
    scene?.tags(enabled);
  });
  byId('toggle-internals').addEventListener('click', (event) => {
    const button = event.currentTarget;
    const enabled = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', enabled);
    scene?.internals(enabled);
  });
  select.addEventListener('change', () => focus(select.value));
  byId('search').addEventListener('input', filter);
  byId('channel-group').addEventListener('change', filter);
  byId('channel-area').addEventListener('change', filter);
  for (const id of ['inspector-content', 'kpis', 'rows']) {
    byId(id).addEventListener('click', (event) => {
      const button = event.target.closest('button[data-tag]');
      if (button) selectTag(button.dataset.tag, id !== 'rows');
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
    changeView('pressure');
    focus('reactor', 'XMEAS(7)');
  }));
  byId('clear-test').addEventListener('click', () => doCommand({ op: 'offset', id: 'XMEAS(7)', value: 0 }));
  soundBtn.addEventListener('click', async () => {
    if (buzzer) {
      buzzer = false;
    } else {
      try {
        audio ??= new AudioContext();
        await audio.resume();
        buzzer = true;
      } catch {
        note('브라우저에서 알람 소리를 재생할 수 없습니다.', true);
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
      openControls = setupControls(schema, () => status, command, focus);
      filter();
      renderInspector(focusedArea, status, channels);
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
legend(mode);
select.innerHTML = Object.entries(AREAS).map(([key, area]) => option(key, key === 'plant' ? '공정 전체' : `${area.code} · ${area.name}`)).join('');
byId('channel-area').insertAdjacentHTML('beforeend', Object.entries(AREAS).filter(([key]) => key !== 'plant').map(([key, area]) => option(key, area.name)).join(''));
renderInspector(focusedArea, status, channels);
renderAlarms(status, alarmTab, channels);
attachEvents();
try {
  scene = mountScene(byId('canvas'), byId('model-labels'), (area, tag) => focus(area, tag));
} catch (error) {
  console.error(error);
  byId('canvas-error').hidden = false;
}
poll();
