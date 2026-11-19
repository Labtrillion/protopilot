import assert from 'node:assert/strict';
import test from 'node:test';
import { bestImprovement, evaluateBranch, scoreBranch } from '../src/simulation-score.js';

const start = { horizon_hours: 4, production_target: 20 };
const baseline = { window_hours: 4, violation_minutes: 0, mean_production: 20, shutdown: null };

test('a full run inside the operating range reaching the production target earns 100 points', () => {
  assert.deepEqual(scoreBranch(baseline, start), { score: 100, safeMinutes: 240, volume: 80, completed: true });
  assert.equal(scoreBranch({ ...baseline, mean_production: 22 }, start).score, 100);
});

test('staying in range longer can outweigh a reduction in production', () => {
  const hold = scoreBranch({ ...baseline, violation_minutes: 120 }, start);
  const reduce = scoreBranch({ ...baseline, mean_production: 18 }, start);
  assert.equal(hold.score, 85);
  assert.equal(reduce.score, 99);
});

test('a protection shutdown scores only the in-range time and production achieved before it', () => {
  const result = scoreBranch({ ...baseline, window_hours: 1, shutdown: {} }, start);
  assert.deepEqual(result, { score: 10, safeMinutes: 60, volume: 20, completed: false });
});

test('a failed branch or missing metrics do not receive a score', () => {
  assert.equal(scoreBranch({ error: 'simulation failed' }, start), null);
  assert.equal(scoreBranch({ ...baseline, mean_production: null }, start), null);
});

test('only a candidate meeting the final operating and alarm criteria can improve on the SOP', () => {
  const stable = { ...baseline, final_window_inside: true, alarm_final_window_inside: true };
  const sop = evaluateBranch({ ...stable, mean_production: 18 }, start);
  const tie = evaluateBranch({ ...stable, mean_production: 18 }, start);
  const unrecovered = evaluateBranch({ ...stable, alarm_final_window_inside: false }, start);
  const outOfRange = evaluateBranch({ ...stable, final_window_inside: false }, start);
  const improved = evaluateBranch({ ...stable, mean_production: 19 }, start);

  assert.equal(bestImprovement(sop, [tie, unrecovered, outOfRange]), null);
  assert.equal(bestImprovement(sop, [tie, unrecovered, outOfRange, improved]), improved);
  assert.equal(bestImprovement(unrecovered, [sop]), sop);
  assert.equal(bestImprovement(evaluateBranch({ error: 'failed' }, start), [improved]), null);
});
