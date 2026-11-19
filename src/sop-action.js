import { reading } from './ui.js';

export function sopActionLabel({ label, reduction_pct: reduction, delay_minutes: delay }) {
  if (!Number.isFinite(reduction) || reduction === 0) return label;
  const timing = delay ? `${delay}분 후` : '즉시';
  const direction = reduction > 0 ? '감량' : '증량';
  return `${timing} 생산량 목표 ${reading(Math.abs(reduction))}% ${direction}`;
}

export function sopProductionTarget(branch, initialTarget) {
  const target = branch.production_target;
  if (!Number.isFinite(target)) return '—';
  return Number.isFinite(initialTarget) && initialTarget !== target
    ? `${reading(initialTarget)} → ${reading(target)}` : reading(target);
}
