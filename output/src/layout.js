import { CurvePath, LineCurve3, QuadraticBezierCurve3, Vector3 } from 'three';
import { ANALYZER_TAPS, AREAS } from './topology.js';

export const OVERVIEW = {
  target: [0, 2.76, -0.8],
  offset: [6.8, 18, 28],
  halfWidth: 22.2,
  halfHeight: 8.9,
};

export const VESSELS = {
  reactor: { radius: 1.47, bottom: 0.9, top: 5.28, deck: 4.35, seams: [2.5, 3.65], agitator: true, levelTag: 8 },
  separator: { radius: 1.19, bottom: 0.84, top: 4.55, deck: 3.4, seams: [2.31], levelTag: 12 },
  stripper: { radius: 1.04, bottom: 0.83, top: 7.22, deck: 4.45, seams: [1.85, 3.36, 5.56, 6.4], levelTag: 15 },
  reboiler: { radius: 0.38, bottom: 0.51, top: 2.93, seams: [1.25, 2.24] },
};

export function sampleRoute(area) {
  const [x, y, z] = AREAS[area].position;
  const [tapX, tapY, tapZ] = ANALYZER_TAPS[area];
  return [[x + 0.22, y + 1.84, z], [x + 0.22, y + 2.04, z], [tapX, y + 2.04, tapZ], [tapX, tapY, tapZ]];
}

export function pipePath(points) {
  const segments = new CurvePath();
  let start = new Vector3(...points[0]);
  for (let i = 1; i < points.length - 1; i++) {
    const corner = new Vector3(...points[i]);
    const before = new Vector3(...points[i - 1]);
    const after = new Vector3(...points[i + 1]);
    const radius = Math.min(0.21, corner.distanceTo(before) * 0.35, corner.distanceTo(after) * 0.35);
    const entry = corner.clone().add(before.sub(corner).setLength(radius));
    const exit = corner.clone().add(after.sub(corner).setLength(radius));
    if (start.distanceToSquared(entry) > 1e-6) segments.add(new LineCurve3(start, entry));
    segments.add(new QuadraticBezierCurve3(entry, corner, exit));
    start = exit;
  }
  segments.add(new LineCurve3(start, new Vector3(...points.at(-1))));
  return segments;
}

export function valvePosition(path, distance, length = path.getLength()) {
  const fraction = Math.min(0.88, distance / length);
  return { point: path.getPointAt(fraction), tangent: path.getTangentAt(fraction) };
}
