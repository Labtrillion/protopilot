import { CurvePath, LineCurve3, QuadraticBezierCurve3, Vector3 } from 'three';
import { ANCHORS, BODIES, BOUNDARY_PORTS, FEEDS, FLOW_READOUTS, JUNCTIONS, ROUTES, SAMPLES, VALVES, VIEWBOX } from './schematic.js';
import { FEED_TAGS, STREAMS } from './topology.js';

const pixelsPerUnit = 28;
const sinPitch = 0.6;
const cosPitch = 0.8;
const [width, height] = VIEWBOX;

export const OVERVIEW = {
  target: [0, 2.4, 0],
  offset: [0, sinPitch * 35, cosPitch * 35],
  halfWidth: width / (2 * pixelsPerUnit),
  halfHeight: height / (2 * pixelsPerUnit),
};

// The camera sees the drawing coordinates at every elevation. Elevation determines depth.
export function scenePoint([x, y], elevation) {
  const z = ((y - height / 2) / pixelsPerUnit + cosPitch * (elevation - OVERVIEW.target[1])) / sinPitch;
  return new Vector3((x - width / 2) / pixelsPerUnit, elevation, z);
}

export const VESSELS = {
  reactor: { radius: 1.47, bottom: 0.9, top: 5.28, deck: 4.35, seams: [2.5, 3.65], agitator: true, levelTag: 8 },
  separator: { radius: 1.19, bottom: 0.84, top: 4.55, deck: 3.4, seams: [2.31], levelTag: 12 },
  stripper: { radius: 1.04, bottom: 0.83, top: 7.22, deck: 4.45, seams: [1.85, 3.36, 5.56, 6.4], levelTag: 15 },
  reboiler: { radius: 0.38, bottom: 0.51, top: 2.93, seams: [1.25, 2.24] },
};

function vesselLayout(area, { radius, top, bottom }) {
  const [x, y, w, h] = BODIES[area];
  const sx = w / (pixelsPerUnit * 2 * radius);
  const sz = sx * 0.76;
  const projectedDepth = sinPitch * radius * sz;
  const cylinder = cosPitch * (top - bottom);
  const cap = (factor, s) => Math.hypot(projectedDepth, cosPitch * radius * factor * s);
  // Fit the projected cylinder and its two ellipsoid caps to the drawn body.
  let sy = (h / pixelsPerUnit - 2 * projectedDepth) / cylinder;
  for (let i = 0; i < 8; i++) sy = (h / pixelsPerUnit - cap(0.42, sy) - cap(0.37, sy)) / cylinder;
  const position = scenePoint([x + w / 2, y + cap(0.42, sy) * pixelsPerUnit], top * sy).setY(0);
  const domeY = (factor) => cosPitch * (radius * factor * sy) ** 2 / cap(factor, sy);
  return {
    position, scale: [sx, sy, sz],
    topPort: top * sy + domeY(0.42), bottomPort: bottom * sy - domeY(0.37),
  };
}

function centeredModel(area, elevation, scale = [1, 1, 1], center) {
  const [x, y, w, h] = BODIES[area];
  return { position: scenePoint(center ?? [x + w / 2, y + h / 2], elevation).setY(0), scale };
}

const feedElevation = 0.8;
const compressorElevation = 1.13;
const pumpElevation = 0.70;
const condenserElevation = 3.43;
const purgeElevation = 1.2;
const waterElevation = 0.52;
const feedSkidScale = 0.65;

function analyzerModel(area, [, , [x, y]]) {
  const [, top, , height] = BODIES[area];
  const centerY = top + height / 2;
  const scaleY = (centerY - y) / (pixelsPerUnit * cosPitch * (1.84 - 0.87));
  return centeredModel(area, 0.87 * scaleY, [1.4, scaleY, 1], [x, centerY]);
}

export const EQUIPMENT_LAYOUT = {
  ...Object.fromEntries(Object.entries(VESSELS).map(([area, spec]) => [area, vesselLayout(area, spec)])),
  condenser: centeredModel('condenser', condenserElevation, [1.14, 1, 1], [473, 164]),
  compressor: centeredModel('compressor', compressorElevation, [1, 1, 1], [604, 76]),
  pump_sep: centeredModel('pump_sep', pumpElevation),
  pump_prod: centeredModel('pump_prod', pumpElevation),
  ...Object.fromEntries(Object.entries(SAMPLES).map(([area, sample]) => [area, analyzerModel(area, sample)])),
  feeds: {
    position: scenePoint(ROUTES['02'][0], feedElevation).add(new Vector3(-1.5 * feedSkidScale, -feedElevation, 0)),
    scale: [feedSkidScale, 1, 1],
  },
  purge: { position: scenePoint(ROUTES['09'].at(-1), purgeElevation).setY(0), terminalHeight: purgeElevation },
  product: { position: scenePoint(ROUTES['11'].at(-1), pumpElevation).setY(0), terminalHeight: pumpElevation },
};

