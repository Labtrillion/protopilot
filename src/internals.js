import * as T from 'three';
import { steel, edge, lightSteel, hardware, box, rod, v3 } from './geometry.js';
import { CORE_PRESSURES, INTERIOR_PARTS, LEVEL_TAGS, codeFor, levelReading } from './topology.js';
import { INTERNAL_NOZZLES, VESSELS } from './layout.js';
import { readColor } from './palette.js';
import { mountVesselLevel } from './vessel-level.js';

const glass = new T.MeshPhysicalMaterial({
  color: readColor('--surface'), roughness: 0.55, metalness: 0.15,
  transparent: true, opacity: 0.11, depthWrite: false, side: T.FrontSide,
});
const water = new T.MeshStandardMaterial({
  color: readColor('--process-water'), metalness: 0.32, roughness: 0.28,
});
const steam = new T.MeshStandardMaterial({
  color: readColor('--process-steam'), metalness: 0.29, roughness: 0.28,
});
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
      const paddle = box(blade, [0.29, 0.10, 0.37], [x,y,0], hardware);
      paddle.rotation.x = 0.35;
    }
    rotor.add(blade);
  }
  return rotor;
}

function trayStack(group) {
  const plate = new T.CylinderGeometry(0.89, 0.89, 0.037, 40);
  for (const [index, y] of [2.98,3.48,3.98,4.48,4.98,5.48].entries()) {
    const tray = new T.Mesh(plate, index % 2 ? edge : hardware);
    tray.position.y = y;
    group.add(tray);
    const x = index % 2 ? 0.60 : -0.60;
    box(group, [0.13, 0.50, 0.94], [x,y-0.23,0], lightSteel);
    for (const z of [-0.28, 0, 0.28]) rod(group, [-0.57,y+0.04,z], [0.57,y+0.04,z], 0.018, edge);
  }
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
  const [coilIn, coilOut] = INTERNAL_NOZZLES.reactor;
  coil(interiors.reactor, 4, coilIn, coilOut, 1.07, water);
  const sep = interiors.separator;
  box(sep, [1.18,0.09,0.42], [-0.59,2.89,0], lightSteel);
  box(sep, [0.08,0.64,0.77], [-0.30,2.89,0], edge);
  for (let i = 0; i < 8; i++) rod(sep, [-0.63,4.25,-0.48+i*0.135], [0.63,4.25,-0.48+i*0.135], 0.029, edge);
  trayStack(interiors.stripper);

  const condenser = interiors.condenser;
  const [waterIn, waterOut] = INTERNAL_NOZZLES.condenser;
  for (const [y,z] of [[3.29,-0.21],[3.29,0],[3.29,0.21],[3.57,-0.21],[3.57,0],[3.57,0.21]]) {
    pipe(condenser, [waterIn, [-1.1,y,z], [1.17,y,z], waterOut], water, 0.036, 26);
  }
  coil(interiors.reboiler, 5, ...INTERNAL_NOZZLES.reboilerSteam, 0.26, steam, 0.8);
  rod(interiors.reboiler, ...INTERNAL_NOZZLES.reboilerProcess, 0.073, edge);
  const levels = Object.entries(LEVEL_TAGS).map(([area, tag]) => ({
    area, tag: codeFor(tag), meter: mountVesselLevel(interiors[area], VESSELS[area]),
  }));
  const fixedAnchor = (group, position) => {
    const anchor = new T.Object3D();
    anchor.position.set(...position);
    group.add(anchor);
    return anchor;
  };
  // Anchors sit on the modeled parts: shaft above the top impeller and the outer coil turn.
  const anchors = {
    ...Object.fromEntries(levels.map(({ tag, meter }) => [tag, meter.anchor])),
    'XMV(12)': fixedAnchor(interiors.reactor, [0, 4.02, 0]),
    'XMEAS(21)': fixedAnchor(interiors.reactor, [1.07, (coilIn[1] + coilOut[1]) / 2, 0.35]),
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
    update(status) {
      for (const { area, meter } of levels) {
        meter.draw(levelReading(status, area), status.setpoints[`${area}_level`]);
        const pressureTag = codeFor(CORE_PRESSURES[area]);
        const p = status.values[pressureTag];
        if (Number.isFinite(p)) equipment[area].needle.rotation.z = 2.18 - limit(p / 3800) * 4.36;
      }
    },
    animate(status, dt) {
      const rpm = 150 + (status.actuators?.['XMV(12)'] ?? status.values['XMV(12)'] ?? 50);
      rotor.rotation.y += dt * rpm * Math.PI / 120;
    },
  };
}
