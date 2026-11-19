export const SCORE_WEIGHTS = { completion: 60, operatingRange: 30, production: 10 };

const shareFormat = new Intl.NumberFormat('ko-KR', { style: 'percent', maximumFractionDigits: 2 });

export function softmaxShares(scores = {}, temperature = 1) {
  const entries = Object.entries(scores).filter(([, score]) => Number.isFinite(score));
  const max = Math.max(...entries.map(([, score]) => score));
  const weights = entries.map(([id, score]) => [id, Math.exp((score - max) / temperature)]);
  const total = weights.reduce((sum, [, weight]) => sum + weight, 0);
  return Object.fromEntries(weights.map(([id, weight]) => [id, weight / total]));
}

export function formatZoroShare(share) {
  return Number.isFinite(share) ? shareFormat.format(share) : '—';
}

export function scoreBranch(branch, start) {
  const { window_hours: window, violation_minutes: violations, mean_production: flow } = branch;
  const { horizon_hours: horizon, production_target: target } = start;
  if (branch.error || ![window, violations, flow, horizon, target].every(Number.isFinite)
    || window <= 0 || horizon <= 0 || target <= 0 || violations < 0) return null;

  const comparedHours = Math.min(window, horizon);
  const safeMinutes = Math.max(0, comparedHours * 60 - violations);
  const volume = Math.max(0, flow) * comparedHours;
  const completed = !branch.shutdown && window >= horizon;
  const points = (completed ? SCORE_WEIGHTS.completion : 0)
    + SCORE_WEIGHTS.operatingRange * (safeMinutes / (horizon * 60))
    + SCORE_WEIGHTS.production * Math.min(1, volume / (target * horizon));
  return { score: Math.round(points * 100) / 100, safeMinutes, volume, completed };
}

export function evaluateBranch(branch, start) {
  const rating = scoreBranch(branch, start);
  let issue = '';
  if (!rating) issue = '계산 불가';
  else if (branch.shutdown) issue = '보호 정지';
  else if (!rating.completed) issue = '중도 종료';
  else if (branch.final_window_inside !== true) issue = '종료 구간 운전 범위 이탈';
  else if (branch.alarm_final_window_inside !== true) issue = '알람 복귀 기준 미충족';
  return { branch, rating, issue };
}

export function bestImprovement(baseline, candidates) {
  if (!baseline.rating) return null;
  return candidates.filter(({ issue, rating }) => !issue && (baseline.issue || rating.score > baseline.rating.score))
    .reduce((best, candidate) => (!best || candidate.rating.score > best.rating.score ? candidate : best), null);
}
