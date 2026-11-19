import { AREAS } from './topology.js';
import { elapsed, escape } from './ui.js';

export function alarmMeta(alarm) {
  const trip = alarm.kind === 'trip';
  const context = [AREAS[alarm.area]?.code, alarm.tag].filter(Boolean).join(' · ');
  return `<span class="alarm-card-meta">
    <span class="alarm-card-kind">${trip ? '보호 정지' : '경고'}</span>
    <span class="alarm-card-tag">${escape(context)}</span>
    <span class="alarm-card-time" title="시뮬레이션 발생 시각">${elapsed(alarm.time_hours)}</span>
  </span>`;
}

export function alarmActions(alarm, canRespond, active = true) {
  const id = escape(alarm.id), label = escape(alarm.label);
  const response = alarm.kind === 'trip' ? '대응 절차 보기' : '대응 비교';
  const canReview = canRespond && alarm.kind === 'warning';
  return `${active || canRespond ? `<button class="alarm-response" type="button" data-response-id="${id}"
    aria-label="${response} · ${label}" aria-haspopup="dialog"${canRespond ? '' : ' disabled title="비교할 발생 기록이 없습니다"'}>${response}</button>` : ''}
    ${canReview ? `<button type="button" data-zoro-id="${id}" aria-label="Zoro 대응 검토 · ${label}" aria-haspopup="dialog">Zoro 대응 검토</button>` : ''}
    ${active ? `<button type="button" data-ack-id="${id}" aria-label="알림 끄기 · ${label}">알림 끄기</button>` : ''}`;
}
