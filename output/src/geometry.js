import * as T from 'three';
import { readColor } from './palette.js';

export const v3 = (coordinates) => new T.Vector3(...coordinates);
const material = (color, roughness = 0.55, metalness = 0.18) =>
  new T.MeshStandardMaterial({ color, roughness, metalness });

export const steel = material(0xa4a9ae, 0.59, 0.28);
export const lightSteel = material(0xbfc4c9, 0.62, 0.22);
export const edge = material(0x596067, 0.60, 0.20);
export const dark = material(0x454b51, 0.74);
export const base = material(readColor('--equipment-fill'), 0.81);
export const hardware = material(0x777e85);
const valveColor = material(0x666d74);
const black = material(0x30363c);
const dialFace = material(readColor('--surface'), 0.57, 0.06);
const dialPointer = material(readColor('--text'), 0.55, 0.20);
const instrumentHousing = material(0x575e65);
const display = new T.MeshBasicMaterial({ color: readColor('--process-liquid'), toneMapped: false });
const UP = new T.Vector3(0, 1, 0);

function mesh(group, geometry, mat, location, shadow = false) {
  const object = new T.Mesh(geometry, mat);
  if (location) object.position.copy(v3(location));
  object.castShadow = shadow;
  object.receiveShadow = shadow;
  group.add(object);
  return object;
}

export function box(group, dimensions, position, mat = steel, shadow = false) {
  return mesh(group, new T.BoxGeometry(...dimensions), mat, position, shadow);
}
function cylinder(group, radius, height, position, mat = steel, shadow = false, segments = 28) {
  return mesh(group, new T.CylinderGeometry(radius, radius, height, segments), mat, position, shadow);
}
export function rod(group, from, to, radius, mat = steel, segments = 9) {
  const first = v3(from), last = v3(to), delta = last.clone().sub(first);
  const object = cylinder(group, radius, delta.length(), first.lerp(last, 0.5).toArray(), mat, false, segments);
  object.quaternion.setFromUnitVectors(UP, delta.normalize());
  return object;
}
function ring(group, radius, thickness, height, mat = edge) {
  const object = mesh(group, new T.TorusGeometry(radius, thickness, 7, 40), mat, [0, height, 0]);
  object.rotation.x = -Math.PI / 2;
  return object;
}
function dome(group, radius, height, center, top) {
  const object = mesh(group, new T.SphereGeometry(1, 36, 12, 0, Math.PI * 2, top ? 0 : Math.PI / 2, Math.PI / 2), steel, center, true);
  object.scale.set(radius, height, radius);
  return object;
}
function plinth(group, width, depth) {
  box(group, [width, 0.17, depth], [0, 0.04, 0], base, true);
  box(group, [width - 0.14, 0.06, depth - 0.14], [0, 0.15, 0], edge);
  for (const x of [-1, 1]) for (const z of [-1, 1]) {
    cylinder(group, 0.095, 0.065, [x * (width / 2 - 0.22), 0.215, z * (depth / 2 - 0.22)], lightSteel, false, 6);
  }
}
function gauge(group, position, scale = 1) {
  const gauge = new T.Group();
  gauge.position.copy(v3(position));
  gauge.scale.setScalar(scale);
  group.add(gauge);
  const body = cylinder(gauge, 0.16, 0.09, [0, 0, 0], edge, false, 24);
  body.rotation.x = Math.PI / 2;
  const dial = cylinder(gauge, 0.122, 0.015, [0, 0, 0.052], dialFace, false, 24);
  dial.rotation.x = Math.PI / 2;
  for (let i = 0; i <= 8; i++) {
    const a = -0.78 + i * Math.PI * 1.5 / 8;
    rod(gauge, [0.096*Math.cos(a),0.096*Math.sin(a),0.064], [0.105*Math.cos(a),0.105*Math.sin(a),0.064], 0.004, black, 5);
  }
  const needle = new T.Group();
  gauge.add(needle);
  needle.position.z = 0.070;
  rod(needle, [0, -0.018, 0], [0, 0.090, 0], 0.008, dialPointer, 6);
  rod(gauge, [0, -0.12, 0], [0, -0.27, 0], 0.037, hardware);
  return needle;
}
function railing(group, radius, height) {
  ring(group, radius, 0.11, height, edge);
  ring(group, radius, 0.044, height + 0.51, hardware);
  ring(group, radius, 0.032, height + 0.29, hardware);
  for (let i = 0; i < 14; i++) {
    const angle = i / 14 * Math.PI * 2;
    const x = radius * Math.cos(angle), z = radius * Math.sin(angle);
    rod(group, [x, height, z], [x, height + 0.51, z], 0.035, hardware, 6);
  }
}
function ladder(group, height, x, z) {
  for (const offset of [-0.2, 0.2]) rod(group, [x + offset, 0.34, z], [x + offset, height + 0.34, z], 0.044, hardware, 8);
  for (let y = 0.43; y < height + 0.18; y += 0.25) rod(group, [x - 0.2, y, z], [x + 0.2, y, z], 0.031, hardware, 6);
}
function sightGlass(group, radius, bottom, height) {
  const x = radius * 0.56, z = radius * 0.90;
  box(group, [0.18, height + 0.18, 0.12], [x, bottom + height / 2, z], dark);
  box(group, [0.085, height, 0.028], [x, bottom + height / 2, z + 0.066], instrumentHousing);
  const fill = box(group, [0.085, 1, 0.037], [x, bottom, z + 0.087], display);
  return { fill, bottom, height };
}