const sidePort = (area, [, y]) => OVERVIEW.target[1] +
  (sinPitch * EQUIPMENT_LAYOUT[area].position.z - (y - height / 2) / pixelsPerUnit) / cosPitch;

const contact = (area, id, end) => sidePort(area, ROUTES[id].at(end === 'start' ? 0 : -1));
const { reactor, separator, stripper, reboiler } = EQUIPMENT_LAYOUT;
const reactorOut = reactor.topPort;
const stripperOut = stripper.topPort;
const stripperFeed = contact('stripper', '04', 'end');
const stripperLiquid = contact('stripper', '10', 'end');
const reboilerSteam = contact('reboiler', 'ST', 'end');
const condensate = contact('reboiler', 'CD', 'start');
const condenserWater = condenserElevation - cosPitch * (184 - 164) / pixelsPerUnit;
const purgeTee = 2.12;

// Values follow the route vertices in the schematic, including points shared by branches.
const elevations = {
  '01': [feedElevation, feedElevation],
  '02': [feedElevation, feedElevation],
  '03': [feedElevation, feedElevation],
  'M': [feedElevation, feedElevation, feedElevation, feedElevation],
  '04': [feedElevation, feedElevation, stripperFeed, stripperFeed],
  '05': [stripperOut, stripperOut + 0.65, stripperOut + 0.65, compressorElevation],
  '06': [feedElevation, contact('reactor', '06', 'end'), contact('reactor', '06', 'end')],
  '07': [reactorOut, reactorOut + 0.50, reactorOut + 0.50, condenserElevation],
  '07B': [condenserElevation, condenserElevation, contact('separator', '07B', 'end'), contact('separator', '07B', 'end')],
  '08': [separator.topPort, purgeTee, compressorElevation, compressorElevation],
  '08I': [compressorElevation, compressorElevation],
  '08R': [compressorElevation, compressorElevation, compressorElevation],
  '08M': [compressorElevation, compressorElevation, feedElevation],
  '08B': [compressorElevation, 1.75, 1.75, compressorElevation],
  '09': [purgeTee, purgeElevation, purgeElevation],
  '10S': [separator.bottomPort, pumpElevation, pumpElevation],
  '10': [pumpElevation, pumpElevation, stripperLiquid, stripperLiquid],
  '11S': [stripper.bottomPort, 0.34, pumpElevation, pumpElevation],
  '11': [pumpElevation, pumpElevation, pumpElevation],
  'CWS': [waterElevation, waterElevation],
  'CWR': [waterElevation, waterElevation],
  '12I': [waterElevation, contact('reactor', '12I', 'end'), contact('reactor', '12I', 'end')],
  '12': [contact('reactor', '12', 'start'), contact('reactor', '12', 'start'), waterElevation, waterElevation],
  '13I': [waterElevation, waterElevation, condenserWater],
  '13': [condenserWater, waterElevation, waterElevation, waterElevation],
  'ST': [reboilerSteam, reboilerSteam],
  'CD': [condensate, condensate],
  'RB-I': [0.34, 0.34, reboiler.bottomPort],
  'RB-R': [reboiler.topPort, reboiler.topPort + 0.24, reboiler.topPort + 0.24,
    contact('stripper', 'RB-R', 'end'), contact('stripper', 'RB-R', 'end')],
};

const joinedCoordinates = new Set([...JUNCTIONS, ...Object.values(SAMPLES).map(([, point]) => point)].map(String));
const joinedPoints = new WeakSet();
export const ROUTES_3D = Object.fromEntries(STREAMS.map(({ id }) => [id, ROUTES[id].map((point, i) => {
  const position = scenePoint(point, elevations[id][i]);
  if (joinedCoordinates.has(String(point))) joinedPoints.add(position);
  return position;
})]));

export const FEED_OFFSETS = FEEDS.map(([id]) => ROUTES_3D[id][0].z - EQUIPMENT_LAYOUT.feeds.position.z);

