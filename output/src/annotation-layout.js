import { MathUtils } from 'three';

const inset = 3;
const gap = 3;

export function placeAnnotation(x, y, width, height, viewportWidth, viewportHeight, occupied, range) {
  const maxLeft = viewportWidth - inset - width;
  const maxTop = viewportHeight - inset - height;
  if (!width || !height || maxLeft < inset || maxTop < inset) return;

  const preferredLeft = MathUtils.clamp(x, inset, maxLeft);
  const preferredTop = MathUtils.clamp(y, inset, maxTop);
  const nearby = occupied.filter((box) =>
    box.right + gap > preferredLeft - range && box.left - gap < preferredLeft + range + width &&
    box.bottom + gap > preferredTop - range && box.top - gap < preferredTop + range + height);

  // The nearest free rectangle touches the preferred position, a viewport edge, or a neighbor's edge.
  const lefts = [preferredLeft, inset, maxLeft];
  const tops = [preferredTop, inset, maxTop];
  for (const box of nearby) {
    lefts.push(box.left - gap - width, box.right + gap);
    tops.push(box.top - gap - height, box.bottom + gap);
  }

  let closest;
  let best = range * range;
  for (const left of lefts) {
    const dx = left - preferredLeft;
    if (left < inset || left > maxLeft || dx * dx >= best) continue;
    const right = left + width;
    const column = nearby.filter((box) => left < box.right + gap && left > box.left - gap - width);
    for (const top of tops) {
      const dy = top - preferredTop;
      const distance = dx * dx + dy * dy;
      if (top < inset || top > maxTop || distance >= best) continue;
      const bottom = top + height;
      if (column.some((box) => top < box.bottom + gap && top > box.top - gap - height)) continue;
      closest = { left, top, right, bottom };
      if (distance === 0) return closest;
      best = distance;
    }
  }
  return closest;
}
