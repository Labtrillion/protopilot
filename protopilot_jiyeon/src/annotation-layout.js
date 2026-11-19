import { MathUtils } from 'three';

const inset = 3;
const gap = 3;

export function placeAnnotation(x, y, width, height, viewportWidth, viewportHeight, occupied, range, offset) {
  const maxLeft = viewportWidth - inset - width;
  const maxTop = viewportHeight - inset - height;
  if (!width || !height || maxLeft < inset || maxTop < inset) return;

  const preferredLeft = MathUtils.clamp(x, inset, maxLeft);
  const preferredTop = MathUtils.clamp(y, inset, maxTop);
  // Carry the previous offset with the anchor so nearby openings do not swap a tag to another side.
  const targetLeft = MathUtils.clamp(preferredLeft + (offset?.x ?? 0), inset, maxLeft);
  const targetTop = MathUtils.clamp(preferredTop + (offset?.y ?? 0), inset, maxTop);
  const nearby = occupied.filter((box) =>
    box.right + gap > preferredLeft - range && box.left - gap < preferredLeft + range + width &&
    box.bottom + gap > preferredTop - range && box.top - gap < preferredTop + range + height);

  // The nearest free rectangle touches the preferred position, a viewport edge, or a neighbor's edge.
  const lefts = [targetLeft, preferredLeft, inset, maxLeft];
  const tops = [targetTop, preferredTop, inset, maxTop];
  for (const box of nearby) {
    lefts.push(box.left - gap - width, box.right + gap);
    tops.push(box.top - gap - height, box.bottom + gap);
  }

  let closest;
  let best = Infinity;
  const limit = range * range;
  for (const left of lefts) {
    const dx = left - targetLeft;
    if (left < inset || left > maxLeft || dx * dx >= best) continue;
    const right = left + width;
    const column = nearby.filter((box) => left < box.right + gap && left > box.left - gap - width);
    for (const top of tops) {
      const dy = top - targetTop;
      const distance = dx * dx + dy * dy;
      if (top < inset || top > maxTop || distance >= best) continue;
      if ((left - preferredLeft) ** 2 + (top - preferredTop) ** 2 > limit) continue;
      const bottom = top + height;
      if (column.some((box) => top < box.bottom + gap && top > box.top - gap - height)) continue;
      closest = { left, top, right, bottom, offset: { x: left - preferredLeft, y: top - preferredTop } };
      if (distance === 0) return closest;
      best = distance;
    }
  }
  return closest;
}