export function vessel(group, spec) {
  const { radius, bottom, top, deck, seams, agitator = false } = spec;
  plinth(group, radius * 2 + 0.98, radius * 2 + 0.77);
  const casing = [
    cylinder(group, radius, top - bottom, [0, (top + bottom) / 2, 0], steel, true, 48),
    dome(group, radius, radius * 0.42, [0, top, 0], true),
    dome(group, radius, radius * 0.37, [0, bottom, 0], false),
  ];
  for (const h of [bottom + 0.03, top - 0.035, ...seams]) ring(group, radius + 0.018, 0.031, h);
  if (deck) {
    railing(group, radius + 0.36, deck);
    ladder(group, deck, -radius * 0.55, radius + 0.23);
  }
  for (let i = 0; i < 4; i++) {
    const angle = (i / 4 + 0.125) * Math.PI * 2;
    const x = radius * 0.79 * Math.cos(angle), z = radius * 0.79 * Math.sin(angle);
    box(group, [0.24, bottom - 0.10, 0.24], [x, bottom / 2 + 0.11, z], edge, true);
    box(group, [0.43, 0.07, 0.36], [x, 0.21, z], dark);
  }
  cylinder(group, radius * 0.24, 0.17, [0, top + radius * 0.42 + 0.08, 0], edge);
  if (agitator) {
    cylinder(group, 0.31, 0.69, [0, top + 1.0, 0], instrumentHousing, true);
    for (let i = 0; i < 6; i++) ring(group, 0.32, 0.021, top + 0.72 + i * 0.105, edge);
    cylinder(group, 0.34, 0.11, [0, top + 1.38, 0], lightSteel);
    box(group, [0.41, 0.32, 0.42], [0.3, top + 0.99, 0], dark);
  }
  const needle = gauge(group, [radius * 0.27, top - 0.33, radius + 0.05], 1.22);
  box(group, [Math.min(0.43, radius * 0.48),0.19,0.042], [0.16,top-0.62,radius+0.04], edge);
  for (const fraction of [0.20,0.40,0.60,0.80]) {
    const y = bottom + 0.18 + Math.min(1.44, top - bottom - 0.35) * fraction;
    box(group, [0.11,0.018,0.013], [radius*0.56+0.143,y,radius*0.90+0.075], lightSteel);
  }
  return { needle, casing, ...sightGlass(group, radius, bottom + 0.18, Math.min(1.44, top - bottom - 0.35)) };
}

export function condenser(group) {
  plinth(group, 4.6, 2.2);
  for (const x of [-1.17, 1.17]) {
    box(group, [0.30, 2.82, 1.18], [x, 1.57, 0], edge, true);
    box(group, [0.52, 0.12, 1.43], [x, 0.32, 0], dark);
  }
  for (const z of [-0.45, 0.45]) rod(group, [-1.17, 0.48, z], [1.17, 2.68, z], 0.07, dark);
  const shell = rod(group, [-1.61, 3.43, 0], [1.61, 3.43, 0], 0.72, steel, 52);
  shell.castShadow = true;
  for (const [x, sign] of [[-1.62, -1], [1.62, 1]]) {
    const end = cylinder(group, 0.76, 0.15, [x, 3.43, 0], edge);
    end.rotation.z = Math.PI / 2;
    rod(group, [x, 3.43, 0], [x + sign * 0.34, 3.43, 0], 0.45, lightSteel, 24);
    for (let i = 0; i < 10; i++) {
      const angle = i / 10 * Math.PI * 2;
      const y = 3.43 + Math.cos(angle) * 0.65, z = Math.sin(angle) * 0.65;
      rod(group, [x, y, z], [x + 0.11 * sign, y, z], 0.039, hardware, 6);
    }
  }
  gauge(group, [0.23, 3.76, 0.75]);
  return { casing: [shell] };
}