function localPorts(area, ...points) {
  const { position, scale } = EQUIPMENT_LAYOUT[area];
  return points.map((point) => point.clone().sub(position).divide(new Vector3(...scale)).toArray());
}

export const INTERNAL_NOZZLES = {
  reactor: localPorts('reactor', ROUTES_3D['12I'].at(-1), ROUTES_3D['12'][0]),
  condenser: localPorts('condenser', ROUTES_3D['13I'].at(-1), ROUTES_3D['13'][0]),
  reboilerSteam: localPorts('reboiler', ROUTES_3D.ST.at(-1), ROUTES_3D.CD[0]),
  reboilerProcess: localPorts('reboiler', ROUTES_3D['RB-I'].at(-1), ROUTES_3D['RB-R'][0]),
};

export const SAMPLE_ROUTES_3D = Object.fromEntries(Object.entries(SAMPLES).map(([area, [id, tap, end]]) => {
  const index = ROUTES[id].findIndex(([x, y]) => x === tap[0] && y === tap[1]);
  return [area, [ROUTES_3D[id][index], scenePoint(end, 1.84 * EQUIPMENT_LAYOUT[area].scale[1])]];
}));

export const LABEL_POINTS = Object.fromEntries(Object.entries(ANCHORS).map(([area, point]) => {
  // HTML tags sit beside their symbols with clearance for the larger pressure readouts.
  const [x, y] = point;
  if (area === 'compressor') return [area, scenePoint([x, y + 40], 1)];
  if (area === 'product') return [area, scenePoint([x + 16, y + 15], 1)];
  return [area, scenePoint([x, y + (area === 'reboiler' ? 18 : 7)], 1)];
}));

export const PORTS_3D = [
  ...FEEDS.map(([id, name]) => [name, scenePoint([55, ROUTES[id][0][1]], feedElevation + 0.7), 'gas', '', FEED_TAGS[id]]),
  ...BOUNDARY_PORTS.map(([id, name, end]) => {
    const { media, circuit = '' } = STREAMS.find((stream) => stream.id === id);
    const [x, y] = ROUTES[id].at(end === 'start' ? 0 : -1);
    const point = ROUTES_3D[id].at(end === 'start' ? 0 : -1);
    const dx = circuit === 'cooling' ? -24 : 65;
    const dy = id === '11' ? 28 : circuit ? 0 : 18;
    return [name, scenePoint([x + dx, y + dy], point.y + 0.4), media, circuit];
  }),
];

export const FLOW_POINTS = Object.fromEntries(Object.entries(FLOW_READOUTS).map(([id, point]) =>
  [id, scenePoint(point, ROUTES_3D[id][0].y)]));

export function pipePath(points) {
  const path = new CurvePath();
  let start = points[0];
  for (let i = 1; i < points.length - 1; i++) {
    const corner = points[i];
    const before = points[i - 1].clone().sub(corner);
    const after = points[i + 1].clone().sub(corner);
    // Keep exact vertices at tees and taps so the branches meet the centerline.
    const radius = joinedPoints.has(corner) ? 0 : Math.min(0.16, before.length() * 0.30, after.length() * 0.30);
    const entry = corner.clone().add(before.setLength(radius));
    const exit = corner.clone().add(after.setLength(radius));
    if (start.distanceToSquared(entry) > 1e-8) path.add(new LineCurve3(start, entry));
    if (radius) path.add(new QuadraticBezierCurve3(entry, corner, exit));
    start = exit;
  }
  path.add(new LineCurve3(start, points.at(-1)));
  return path;
}

export function valvePosition({ id, valve }) {
  const [, x, y] = VALVES.find(([number]) => number === valve);
  const route = ROUTES[id], points = ROUTES_3D[id];
  for (let i = 1; i < route.length; i++) {
    const [ax, ay] = route[i - 1], [bx, by] = route[i];
    if (x < Math.min(ax, bx) || x > Math.max(ax, bx) || y < Math.min(ay, by) || y > Math.max(ay, by)) continue;
    const fraction = ax === bx ? (y - ay) / (by - ay) : (x - ax) / (bx - ax);
    return {
      point: points[i - 1].clone().lerp(points[i], fraction),
      tangent: points[i].clone().sub(points[i - 1]).normalize(),
    };
  }
  throw new Error(`XMV(${valve}) is outside stream ${id}`);
}
