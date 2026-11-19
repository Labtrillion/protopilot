import * as T from 'three';
import { steel, edge, lightSteel, gold, box, rod, v3 } from './geometry.js';
import { CORE_PRESSURES, INTERIOR_PARTS, codeFor } from './topology.js';
import { readColor } from './palette.js';

const glass = new T.MeshPhysicalMaterial({
  color: 0xd2d8ce, roughness: 0.30, metalness: 0.30,
  transparent: true, opacity: 0.11, depthWrite: false, side: T.FrontSide,
});
const liquid = new T.MeshStandardMaterial({
  color: readColor('--process-liquid'), transparent: true, opacity: 0.52, depthWrite: false, roughness: 0.32,
});
const surface = new T.MeshStandardMaterial({
  color: readColor('--process-liquid'), transparent: true, opacity: 0.8, depthWrite: false, roughness: 0.28,
});
const water = new T.MeshStandardMaterial({
  color: readColor('--process-water'), metalness: 0.32, roughness: 0.28,
});
const steam = new T.MeshStandardMaterial({
  color: readColor('--process-steam'), metalness: 0.29, roughness: 0.28,
});
const goalMaterial = new T.LineDashedMaterial({ color: readColor('--plot-sp'), toneMapped: false, dashSize: 0.14, gapSize: 0.085 });
const limit = (value) => T.MathUtils.clamp(value, 0, 1);

function pipe(group, points, material, radius = 0.055, segments = 48) {
  group.add(new T.Mesh(new T.TubeGeometry(new T.CatmullRomCurve3(points.map(v3)), segments, radius, 10), material));
}
function coil(group, turns, start, end, radius, material, offset = 0) {
  const points = [start];
  for (let i = 0; i <= turns * 24; i++) {
    const f = i / (turns * 24);
    const angle = turns * Math.PI * 2 * f + offset;
    points.push([radius * Math.cos(angle), T.MathUtils.lerp(start[1], end[1], f), radius * Math.sin(angle)]);
  }
  points.push(end);
  pipe(group, points, material, radius > 0.5 ? 0.073 : 0.035, turns * 28);
}
function agitator(group) {
  const rotor = new T.Group();
  group.add(rotor);
  rod(rotor, [0,1.08,0], [0,5.36,0], 0.082, lightSteel);
  for (const y of [1.42, 2.55, 3.32]) {
    const blade = new T.Group();
    blade.rotation.y = y;
    box(blade, [1.36, 0.08, 0.12], [0,y,0], edge);
    for (const x of [-0.52, 0.52]) {
      const paddle = box(blade, [0.29, 0.10, 0.37], [x,y,0], gold);
      paddle.rotation.x = 0.35;
    }
    rotor.add(blade);
  }
  return rotor;
}

function trayStack(group) {
  const plate = new T.CylinderGeometry(0.89, 0.89, 0.037, 40);
  for (const [index, y] of [2.98,3.48,3.98,4.48,4.98,5.48].entries()) {
    const tray = new T.Mesh(plate, index % 2 ? edge : gold);
    tray.position.y = y;
    group.add(tray);
    const x = index % 2 ? 0.60 : -0.60;
    box(group, [0.13, 0.50, 0.94], [x,y-0.23,0], lightSteel);
    for (const z of [-0.28, 0, 0.28]) rod(group, [-0.57,y+0.04,z], [0.57,y+0.04,z], 0.018, edge);
  }
}

function inventory(group, radius, bottom, height) {
  const body = new T.Mesh(new T.CylinderGeometry(radius, radius, 1, 54, 1, true), liquid);
  const top = new T.Mesh(new T.CircleGeometry(radius, 54), surface);
  top.rotation.x = -Math.PI / 2;
  const line = new T.LineLoop(new T.BufferGeometry().setFromPoints(
    Array.from({ length: 79 }, (_, i) => {
      const angle = i / 79 * Math.PI * 2;
      return new T.Vector3(radius * 1.06 * Math.cos(angle), 0, radius * 1.06 * Math.sin(angle));
    }),
  ), goalMaterial);
  line.computeLineDistances();
  // Label anchor rides on the liquid surface at the left rim.
  const anchor = new T.Object3D();
  anchor.position.set(-radius * 0.72, bottom, radius * 0.5);
  group.add(body, top, line, anchor);
  return {
    shown: null, target: null, anchor,
    draw(pv, goal, dt) {
      this.shown = this.shown === null ? pv : T.MathUtils.lerp(this.shown, pv, 1 - Math.exp(-8 * dt));
      const filled = Math.max(0.003, limit(this.shown / 100)) * height;
      body.scale.y = filled;
      body.position.y = bottom + filled / 2;
      top.position.y = bottom + filled;
      anchor.position.y = bottom + filled;
      line.visible = Number.isFinite(goal);
      if (line.visible) line.position.y = bottom + limit(goal / 100) * height;
    },
  };
}

