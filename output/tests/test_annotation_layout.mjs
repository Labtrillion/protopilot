import assert from 'node:assert/strict';
import { test } from 'node:test';
import { placeAnnotation } from '../src/annotation-layout.js';

const casing = { left: 350, right: 510, top: 140, bottom: 420 };

test('a tag stays on the same side when a small rotation brings the opposite side closer', () => {
  const first = placeAnnotation(399, 260, 60, 24, 900, 600, [casing], 180);
  const nearest = placeAnnotation(401, 260, 60, 24, 900, 600, [casing], 180);
  const retained = placeAnnotation(401, 260, 60, 24, 900, 600, [casing], 180, first.offset);

  assert.ok(first.right < casing.left);
  assert.ok(nearest.left > casing.right);
  assert.equal(retained.left, first.left);
  assert.equal(retained.top, first.top);
});

test('a previous position still yields to the viewport edge and interior clearance', () => {
  const placed = placeAnnotation(12, 260, 60, 24, 900, 600, [casing], 180, { x: -112, y: 0 });
  assert.ok(placed.left > 0);
  assert.ok(placed.right < casing.left);

  const fullWidth = { left: 0, right: 900, top: 0, bottom: 600 };
  assert.equal(placeAnnotation(401, 260, 60, 24, 900, 600, [fullWidth], 180, placed.offset), undefined);
});
