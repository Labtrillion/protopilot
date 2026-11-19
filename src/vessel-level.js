import * as T from 'three';
import { VESSEL_CAPS } from './layout.js';
import { readColor } from './palette.js';
import { levelFraction } from './topology.js';

const liquid = new T.MeshStandardMaterial({
  color: readColor('--level-fill'), transparent: true, opacity: 0.78, depthWrite: false, roughness: 0.4,
});
const surface = new T.MeshStandardMaterial({
  color: readColor('--level-surface'), transparent: true, opacity: 0.88, depthWrite: false, roughness: 0.35,
});
const goalMaterial = new T.LineDashedMaterial({
  color: readColor('--plot-sp'), toneMapped: false, dashSize: 0.14, gapSize: 0.085,
});
const segments = 48;
const capSteps = 12;

export function mountVesselLevel(interior, { radius, bottom, top }) {
  const lowerCap = radius * VESSEL_CAPS.lower, upperCap = radius * VESSEL_CAPS.upper;
  const floor = bottom - lowerCap, ceiling = top + upperCap;
  const innerRadius = radius * 0.96;
  const radiusAt = (y) => {
    const cap = Math.max(0, (bottom - y) / lowerCap, (y - top) / upperCap);
    return innerRadius * Math.sqrt(Math.max(0, 1 - cap * cap));
  };
  const elevation = (fraction) => T.MathUtils.lerp(floor, ceiling, fraction);
  // Follow both rounded caps so the liquid remains inside the casing at every level.
  const profile = Array.from({ length: capSteps }, (_, i) => floor + lowerCap * (i + 1) / capSteps)
    .concat(Array.from({ length: capSteps }, (_, i) => top + upperCap * i / capSteps));
  const body = new T.Mesh(new T.BufferGeometry(), liquid);
  const waterline = new T.Mesh(new T.CircleGeometry(1, segments), surface);
  waterline.rotation.x = -Math.PI / 2;
  body.renderOrder = 1;
  waterline.renderOrder = 2;
  body.visible = waterline.visible = false;
  const anchor = new T.Object3D();
  const goal = new T.LineLoop(new T.BufferGeometry().setFromPoints(
    Array.from({ length: segments }, (_, i) => {
      const angle = i / segments * Math.PI * 2;
      return new T.Vector3(Math.cos(angle), 0, Math.sin(angle));
    }),
  ), goalMaterial);
  goal.computeLineDistances();
  goal.visible = false;
  interior.add(body, waterline, anchor, goal);
  let previous;
  return {
    anchor,
    draw(value, target) {
      const fraction = levelFraction(value);
      if (fraction !== previous) {
        previous = fraction;
        const y = elevation(fraction), r = radiusAt(y);
        body.visible = waterline.visible = fraction > 0;
        if (body.visible) {
          const points = [new T.Vector2(0, floor), ...profile.filter((height) => height < y)
            .map((height) => new T.Vector2(radiusAt(height), height)), new T.Vector2(r, y)];
          body.geometry.dispose();
          body.geometry = new T.LatheGeometry(points, segments);
          waterline.position.y = y;
          waterline.scale.setScalar(r);
        }
        anchor.position.set(-r * 0.72, y, r * 0.5);
      }
      goal.visible = Number.isFinite(target);
      if (goal.visible) {
        goal.position.y = elevation(levelFraction(target));
        goal.scale.setScalar(radiusAt(goal.position.y));
      }
    },
  };
}