export function rotary(group, kind) {
  const large = kind === 'compressor';
  const scale = large ? 1 : 0.63;
  const core = new T.Group();
  core.scale.setScalar(scale);
  group.add(core);
  plinth(core, 3.12, 1.88);
  box(core, [2.6, 0.27, 1.35], [0, 0.34, 0], dark, true);
  rod(core, [-1.13, 1.13, 0], [0.05, 1.13, 0], 0.43, instrumentHousing, 26).castShadow = true;
  for (let i = 0; i < 10; i++) {
    const x = -1.1 + i * 0.10;
    rod(core, [x, 1.13, 0], [x + 0.044, 1.13, 0], 0.478, edge, 24);
  }
  rod(core, [0.24, 1.13, 0], [1.03, 1.13, 0], 0.62, steel, 32).castShadow = true;
  rod(core, [0.88, 1.13, 0], [1.35, 1.13, 0], 0.26, edge, 22);
  box(core, [0.5, 0.3, 0.37], [-0.49, 1.62, 0], dark);
  box(core, [0.46, 0.055, 0.31], [-0.49, 1.79, 0], lightSteel);
  gauge(core, [0.58, 1.85, 0.55], 0.75);
}

export function feeds(group, offsets) {
  for (const [index, z] of offsets.entries()) {
    const skid = new T.Group();
    skid.position.z = z;
    group.add(skid);
    plinth(skid, 3.21, 1.48);
    rod(skid, [-1.07, 0.83, 0], [0.95, 0.83, 0], 0.51, steel, 32).castShadow = true;
    for (const x of [-1.1, -0.64, 0.57, 0.98]) {
      rod(skid, [x, 0.83, 0], [x + 0.07, 0.83, 0], 0.543, edge, 30);
    }
    for (const x of [-0.73, 0.67]) box(skid, [0.22, 0.36, 0.73], [x, 0.35, 0], edge);
    rod(skid, [0.96, 0.8, 0], [1.5, 0.8, 0], 0.14, hardware);
    gauge(skid, [-0.36, 1.57, 0.15], 0.74);
    box(skid, [0.28, 0.13, 0.04], [0.12 + index * 0.06, 0.9, 0.52], dark);
  }
}

export function analyzer(group) {
  plinth(group, 1.0, 0.73);
  box(group, [0.75, 1.36, 0.49], [0, 0.87, 0], steel, true);
  box(group, [0.61, 1.20, 0.045], [0, 0.87, 0.25], edge);
  box(group, [0.39, 0.26, 0.032], [0, 1.2, 0.286], dark);
  box(group, [0.28, 0.12, 0.011], [0, 1.22, 0.307], display);
  for (let i = 0; i < 4; i++) box(group, [0.45, 0.023, 0.02], [0, 0.58 + i * 0.062, 0.285], dark);
  rod(group, [0, 1.55, 0], [0, 1.84, 0], 0.042, hardware);
}

export function terminal(group, y) {
  plinth(group, 0.96, 0.96);
  rod(group, [0, 0.12, 0], [0, y, 0], 0.1, edge);
}

export function controlValve(group, point, tangent, number) {
  const valve = new T.Group();
  valve.position.copy(point);
  if (tangent.x < 0) tangent.negate();
  valve.quaternion.setFromUnitVectors(new T.Vector3(1, 0, 0), tangent);
  group.add(valve);
  valve.userData = { valve: `XMV(${number})` };
  rod(valve, [-0.24, 0, 0], [0.24, 0, 0], 0.175, valveColor, 12);
  for (const x of [-0.19, 0.14]) rod(valve, [x, 0, 0], [x + 0.06, 0, 0], 0.19, edge, 12);
  for (const x of [-0.10,0.10]) rod(valve, [x,0.10,0], [x,0.46,0], 0.023, lightSteel, 8);
  const stem = box(valve, [0.087,0.12,0.073], [0,0.27,0], dialPointer);
  valve.userData.stem = stem;
  cylinder(valve, 0.21, 0.13, [0, 0.46, 0], hardware, false, 20);
  return valve;
}

export function support(group, x, z, y) {
  box(group, [0.29, y, 0.29], [x, y / 2, z], dark);
  box(group, [0.56, 0.08, 0.55], [x, 0.05, z], base);
}


export function flange(group, point, tangent, radius, mat = edge) {
  const center = v3(point);
  const axis = v3(tangent).normalize();
  const frame = new T.Group();
  frame.position.copy(center);
  frame.quaternion.setFromUnitVectors(UP, axis);
  group.add(frame);
  cylinder(frame, radius * 1.62, 0.097, [0,0,0], mat, false, 20);
  cylinder(frame, radius * 1.45, 0.10, [0,-0.081,0], dark, false, 20);
  for (let i = 0; i < 6; i++) {
    const angle = 2 * Math.PI * i / 6;
    cylinder(frame, radius * 0.17, 0.028,
      [radius * 1.32 * Math.cos(angle),0.059,radius * 1.32 * Math.sin(angle)], lightSteel, false, 6);
  }
  return frame;
}
