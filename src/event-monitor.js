import { AREAS } from './topology.js';
import { byId, elapsed, escape } from './ui.js';

const KIND_LABELS = {
  warning: '경고', trip: '트립', clear: '복구', ack: '확인', protection: '보호 제어', change: '변경',
};

function renderEvent(event) {
  const area = AREAS[event.area];
  const details = [area?.code, event.tag, event.source].filter(Boolean).join(' · ');
  const content = `<span class="event-meta" data-kind="${escape(event.kind)}">
      <span>${KIND_LABELS[event.kind] ?? '이벤트'}</span>
      <span class="event-time" title="시뮬레이션 경과 시간">${elapsed(event.time_hours)}</span>
    </span>
    <span class="event-label">${escape(event.label)}</span>
    ${details ? `<span class="event-context">${escape(details)}</span>` : ''}`;
  return `<li>${area && event.area !== 'plant'
    ? `<button class="event-entry" type="button" data-event-id="${escape(event.id)}" title="${escape(area.name)} 보기">${content}</button>`
    : `<div class="event-entry">${content}</div>`}</li>`;
}

export function mountEventMonitor(selectEvent) {
  const content = byId('event-monitor-content');
  const message = content.querySelector('.monitor-message');
  const list = content.querySelector('.event-list');
  const count = byId('active-alarm-count');
  const buttons = document.querySelectorAll('[data-event-mode]');
  let mode = 'alarms';
  let status, entries = [], renderKey, shownRun, connected;

  function render(resetScroll = false) {
    const alarmMode = mode === 'alarms';
    const current = status?.[mode] ?? [];
    const key = JSON.stringify([status?.run, mode, current]);
    if (renderKey !== key) {
      const focusedId = document.activeElement?.dataset.eventId;
      entries = current.slice().sort((a, b) => b.id - a.id);
      list.innerHTML = entries.map(renderEvent).join('');
      list.hidden = !entries.length;
      if (focusedId) list.querySelector(`[data-event-id="${focusedId}"]`)?.focus({ preventScroll: true });
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
    const button = event.target.closest('button[data-event-id]');
    if (button) selectEvent(entries.find(({ id }) => id === Number(button.dataset.eventId)));
  });
  return {
    update(next, online) {
      status = next;
      connected = online;
      render();
    },
  };
}
