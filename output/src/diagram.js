import { Vector3 } from 'three';
import { ANALYZER_TAPS, AREAS, CORE_PRESSURES, MEDIA, PIPE_JUNCTIONS, STREAMS, codeFor, streamMatchesTag } from './topology.js';
import { OVERVIEW, VESSELS, pipePath, sampleRoute, valvePosition } from './layout.js';
import { reading } from './ui.js';

// Use the same orthographic axes and framing as the default 3D overview.
const UNITS = 25;
const WIDTH = 2 * OVERVIEW.halfWidth * UNITS;
const HEIGHT = 2 * OVERVIEW.halfHeight * UNITS;
const origin = new Vector3(...OVERVIEW.target);
const eye = new Vector3(...OVERVIEW.offset).normalize();
const right = new Vector3(0, 1, 0).cross(eye).normalize();
const up = eye.clone().cross(right);
const streamPaths = new Map(STREAMS.map(({ id, points }) => [id, pipePath(points)]));

function project(point) {
  return [WIDTH / 2 + UNITS * (right.dot(point) - right.dot(origin)),
    HEIGHT / 2 - UNITS * (up.dot(point) - up.dot(origin))];
}

function atArea(area, elevation) {
  const [x, y, z] = AREAS[area].position;
  return project(new Vector3(x, y + elevation, z));
}

function svgPath(path) {
  const point = (vector) => project(vector).map((n) => n.toFixed(2)).join(' ');
  return `M${point(path.getPoint(0))}` + path.curves.map((curve) =>
    curve.type === 'LineCurve3' ? `L${point(curve.v2)}` : `Q${point(curve.v1)} ${point(curve.v2)}`).join('');
}