export function buildInternals(groups, equipment) {
  const interiors = {};
  for (const key of Object.keys(equipment)) {
    const group = new T.Group();
    group.visible = false;
    groups[key].add(group);
    interiors[key] = group;
  }
  const rotor = agitator(interiors.reactor);
  coil(interiors.reactor, 4, [1.47,3.42,0], [1.47,2.03,0], 1.07, water);
  const sep = interiors.separator;
  box(sep, [1.18,0.09,0.42], [-0.59,2.89,0], lightSteel);
  box(sep, [0.08,0.64,0.77], [-0.30,2.89,0], edge);
  for (let i = 0; i < 8; i++) rod(sep, [-0.63,4.25,-0.48+i*0.135], [0.63,4.25,-0.48+i*0.135], 0.029, edge);
  trayStack(interiors.stripper);

  const condenser = interiors.condenser;
  for (const [y,z] of [[3.29,-0.21],[3.29,0],[3.29,0.21],[3.57,-0.21],[3.57,0],[3.57,0.21]]) {
    pipe(condenser, [[0.88,3.24,-0.55],[0.84,y,z],[-0.78,y,z],[-0.95,3.22,-0.56]], water, 0.036, 26);
  }
  coil(interiors.reboiler, 5, [0.38,1.24,0], [0.27,0.83,0.27], 0.26, steam, 0.8);
  rod(interiors.reboiler, [0,0.44,0],[0,3.09,0], 0.073, edge);
  const levels = {
    reactor: { tag: 8, meter: inventory(interiors.reactor, 1.32, 0.95, 2.56) },
    separator: { tag: 12, meter: inventory(sep, 1.075, 0.89, 1.96) },
    stripper: { tag: 15, meter: inventory(interiors.stripper, 0.94, 0.86, 1.60) },
  };
  const fixedAnchor = (group, position) => {
    const anchor = new T.Object3D();
    anchor.position.set(...position);
    group.add(anchor);
    return anchor;
  };
  // Anchors sit on the modeled parts: shaft above the top impeller and the outer coil turn.
  const anchors = {
    'XMEAS(8)': levels.reactor.meter.anchor,
    'XMV(12)': fixedAnchor(interiors.reactor, [0, 4.02, 0]),
    'XMEAS(21)': fixedAnchor(interiors.reactor, [1.07, 2.32, 0.35]),
    'XMEAS(12)': levels.separator.meter.anchor,
    'XMEAS(15)': levels.stripper.meter.anchor,
  };
  const parts = Object.entries(INTERIOR_PARTS).flatMap(([area, list]) =>
    list.map(([tag, name]) => ({ area, tag, name, anchor: anchors[tag] })));
  return {
    parts,
    // Show every interior, or only the selected unit's section when `only` names one.
    show(enabled, only = '') {
      for (const [key, interior] of Object.entries(interiors)) {
        const on = enabled && (!only || only === key);
        interior.visible = on;
        for (const shell of equipment[key].casing) {
          shell.material = on ? glass : steel;
          shell.castShadow = !on;
        }
      }
      return Object.keys(interiors).filter((key) => interiors[key].visible);
    },
    visible(area) { return Boolean(interiors[area]?.visible); },
    animate(status, dt, moving) {
      if (!status) return;
      for (const [key, { meter, tag }] of Object.entries(levels)) {
        const channel = codeFor(tag);
        const pv = status.native_values[channel] ?? status.values[channel];
        if (Number.isFinite(pv)) meter.draw(pv, status.setpoints[`${key}_level`], dt);
        const pressureTag = codeFor(CORE_PRESSURES[key]);
        const p = status.values[pressureTag];
        if (Number.isFinite(p)) equipment[key].needle.rotation.z = 2.18 - limit(p / 3800) * 4.36;
      }
      if (moving) {
        const rpm = 150 + (status.actuators?.['XMV(12)'] ?? status.values['XMV(12)'] ?? 50);
        rotor.rotation.y += dt * rpm * Math.PI / 120;
      }
    },
  };
}
