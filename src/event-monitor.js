import { AREAS } from './topology.js';
import { alarmActions, alarmMeta } from './alarm-card.js';
import { byId, elapsed, escape } from './ui.js';

const KIND_LABELS = {
  warning: '경고', trip: '트립', clear: '복구', ack: '확인', protection: '보호 제어', change: '변경',
};

function renderEvent(event, canRespond, active) {
  const area = AREAS[event.area];
  const details = active ? (event.source === '공정 계측' ? '' : event.source)
    : [area?.code, event.tag, event.source].filter(Boolean).join(' · ');
  const meta = active ? alarmMeta(event) : `<span class="event-meta" data-kind="${escape(event.kind)}">
      <span>${KIND_LABELS[event.kind] ?? '이벤트'}</span>
      <span class="event-time" title="시뮬레이션 경과 시간">${elapsed(event.time_hours)}</span>
    </span>`;
  const content = `${meta}
    <span class="event-label">${escape(event.label)}</span>
    ${details ? `<span class="event-context">${escape(details)}</span>` : ''}`;
  const entry = area && event.area !== 'plant'
    ? `<button class="event-entry" type="button" data-event-id="${escape(event.id)}" title="${escape(area.name)} 보기">${content}</button>`
    : `<div class="event-entry">${content}</div>`;
  return `<li${active ? ` class="alarm-card" data-kind="${escape(event.kind)}"${event.ack ? ' data-silenced' : ''}` : ''}>
    ${entry}${active || canRespond ? `<div class="alarm-card-actions">${alarmActions(event, canRespond, active)}</div>` : ''}</li>`;
}

export function mountEventMonitor(selectEvent, openResponse, silenceAlarm) {
  const content = byId('event-monitor-content');
  const announcement = byId('alarm-announcement');
  const message = content.querySelector('.monitor-message');
  const list = content.querySelector('.event-list');
  const count = byId('active-alarm-count');
  const buttons = document.querySelectorAll('[data-event-mode]');
  let mode = 'alarms';
  let status, entries = [], renderKey, shownRun, connected;

  function render(resetScroll = false) {
    const alarmMode = mode === 'alarms';
    const current = status?.[mode] ?? [];
    const responseIds = new Set(status?.response_events ?? []);
    const available = current.filter(({ id }) => responseIds.has(id)).map(({ id }) => id);
    const key = JSON.stringify([status?.run, mode, current, available]);
    if (renderKey !== key) {
      const focused = document.activeElement;
      const { eventId, responseId, ackId } = focused?.dataset ?? {};
      entries = current.slice().sort((a, b) => (alarmMode ? Number(Boolean(a.ack)) - Number(Boolean(b.ack)) : 0) || b.id - a.id);
      list.classList.toggle('alarm-list', alarmMode);
      const restoreFocus = list.contains(focused);
      list.innerHTML = entries.map((entry) => renderEvent(entry, responseIds.has(entry.id), alarmMode)).join('');
      list.hidden = !entries.length;
      if (restoreFocus) {
        const selector = responseId ? `[data-response-id="${responseId}"]` : eventId ? `[data-event-id="${eventId}"]`
          : `[data-ack-id="${ackId}"], [data-response-id="${ackId}"]:enabled`;
        const nextFocus = list.querySelector(selector) ?? content;
        nextFocus.focus({ preventScroll: !ackId });
      }
      renderKey = key;
    }
    const text = connected
      ? (current.length ? '' : alarmMode ? '현재 활성 알람이 없습니다.' : '이 실행에 기록된 이벤트가 없습니다.')
      : status ? '연결 끊김 · 마지막 수신 기록' : '알람 및 이벤트에 연결하지 못했습니다.';
    if (message.textContent !== text) message.textContent = text;
    message.hidden = !text;
    const total = status ? String(status.alarms.length) : '';
    if (count.textContent !== total) count.textContent = total;
    if (resetScroll || shownRun !== status?.run) content.scrollTop = 0;
    shownRun = status?.run;
  }

  for (const button of buttons) button.addEventListener('click', () => {
    if (mode === button.dataset.eventMode) return;
    mode = button.dataset.eventMode;
    for (const control of buttons) control.setAttribute('aria-pressed', control === button);
    content.setAttribute('aria-label', mode === 'alarms' ? '현재 알람' : '최근 이벤트');
    render(true);
  });
  list.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-event-id], button[data-response-id], button[data-ack-id]');
    if (!button) return;
    const { eventId, responseId, ackId } = button.dataset;
    const entry = entries.find(({ id }) => id === Number(responseId ?? eventId ?? ackId));
    if (entry) (ackId ? silenceAlarm : responseId ? openResponse : selectEvent)(entry);
  });
  return {
    update(next, online) {
      const previous = status?.run === next?.run ? status?.alarms ?? [] : [];
      const newAlarms = next?.alarms.filter((alarm) => !alarm.ack && !previous.some(({ id }) => id === alarm.id)) ?? [];
      const text = newAlarms.map(({ label }) => label).join(' · ');
      if (announcement.textContent !== text) announcement.textContent = text;
      status = next;
      connected = online;
      render();
    },
  };
}