const rect = (x, y, w, h, r = 4) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/>`;
const path = (d) => `<path d="${d}"/>`;
const symbol = (area, shape) => `<g class="pfd-symbol" data-symbol="${area}">${shape}</g>`;

const vesselBodies = Object.fromEntries(Object.entries(VESSELS).map(([area, spec]) => {
  const [cx, bottom] = atArea(area, spec.bottom - spec.radius * 0.37);
  const [, top] = atArea(area, spec.top + spec.radius * 0.42);
  const r = spec.radius * UNITS;
  return [area, [cx - r, top, 2 * r, bottom - top, r * 0.74]];
}));

function vesselSymbol(area, [x, y, width, height, radius]) {
  const rows = area === 'stripper' ? [0.22, 0.30, 0.38, 0.46] : area === 'reboiler' ? [0.34, 0.43, 0.52, 0.61] : [];
  const details = rows.map((row) => `M${x + 3} ${y + height * row}h${width - 6}`).join('');
  const agitator = area === 'reactor'
    ? `M${x + width / 2} ${y}v${height * 0.70}m-16 0h32m-16-12h-14m14 0h14`
    : '';
  return symbol(area, rect(x, y, width, height, radius) + (details || agitator ? path(details + agitator) : ''));
}

function rotarySymbol(area, elevation, from, to, radius) {
  const [x, y] = atArea(area, elevation);
  const [x1, y1] = project(from), [x2, y2] = project(to);
  const rotation = Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI;
  return symbol(area, `<g transform="translate(${x} ${y}) rotate(${rotation})">
    <circle r="${radius}"/>${path(`M${-radius * 0.35} ${-radius * 0.5}L${radius * 0.6} 0 ${-radius * 0.35} ${radius * 0.5}Z`)}</g>`);
}

function equipment() {
  const [cx, cy] = atArea('condenser', 3.43);
  const yaw = Math.atan2(-up.x, right.x) * 180 / Math.PI;
  const condenser = `<g transform="translate(${cx} ${cy}) rotate(${yaw})">
    ${rect(-48, -17, 96, 34, 14)}${path('M-34-17v34M34-17v34M-27-4h54m-54 8h54')}</g>`;
  return [
    ...Object.entries(vesselBodies).map(([area, body]) => vesselSymbol(area, body)),
    symbol('condenser', condenser),
    rotarySymbol('compressor', 1.13, streamPaths.get('08I').getPoint(1), streamPaths.get('08R').getPoint(0), 27),
    rotarySymbol('pump_sep', 0.70, streamPaths.get('10S').getPoint(1), streamPaths.get('10').getPoint(0), 11),
    rotarySymbol('pump_prod', 0.70, streamPaths.get('11S').getPoint(1), streamPaths.get('11').getPoint(0), 11),
    ...Object.keys(ANALYZER_TAPS).map((area) => {
      const [x, y] = atArea(area, 0.87);
      return symbol(area, rect(x - 10, y - 14, 20, 28, 3) + path(`M${x - 6} ${y - 7}h12v7h-12Z`));
    }),
  ].join('');
}

// Keep labels beside the projected equipment, and offset text from the lines it annotates.
const anchors = Object.entries({
  feeds: [1.6, 0, -62], reactor: [6.66, 0, -7], condenser: [4.19, 0, -8],
  separator: [5.13, 26, -4], compressor: [1.13, -1, 47], stripper: [7.66, -26, -6],
  reboiler: [2.93, 10, -12], purge: [1.2, 0, -17], product: [0.70, 0, 34],
  pump_sep: [0.70, -25, 21], pump_prod: [0.70, -28, 20],
  gc_feed: [0.87, -27, 9], gc_purge: [0.87, 25, -1], gc_product: [0.87, -36, 4],
}).map(([area, [height, dx, dy]]) => {
  const [x, y] = atArea(area, height);
  return [area, [x + dx, y + dy]];
});
const anchorsByArea = new Map(anchors);

const valves = STREAMS.filter((stream) => stream.valve).map(({ id, valve: [number, distance] }) => {
  const { point, tangent } = valvePosition(streamPaths.get(id), distance);
  const [x, y] = project(point);
  const degrees = Math.atan2(-up.dot(tangent), right.dot(tangent)) * 180 / Math.PI;
  // Keep the actuator handle above a near-horizontal pipe in either flow direction.
  const rotation = ((degrees + 270) % 180) - 90;
  return [number, x, y, rotation];
});
const LEVELS = Object.fromEntries(Object.entries(VESSELS).filter(([, spec]) => spec.levelTag).map(([area, spec]) => {
  const body = vesselBodies[area];
  const [, , , height] = body;
  return [area, { tag: spec.levelTag, body, datum: body[1] + height - 4, range: height * 0.51 }];
}));

const portEnds = [
  ['01', 'A', 'start', 'left'], ['02', 'D', 'start', 'left'],
  ['03', 'E', 'start', 'left'], ['04', 'A/C', 'start', 'left'],
  ['CWS', 'CWS', 'start', 'left'], ['CWR', 'CWR', 'end', 'left'],
  ['09', 'PURGE', 'end', 'right'],
  ['CD', 'COND', 'end', 'right', 39, -14], ['11', 'PRODUCT', 'end', 'right', 39, 0],
  ['ST', 'STM', 'start', 'right', 53, 18],
];

const meters = [
  ['05', 0.40, 2, -8], ['06', 0.20, 0, -8], ['07', 0.41, 0, -6], ['08', 0.27, -10, -7],
  ['09', 0.77, 0, -7], ['10', 0.57, -23, -5], ['11', 0.76, 0, -7],
  ['12', 0.26, -22, -4], ['13', 0.48, 0, -7],
];
const flowReadouts = [
  ['06', 0.02, 7, 17], ['08R', 0.51, -34, -7], ['10', 0.10, 17, 17], ['11', 0.02, 24, -10],
];

function annotation([id, fraction, dx, dy]) {
  const [x, y] = project(streamPaths.get(id).getPointAt(fraction));
  return [id, x + dx, y + dy];
}

export function mountDiagram(host, selectArea, selectCircuit, openValve) {
  const lines = STREAMS.map((stream) => {
    const d = svgPath(streamPaths.get(stream.id));
    const { colorToken } = MEDIA[stream.media];
    const hit = stream.circuit ? `<path class="pipe-hit" d="${d}" vector-effect="non-scaling-stroke"/>` : '';
    return `<g class="pfd-stream" data-stream="${stream.id}" data-circuit="${stream.circuit ?? ''}" style="--pipe:var(${colorToken})">
      <title>${stream.id} · ${stream.from} → ${stream.to}</title>
      <path class="pipe-sleeve" d="${d}"/><path class="pipe-line" d="${d}" marker-end="url(#arrow-${stream.media})"/>
      <path class="pipe-ticks" d="${d}"/>${hit}</g>`;
  }).join('');
  const arrows = Object.entries({ ...MEDIA, tracked: { colorToken: '--tracked' }, muted: { colorToken: '--pipe-muted' } }).map(([id, { colorToken }]) =>
    `<marker id="arrow-${id}" viewBox="0 0 9 8" refX="8" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 9 4 0 8 2 4Z" fill="var(${colorToken})"/></marker>`).join('');
  const portsMarkup = portEnds.map(([id, name, end, side, dx = 0, dy = 0]) => {
    const [x, y] = project(streamPaths.get(id).getPoint(end === 'start' ? 0 : 1));
    const width = name.length > 4 ? 59 : 33;
    const left = x + dx - (side === 'left' ? width : 0);
    const baseline = y + dy;
    const leader = dx || dy ? `<path d="M${x} ${y}L${x + dx - 12} ${baseline}H${x + dx}"/>` : '';
    return `<g class="pfd-port">${leader}${rect(left, baseline - 8, width, 16, 3)}
      <text x="${left + width / 2}" y="${baseline + 4}">${name}</text></g>`;
  }).join('');
  const meterMarkup = meters.map(annotation).map(([id, x, y]) => `<text class="pfd-id" x="${x}" y="${y}">#${id}</text>`).join('');
  const target = '<rect class="pfd-hit" aria-hidden="true" rx="3"/>';
  const valveMarkup = valves.map(([id, x, y, r]) => `<g class="pfd-valve" data-valve="XMV(${id})" tabindex="0" role="button" aria-label="XMV(${id}) 밸브 조정" transform="translate(${x} ${y}) rotate(${r})">
    ${target}<path d="M-8-5 8 5V-5L-8 5ZM0 0v-8m-4 0h8"/></g>`).join('');
  const labels = anchors.map(([id, [x, y]]) => {
    const { code, name } = AREAS[id];
    return `<g class="pfd-unit" role="button" tabindex="0" data-area="${id}" aria-label="${code} ${name}" transform="translate(${x} ${y})">
      <title>${name}</title>${target}<rect class="pfd-chip" x="-22" y="-12" width="44" height="17" rx="3"/><text y="0">${code}</text></g>`;
  }).join('');
  const levelClips = Object.entries(LEVELS).map(([id, { body }]) => `<clipPath id="level-clip-${id}">${rect(...body)}</clipPath>`).join('');
  const levelLayers = Object.entries(LEVELS).map(([id, { body, datum }]) => {
    const [x, , w] = body;
    return `<g class="pfd-level" data-level="${id}" clip-path="url(#level-clip-${id})" aria-hidden="true">
      <rect x="${x}" y="${datum}" width="${w}" height="0"/><line x1="${x + 4}" x2="${x + w - 4}" y1="${datum}" y2="${datum}"/></g>`;
  }).join('');
  const readouts = anchors.filter(([id]) => id in CORE_PRESSURES).map(([id, [x, y]]) =>
    `<text class="pfd-pressure" data-pressure="${id}" x="${x + 28}" y="${y + (id === 'reactor' ? 12 : 0)}"></text>`
  ).join('');
  const flowMarkup = flowReadouts.map(annotation).map(([id, x, y]) =>
    `<text class="pfd-flow-reading" data-flow-reading="${id}" x="${x}" y="${y}"></text>`
  ).join('');
  const samples = Object.keys(ANALYZER_TAPS).map((area) => path(svgPath(pipePath(sampleRoute(area))))).join('');
  const junctions = PIPE_JUNCTIONS.map((point) => {
    const [x, y] = project(new Vector3(...point));
    return `<circle cx="${x}" cy="${y}" r="3"/>`;
  }).join('');
  host.innerHTML = `<svg role="group" aria-label="Tennessee Eastman 공정 흐름도" class="pfd-svg" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <defs>${arrows}${levelClips}</defs>${lines}
    <g class="pfd-samples">${samples}</g><g class="pfd-junctions">${junctions}</g>
    ${equipment()}${levelLayers}${valveMarkup}${portsMarkup}${meterMarkup}${labels}${readouts}${flowMarkup}</svg>`;
  const svg = host.firstElementChild;
  const targets = [...svg.querySelectorAll('.pfd-hit')];
  const largerTargets = matchMedia('(max-width: 600px), (pointer: coarse)');
  function sizeTargets() {
    const box = svg.viewBox.baseVal;
    const scale = Math.min(svg.clientWidth / box.width, svg.clientHeight / box.height);
    if (!scale) return;
    const side = (largerTargets.matches ? 36 : 28) / scale;
    for (const rect of targets) {
      const width = Math.max(rect.parentElement.classList.contains('pfd-unit') ? 44 : 24, side);
      const height = Math.max(20, side);
      rect.setAttribute('width', width);
      rect.setAttribute('height', height);
      rect.setAttribute('x', -width / 2);
      rect.setAttribute('y', -height / 2 - 3);
    }
  }
  new ResizeObserver(sizeTargets).observe(svg);
  largerTargets.addEventListener('change', sizeTargets);

  function activate(node) {
    const valve = node.closest('[data-valve]');
    const unit = node.closest('.pfd-unit, .pfd-symbol');
    const stream = node.closest('[data-circuit]');
    if (valve) openValve(valve.dataset.valve);
    else if (unit) selectArea(unit.dataset.area ?? unit.dataset.symbol);
    else if (stream?.dataset.circuit) selectCircuit(stream.dataset.circuit);
  }
  svg.addEventListener('click', (event) => {
    let node = event.target;
    if (node.classList.contains('pfd-hit')) {
      let distance = Infinity;
      // Hit regions can overlap in a fitted diagram. Choose the closest visible label or valve.
      for (const hit of document.elementsFromPoint(event.clientX, event.clientY)) {
        if (!hit.classList.contains('pfd-hit') || !svg.contains(hit)) continue;
        const rect = hit.getBoundingClientRect();
        const d = Math.hypot(rect.x + rect.width / 2 - event.clientX, rect.y + rect.height / 2 - event.clientY);
        if (d < distance) { node = hit; distance = d; }
      }
    }
    activate(node);
  });
  svg.addEventListener('keydown', (event) => {
    if (!(event.key === 'Enter' || event.key === ' ') || !event.target.matches('.pfd-unit, .pfd-valve')) return;
    event.preventDefault();
    activate(event.target);
  });
  const levelByArea = [...svg.querySelectorAll('[data-level]')].map((node) => ({
    area: node.dataset.level, rect: node.querySelector('rect'), line: node.querySelector('line'),
  }));
  const flowByStream = new Map([...svg.querySelectorAll('[data-flow-reading]')].map((node) => [node.dataset.flowReading, node]));
  const linesById = new Map([...svg.querySelectorAll('.pfd-stream')].map((line) => [line.dataset.stream, line]));
  return {
    focus(area, reset, tag) {
      for (const unit of svg.querySelectorAll('[data-area],[data-symbol]')) unit.classList.toggle('selected', (unit.dataset.area || unit.dataset.symbol) === area);
      if (reset) host.scrollLeft = 0;
      else if (anchorsByArea.has(area) && !host.hidden) {
        const valve = valves.find(([id]) => `XMV(${id})` === tag);
        const x = valve?.[1] ?? anchorsByArea.get(area)[0];
        const scale = Math.min(svg.clientWidth / WIDTH, svg.clientHeight / HEIGHT);
        const center = svg.clientWidth / 2 + (x - WIDTH / 2) * scale;
        host.scrollLeft = Math.max(0, center - host.clientWidth / 2);
      }
    },
    track(area, tag) {
      for (const unit of svg.querySelectorAll('[data-area],[data-symbol],[data-pressure]')) {
        const tracked = unit.dataset.pressure ? codeFor(CORE_PRESSURES[unit.dataset.pressure]) === tag : (unit.dataset.area || unit.dataset.symbol) === area;
        unit.classList.toggle('tracked', tracked);
      }
      for (const valve of svg.querySelectorAll('[data-valve]')) valve.classList.toggle('tracked', valve.dataset.valve === tag);
      for (const line of STREAMS) {
        const tracked = streamMatchesTag(line, tag);
        linesById.get(line.id).classList.toggle('tracked', tracked);
        flowByStream.get(line.id)?.classList.toggle('tracked', tracked);
      }
    },
    circuit(id) {
      for (const line of linesById.values()) {
        line.classList.toggle('out-of-circuit', Boolean(id) && line.dataset.circuit !== id);
      }
    },
    flow(on) { svg.classList.toggle('show-flow', on); },
    pressure(on) { svg.classList.toggle('show-pressure', on); },
    update(status) {
      // Displayed numbers carry a visible marker while a sensor test offset is active.
      const testMark = (tag) => (tag && tag in status.offsets ? ' · 시험' : '');
      svg.classList.toggle('running', status.running);
      for (const stream of STREAMS) {
        const tag = codeFor(stream.tag);
        const value = status.values[tag];
        const measured = status.native_values[tag] ?? value;
        const ratio = tag && Number.isFinite(measured) ? Math.max(0, measured / stream.nominal) : 0.57;
        const line = linesById.get(stream.id);
        line.classList.toggle('no-flow', ratio < 0.015);
        line.style.setProperty('--speed', `${Math.min(5, 1.5 / Math.max(0.28, ratio))}s`);
        const label = flowByStream.get(stream.id);
        if (label) label.textContent = `${reading(value)} ${stream.media === 'gas' ? 'kscm/h' : 'm³/h'}${testMark(tag)}`;
      }
      for (const node of svg.querySelectorAll('.pfd-unit,.pfd-symbol,.pfd-pressure')) {
        const area = node.dataset.area ?? node.dataset.symbol ?? node.dataset.pressure;
        node.classList.toggle('in-alarm', status.alarms.some((alarm) => alarm.area === area));
      }
      const normalized = (value) => Math.max(0, Math.min(100, value ?? 0)) / 100;
      for (const { area, rect, line } of levelByArea) {
        const { tag, datum, range } = LEVELS[area];
        const channel = codeFor(tag);
        const fill = range * normalized(status.native_values[channel] ?? status.values[channel]);
        rect.setAttribute('height', fill);
        rect.setAttribute('y', datum - fill);
        const y = datum - range * normalized(status.setpoints[`${area}_level`]);
        line.setAttribute('y1', y);
        line.setAttribute('y2', y);
      }
      for (const node of svg.querySelectorAll('.pfd-pressure')) {
        const tag = codeFor(CORE_PRESSURES[node.dataset.pressure]);
        node.textContent = `${reading(status.values[tag])} kPa(g)${testMark(tag)}`;
      }
    },
  };
}
