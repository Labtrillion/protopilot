import assert from 'node:assert/strict';
import test from 'node:test';

// Importing UI helpers reads a CSS palette from the document.
globalThis.document = { documentElement: {}, createElement: () => ({ setAttribute() {} }) };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#000' });
const { renderFeature1Comparison, renderZoroCards, renderZoroEvidence, renderZoroSummary } =
  await import('../src/zoro-results.js');
delete globalThis.document;
delete globalThis.getComputedStyle;

const hold = { id: 'hold', changes: [], cost_per_m3: 7.14, window_hours: 4 };
const rule = { ...hold, id: 'rule' };
const move = { setpoint: 'production_rate', from: 21, to: 20, move: -0.05 };
const baseStudy = {
  start: { horizon_hours: 4 },
  setpoints: { production_rate: { label: '생산 목표', unit: 'm³/h' } },
  evidence: [],
};

test('a completed Zoro decision with no moves selects the unchanged setpoints', () => {
  const study = {
    ...baseStudy,
    decision: { status: 'ready', changes: [] },
    branches: [hold, rule, { ...hold, id: 'zoro' }],
    feature2: { status: 'human_review', reason: 'no_eligible_branch' },
  };
  const comparison = renderFeature1Comparison(study);
  assert.match(comparison, /<h3>현재 설정값 유지<\/h3>/);
  assert.match(comparison, /<th scope="col" data-selected>현재 목표 유지/);
  assert.equal(comparison.match(/유지안과 동일/g).length, 2);
  assert.doesNotMatch(comparison, /유지 대비 0/);

  const summary = renderZoroSummary(study);
  assert.match(summary, /피처 1 · 현재 설정값 유지/);
  assert.match(summary, /피처 2 · 권고안 미확정/);
});

test('a chosen change that violates an operating limit is flagged for review', () => {
  const study = {
    ...baseStudy,
    decision: { status: 'ready', changes: [move] },
    branches: [hold, { ...hold, id: 'zoro', changes: [move], violation_minutes: 3 }],
  };
  const comparison = renderFeature1Comparison(study);
  assert.match(comparison, /<h3>선택한 안 재검토 필요<\/h3>/);
  assert.match(comparison, /운전 한계 위반/);
  assert.match(comparison, /21 → 20/);
  assert.match(renderZoroSummary(study), /선택한 안 재검토 필요/);
});

test('an incomplete or failed simulation reports a status without using failed metrics', () => {
  const study = {
    ...baseStudy,
    decision: { status: 'ready', changes: [move] },
    branches: [
      { ...hold, window_hours: 2 },
      { id: 'zoro', changes: [move], error: 'simulation failed', cost_per_m3: 999 },
    ],
  };
  const comparison = renderFeature1Comparison(study);
  assert.match(comparison, /비교 시간 미완료/);
  assert.match(comparison, /계산 실패/);
  assert.doesNotMatch(comparison, /999/);
});

test('Feature 2 reports skipped comparisons and only renders one baseline', () => {
  const study = { ...baseStudy, decision: { status: 'hold', changes: [] }, branches: [hold] };
  const skipped = renderZoroSummary({ ...study, feature2: { status: 'skipped' } });
  assert.match(skipped, /후속 비교 미실행/);
  assert.doesNotMatch(skipped, /0개 채점/);

  const baseline = { ...hold, id: 'feature1', failed_gates: ['final_window_inside'] };
  const alternative = { id: 'alt', error: 'simulation failed' };
  const result = { ...study, feature2: { baseline, evaluated: [baseline, alternative] } };
  const cards = renderZoroCards(result);
  assert.equal(cards.match(/class="simulation-card"/g).length, 2);
  assert.match(cards, /설정값 변경 정보 없음/);
  const evidence = renderZoroEvidence(result);
  assert.match(evidence, /종료 구간 추적 변수 허용 범위/);
  assert.doesNotMatch(evidence, /final_window_inside/);
});

test('stored evidence still shows the gate score when the moves field is missing', () => {
  const evidence = renderZoroEvidence({ ...baseStudy, decision: { gate: { production_rate: 0.2629 } } });
  assert.match(evidence, /생산 목표 · 26\.29%/);
});
