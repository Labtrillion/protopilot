import { AREAS, CORE_PRESSURES, MEDIA, STREAMS, codeFor, streamMatchesTag } from './topology.js';
import { reading } from './ui.js';

// Orthogonal routing reads left-to-right. Only solid dots indicate a pipe junction.
const ROUTING = {
  '01': 'M78 170H190', '02': 'M78 214H190', '03': 'M78 258H190', 'M': 'M190 170V306',
  '04': 'M78 414H822V348H856',
  '05': 'M890 248V112H517V76',
  '06': 'M190 306H252', '07': 'M294 228V134H410V164',
  '07B': 'M536 164H558V234H695',
  '08': 'M729 188V76H675', '08I': 'M675 76H645', '08R': 'M562 76H517',
  '08M': 'M517 76H190V170', '08B': 'M550 76V39H675V76',
  '09': 'M729 139H1040',
  '10S': 'M729 282V327H750', '10': 'M792 327H828V273H856',
  '11S': 'M890 384V414H924', '11': 'M966 414H1040',
  'CWS': 'M317 389H382', 'CWR': 'M355 447H317',
  '12I': 'M382 389V286H338', '12': 'M338 319H355V447',
  '13I': 'M382 389H470V184', '13': 'M503 184V447H355',
  'ST': 'M1040 295H990', 'CD': 'M990 355H1040',
  'RB-I': 'M890 393H973V371', 'RB-R': 'M973 272V260H941V320H924',
};
const ANCHORS = {
  feeds: [110, 132], reactor: [293, 216], condenser: [478, 142], separator: [729, 177],
  compressor: [604, 100], stripper: [890, 234], reboiler: [990, 248],
  purge: [1068, 124], product: [1068, 398], pump_sep: [770, 359],
  pump_prod: [945, 446], gc_feed: [222, 357], gc_purge: [1001, 181], gc_product: [1019, 446],
};
const VALVES = [
  [3, 125, 170], [1, 125, 214], [2, 125, 258], [4, 125, 414], [5, 609, 39],
  [6, 967, 139], [7, 812, 327], [8, 1000, 414], [9, 1017, 295],
  [10, 355, 361, 90], [11, 503, 268, 90],
];
const METERS = [
  ['05', 554, 112], ['06', 222, 300], ['07', 357, 128], ['08', 693, 67],
  ['09', 819, 131], ['10', 824, 266], ['11', 983, 407], ['12', 371, 384], ['13', 517, 316],
];
const DOTS = [[190,170], [190,214], [190,258], [190,306], [550,76], [517,76], [675,76],
  [729,139], [382,389], [355,447], [890,393]];
const LEVELS = {
  reactor: { tag: 8, body: [252, 228, 86, 133], datum: 354, range: 65 },
  separator: { tag: 12, body: [695, 188, 68, 94], datum: 278, range: 42 },
  stripper: { tag: 15, body: [856, 248, 68, 136], datum: 379, range: 41 },
};
const FLOW_READOUTS = {
  '06': [205, 326], '08R': [510, 63], '10': [747, 310], '11': [971, 400],
};
const symbol = (area, shape) => `<g class="pfd-symbol" data-symbol="${area}">${shape}</g>`;
const rect = (x,y,w,h,r = 4) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}"/>`;
const path = (d) => `<path d="${d}"/>`;
const vessel = (x,y,w,h) => rect(x,y,w,h,w / 2.3);
const pump = (x,y) => `<circle cx="${x}" cy="${y}" r="15"/><path d="M${x-15} ${y}h-6m36 0h6m-29-6 11 6-11 6z"/>`;
const ports = [
  [36,170,'A'], [36,214,'D'], [36,258,'E'], [36,414,'A/C'],
  [268,389,'CWS'],[268,447,'CWR'],[1040,139,'PURGE'],[1040,295,'STM'],
  [1040,355,'COND'],[1040,414,'PRODUCT'],
];
const anchors = Object.entries(ANCHORS);

function equipment() {
  return [
    symbol('reactor', vessel(252,228,86,133) + path('M294 231v100m-17-15h34m-31 16h28M334 285h-29q-13 0-13 9t13 8h14q9 0 9 8v9h10')),
    symbol('condenser', rect(408,146,128,38,15) + path('M423 146v38m98-38v38m-91-24h82m-82 9h82')),
    symbol('separator', vessel(695,188,68,94) + path('M701 254h56m-55 3h54M697 234h27v13')),
    symbol('compressor', pump(604,76) + path('M562 76h21m42 0h20m-28-15 12-7v44l-12-7')),
    symbol('stripper', vessel(856,248,68,136) + path('M859 285h62m0 10h-62m0 10h62m0 10h-62m3 39h56')),
    symbol('reboiler', vessel(956,272,34,99) + path('M960 302h26m-26 6h26m-26 6h26m-26 6h26m-26 6h26m-26 6h26')),
    symbol('pump_sep', pump(771,327)), symbol('pump_prod', pump(945,414)),
    symbol('gc_feed', rect(203,331,38,19)), symbol('gc_purge', rect(981,154,40,19)),
    symbol('gc_product', rect(1000,428,38,17)),
  ].join('');
}

