import { AREAS, CORE_PRESSURES, FEED_TAGS, LEVEL_TAGS, MEDIA, STREAMS, codeFor, levelFraction, levelReading, streamMatchesTag } from './topology.js';
import { ANCHORS, BODIES, BOUNDARY_PORTS, FEEDS, FLOW_READOUTS, JUNCTIONS, ROUTES, SAMPLES, VALVES, VIEWBOX } from './schematic.js';
import { flowReading, pressureReading } from './ui.js';
import { mountDiagramPreview } from './diagram-preview.js';

// Orthogonal routing reads left-to-right. Only solid dots indicate a pipe junction.
const routePath = (points) => 'M' + points.map(([x, y]) => `${x} ${y}`).join('L');
const METERS = [
  ['05', 554, 112], ['06', 222, 300], ['07', 357, 128], ['08', 693, 67],
  ['09', 819, 131], ['10', 824, 266], ['11', 976, 428], ['12', 371, 384], ['13', 517, 316],
];
const symbol = (area, shape) => `<g class="pfd-symbol" data-symbol="${area}">${shape}</g>`;
const rect = (x,y,w,h,r = 4) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/>`;
const path = (d) => `<path d="${d}"/>`;
const vessel = (x,y,w,h) => rect(x,y,w,h,w / 2.3);
const pump = (x,y) => `<circle cx="${x}" cy="${y}" r="15"/><path d="M${x-15} ${y}h-6m36 0h6m-29-6 11 6-11 6z"/>`;
const ports = [
  ...FEEDS.map(([id, name]) => [ROUTES[id][0][0] - 42, ROUTES[id][0][1], name, FEED_TAGS[id]]),
  ...BOUNDARY_PORTS.map(([id, name, end]) => {
    const [x, y] = ROUTES[id].at(end === 'start' ? 0 : -1);
    return [x === 317 ? x - 49 : x, y, name];
  }),
];
const anchors = Object.entries(ANCHORS);

function levelVessel(area, markings) {
  const body = BODIES[area];
  const [x, y, w, h] = body;
  const bottom = y + h;
  return symbol(area, `${vessel(...body)}<g class="pfd-level" data-level="${area}" clip-path="url(#level-clip-${area})" aria-hidden="true">
    <rect x="${x}" y="${bottom}" width="${w}" height="0"/>
    <line class="pfd-level-surface" x1="${x}" x2="${x + w}" y1="${bottom}" y2="${bottom}" hidden/>
    <line class="pfd-level-goal" x1="${x}" x2="${x + w}" y1="${bottom}" y2="${bottom}" hidden/>
    </g>${path(markings)}`);
}

function equipment() {
  return [
    levelVessel('reactor', 'M294 231v100m-17-15h34m-31 16h28M334 285h-29q-13 0-13 9t13 8h14q9 0 9 8v9h10'),
    symbol('condenser', rect(...BODIES.condenser,15) + path('M423 146v38m98-38v38m-91-24h82m-82 9h82')),
    levelVessel('separator', 'M701 254h56m-55 3h54M697 234h27v13'),
    symbol('compressor', pump(604,76) + path('M562 76h21m42 0h20m-28-15 12-7v44l-12-7')),
    levelVessel('stripper', 'M859 285h62m0 10h-62m0 10h62m0 10h-62m3 39h56'),
    symbol('reboiler', vessel(...BODIES.reboiler) + path('M960 302h26m-26 6h26m-26 6h26m-26 6h26m-26 6h26m-26 6h26')),
    symbol('pump_sep', pump(771,327)), symbol('pump_prod', pump(945,414)),
    symbol('gc_feed', rect(...BODIES.gc_feed)), symbol('gc_purge', rect(...BODIES.gc_purge)),
    symbol('gc_product', rect(...BODIES.gc_product)),
  ].join('');
}

export function mountDiagram(host, selectArea, selectCircuit, openValve, selectFeed, drawPreview) {
  const lines = STREAMS.map((stream) => {
    const d = routePath(ROUTES[stream.id]);
    const { colorToken } = MEDIA[stream.media];
    const feedTag = FEED_TAGS[stream.id];
    const hit = stream.circuit || feedTag ? `<path class="pipe-hit" d="${d}" vector-effect="non-scaling-stroke"/>` : '';
    return `<g class="pfd-stream" data-stream="${stream.id}" data-circuit="${stream.circuit ?? ''}" ${feedTag ? `data-feed-tag="${feedTag}"` : ''} style="--pipe:var(${colorToken})">
      <title>${stream.id} · ${stream.from} → ${stream.to}</title>
      <path class="pipe-sleeve" d="${d}"/><path class="pipe-line" d="${d}" marker-end="url(#arrow-${stream.media})"/>
      <path class="pipe-ticks" d="${d}"/>${hit}</g>`;
  }).join('');
  const arrows = Object.entries({ ...MEDIA, tracked: { colorToken: '--tracked' }, muted: { colorToken: '--pipe-muted' } }).map(([id, { colorToken }]) =>
    `<marker id="arrow-${id}" viewBox="0 0 9 8" refX="8" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 9 4 0 8 2 4Z" fill="var(${colorToken})"/></marker>`).join('');
  const portsMarkup = ports.map(([x,y,name,tag]) =>
    `<g class="pfd-port" ${tag ? `data-feed-tag="${tag}" role="button" tabindex="0" aria-pressed="false" aria-label="${name} 원료 유량 추적"` : ''} transform="translate(${x} ${y})"><rect x="0" y="-10" width="${x === 1040 ? 78 : 47}" height="20" rx="3"/>
      <text x="${x === 1040 ? 39 : 23}" y="4">${name}</text></g>`).join('');
  const meterMarkup = METERS.map(([id,x,y]) => `<text class="pfd-id" x="${x}" y="${y}">#${id}</text>`).join('');
  const target = '<rect class="pfd-hit" aria-hidden="true" rx="3"/>';
  const valves = VALVES.map(([id,x,y,r = 0]) => `<g class="pfd-valve" data-valve="XMV(${id})" tabindex="0" role="button" aria-label="XMV(${id}) 밸브 조정" transform="translate(${x} ${y}) rotate(${r})">
    ${target}<path d="M-8-5 8 5V-5L-8 5ZM0 0v-8m-4 0h8"/></g>`).join('');
  const labels = anchors.map(([id, [x, y]]) => {
    const { code, name } = AREAS[id];
    return `<g class="pfd-unit" role="button" tabindex="0" data-area="${id}" aria-pressed="false" aria-label="${code} ${name}" transform="translate(${x} ${y})">
      <title>${name}</title>${target}<rect class="pfd-chip" x="-22" y="-12" width="44" height="17" rx="3"/><text y="0">${code}</text></g>`;
  }).join('');
  const levelClips = Object.keys(LEVEL_TAGS).map((id) => `<clipPath id="level-clip-${id}">${vessel(...BODIES[id])}</clipPath>`).join('');
  const readouts = anchors.filter(([id]) => id in CORE_PRESSURES).map(([id, [x, y]]) =>
    `<text class="pfd-pressure" data-pressure="${id}" x="${x + 32}" y="${y - 8}"><tspan>—</tspan><tspan class="pfd-pressure-unit" dx="5">kPa(g)</tspan><tspan class="pfd-pressure-state" dx="7"></tspan></text>`
  ).join('');
  const flowReadouts = Object.entries(FLOW_READOUTS).map(([id, [x, y]]) =>
    `<text class="pfd-flow-reading" data-flow-reading="${id}" x="${x}" y="${y}"></text>`
  ).join('');
  const samples = Object.values(SAMPLES).map(([, from, to]) => routePath([from, to])).join('');
  host.innerHTML = `<svg role="group" aria-label="Downs와 Vogel Fig. 1 공정 흐름도" class="pfd-svg" viewBox="0 0 ${VIEWBOX.join(' ')}">
    <defs>${arrows}${levelClips}</defs>${lines}
    <g class="pfd-samples">${path(samples)}</g>
    <g class="pfd-junctions">${JUNCTIONS.map(([x,y]) => `<circle cx="${x}" cy="${y}" r="3"/>`).join('')}</g>
    ${equipment()}${valves}${portsMarkup}${meterMarkup}${labels}${readouts}${flowReadouts}</svg>`;
  const svg = host.firstElementChild;
  const preview = mountDiagramPreview(host, svg, drawPreview);
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
    const feed = node.closest('[data-feed-tag]');
    if (valve) openValve(valve.dataset.valve);
    else if (unit) selectArea(unit.dataset.area ?? unit.dataset.symbol);
    else if (feed) selectFeed(feed.dataset.feedTag);
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
    if (!(event.key === 'Enter' || event.key === ' ') || !event.target.matches('.pfd-unit, .pfd-valve, .pfd-port[data-feed-tag]')) return;
    event.preventDefault();
    activate(event.target);
  });
  const levelByArea = [...svg.querySelectorAll('[data-level]')].map((node) => ({
    area: node.dataset.level, fill: node.querySelector('rect'),
    surface: node.querySelector('.pfd-level-surface'), goal: node.querySelector('.pfd-level-goal'),
  }));
  function levelLine(line, y, visible) {
    line.toggleAttribute('hidden', !visible);
    line.setAttribute('y1', y);
    line.setAttribute('y2', y);
  }
  const flowByStream = new Map([...svg.querySelectorAll('[data-flow-reading]')].map((node) => [node.dataset.flowReading, node]));
  const linesById = new Map([...svg.querySelectorAll('.pfd-stream')].map((line) => [line.dataset.stream, line]));
  const feedPorts = new Map([...svg.querySelectorAll('.pfd-port[data-feed-tag]')].map((port) => [port.dataset.feedTag, port]));
  return {
    focus(area, reset, tag) {
      for (const unit of svg.querySelectorAll('[data-area],[data-symbol]')) {
        const selected = (unit.dataset.area || unit.dataset.symbol) === area;
        unit.classList.toggle('selected', selected);
        if (unit.dataset.area) unit.setAttribute('aria-pressed', selected);
      }
      if (reset) host.scrollLeft = 0;
      else if (area !== 'plant' && ANCHORS[area] && !host.hidden && !feedPorts.has(tag)) {
        const valve = VALVES.find(([id]) => `XMV(${id})` === tag);
        const center = (valve?.[1] ?? ANCHORS[area][0]) / VIEWBOX[0] * svg.clientWidth;
        host.scrollLeft = Math.max(0, center - host.clientWidth / 2);
      }
      preview.show(area);
    },
    track(area, tag) {
      let trackingFeed = false;
      for (const unit of svg.querySelectorAll('[data-area],[data-symbol],[data-pressure]')) {
        const tracked = unit.dataset.pressure ? codeFor(CORE_PRESSURES[unit.dataset.pressure]) === tag : (unit.dataset.area || unit.dataset.symbol) === area;
        unit.classList.toggle('tracked', tracked);
      }
      for (const valve of svg.querySelectorAll('[data-valve]')) valve.classList.toggle('tracked', valve.dataset.valve === tag);
      for (const line of STREAMS) {
        const tracked = streamMatchesTag(line, tag);
        linesById.get(line.id).classList.toggle('tracked', tracked);
        flowByStream.get(line.id)?.classList.toggle('tracked', tracked);
        const port = feedPorts.get(FEED_TAGS[line.id]);
        if (port) {
          port.setAttribute('aria-pressed', tracked);
          trackingFeed ||= tracked;
        }
      }
      svg.classList.toggle('tracking-feed', trackingFeed);
    },
    circuit(id) {
      for (const line of linesById.values()) {
        line.classList.toggle('out-of-circuit', Boolean(id) && line.dataset.circuit !== id);
      }
    },
    flow(on) { svg.classList.toggle('show-flow', on); },
    pressure(on) {
      svg.classList.toggle('show-pressure', on);
      preview.position();
    },
    update(status) {
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
        if (label) label.textContent = flowReading(status, stream);
      }
      for (const node of svg.querySelectorAll('.pfd-unit,.pfd-symbol')) {
        const area = node.dataset.area ?? node.dataset.symbol;
        node.classList.toggle('in-alarm', status.alarms.some((alarm) => alarm.area === area));
      }
      for (const { area, fill, surface, goal } of levelByArea) {
        const [, y, , height] = BODIES[area];
        const bottom = y + height;
        const filled = height * levelFraction(levelReading(status, area));
        const target = status.setpoints[`${area}_level`];
        fill.setAttribute('height', filled);
        fill.setAttribute('y', bottom - filled);
        levelLine(surface, bottom - filled, filled > 0);
        levelLine(goal, bottom - height * levelFraction(target), Number.isFinite(target));
      }
      for (const node of svg.querySelectorAll('.pfd-pressure')) {
        const tag = codeFor(CORE_PRESSURES[node.dataset.pressure]);
        const { value, alarm, state } = pressureReading(status, tag);
        const [number, , indicator] = node.children;
        if (number.textContent !== value) number.textContent = value;
        if (indicator.textContent !== state) indicator.textContent = state;
        node.classList.toggle('in-alarm', Boolean(alarm));
      }
      preview.update(status);
    },
  };
}