export function mountDiagram(host, selectArea, selectCircuit, openValve) {
  const lines = STREAMS.map((stream) => {
    const d = ROUTING[stream.id];
    const { colorToken } = MEDIA[stream.media];
    const hit = stream.circuit ? `<path class="pipe-hit" d="${d}" vector-effect="non-scaling-stroke"/>` : '';
    return `<g class="pfd-stream" data-stream="${stream.id}" data-circuit="${stream.circuit ?? ''}" style="--pipe:var(${colorToken})">
      <title>${stream.id} · ${stream.from} → ${stream.to}</title>
      <path class="pipe-sleeve" d="${d}"/><path class="pipe-line" d="${d}" marker-end="url(#arrow-${stream.media})"/>
      <path class="pipe-ticks" d="${d}"/>${hit}</g>`;
  }).join('');
  const arrows = Object.entries({ ...MEDIA, tracked: { colorToken: '--tracked' } }).map(([id, { colorToken }]) =>
    `<marker id="arrow-${id}" viewBox="0 0 9 8" refX="8" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 9 4 0 8 2 4Z" fill="var(${colorToken})"/></marker>`).join('');
  const portsMarkup = ports.map(([x,y,name]) =>
    `<g class="pfd-port" transform="translate(${x} ${y})"><rect x="0" y="-10" width="${x === 1040 ? 78 : 47}" height="20" rx="3"/>
      <text x="${x === 1040 ? 39 : 23}" y="4">${name}</text></g>`).join('');
  const meterMarkup = METERS.map(([id,x,y]) => `<text class="pfd-id" x="${x}" y="${y}">#${id}</text>`).join('');
  const target = '<rect class="pfd-hit" aria-hidden="true" rx="3"/>';
  const valves = VALVES.map(([id,x,y,r = 0]) => `<g class="pfd-valve" data-valve="XMV(${id})" tabindex="0" role="button" aria-label="XMV(${id}) 밸브 조정" transform="translate(${x} ${y}) rotate(${r})">
    ${target}<path d="M-8-5 8 5V-5L-8 5ZM0 0v-8m-4 0h8"/></g>`).join('');
  const labels = anchors.map(([id, [x, y]]) => {
    const { code, name } = AREAS[id];
    return `<g class="pfd-unit" role="button" tabindex="0" data-area="${id}" aria-label="${code} ${name}" transform="translate(${x} ${y})">
      <title>${name}</title>${target}<rect class="pfd-chip" x="-22" y="-12" width="44" height="17" rx="3"/><text y="0">${code}</text></g>`;
  }).join('');
  const levelClips = Object.entries(LEVELS).map(([id, { body }]) => `<clipPath id="level-clip-${id}">${vessel(...body)}</clipPath>`).join('');
  const levelLayers = Object.entries(LEVELS).map(([id, { body, datum }]) => {
    const [x, , w] = body;
    return `<g class="pfd-level" data-level="${id}" clip-path="url(#level-clip-${id})" aria-hidden="true">
      <rect x="${x}" y="${datum}" width="${w}" height="0"/><line x1="${x + 4}" x2="${x + w - 4}" y1="${datum}" y2="${datum}"/></g>`;
  }).join('');
  const readouts = anchors.filter(([id]) => id in CORE_PRESSURES).map(([id, [x, y]]) =>
    `<text class="pfd-pressure" data-pressure="${id}" x="${x + 32}" y="${y - 8}"></text>`
  ).join('');
  const flowReadouts = Object.entries(FLOW_READOUTS).map(([id, [x, y]]) =>
    `<text class="pfd-flow-reading" data-flow-reading="${id}" x="${x}" y="${y}"></text>`
  ).join('');
  host.innerHTML = `<svg role="group" aria-label="Downs와 Vogel Fig. 1 공정 흐름도" class="pfd-svg" viewBox="0 0 1140 476">
    <defs>${arrows}${levelClips}</defs>${lines}
    <g class="pfd-samples">${path('M220 306V331M1001 139V154M1019 414V428')}</g>
    <g class="pfd-junctions">${DOTS.map(([x,y]) => `<circle cx="${x}" cy="${y}" r="3"/>`).join('')}</g>
    ${equipment()}${levelLayers}${valves}${portsMarkup}${meterMarkup}${labels}${readouts}${flowReadouts}</svg>`;
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
      else if (area !== 'plant' && ANCHORS[area] && !host.hidden) {
        const valve = VALVES.find(([id]) => `XMV(${id})` === tag);
        const center = (valve?.[1] ?? ANCHORS[area][0]) / 1140 * svg.clientWidth;
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
    circuit(id) { svg.dataset.circuit = id || ''; },
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
