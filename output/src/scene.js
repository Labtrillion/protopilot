import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { AREAS, CORE_PRESSURES, FEED_TAGS, MEDIA, STREAMS, codeFor, streamMatchesTag } from './topology.js';
import { buildInternals } from './internals.js';
import { EQUIPMENT_LAYOUT, FEED_OFFSETS, FLOW_POINTS, LABEL_POINTS, OVERVIEW, PORTS_3D, ROUTES_3D, SAMPLE_ROUTES_3D,
  VESSELS, pipePath, scenePoint, valvePosition } from './layout.js';
import { flowReading, reading } from './ui.js';
import { readColor } from './palette.js';
import { FEEDS } from './schematic.js';
import * as geo from './geometry.js';

const initialOffset = geo.v3(OVERVIEW.offset);
const initialPolarAngle = new T.Spherical().setFromVector3(initialOffset).phi;
const verticalOrbitLimit = T.MathUtils.degToRad(25);
const clickSlop = 5;
const processFrameInterval = 24;
const home = geo.v3(OVERVIEW.target);
const up = new T.Vector3(0, 1, 0);
const primaryLabels = new Set(['feeds', 'reactor', 'condenser', 'separator', 'stripper']);

function setupSite(scene, groups) {
  const footprint = new T.Box3();
  const bounds = new T.Box3();
  const depthPerHeight = initialOffset.z / initialOffset.y;
  let backInCamera = Infinity;
  for (const group of Object.values(groups)) {
    bounds.setFromObject(group);
    footprint.union(bounds);
    backInCamera = Math.min(backInCamera, bounds.min.z - depthPerHeight * bounds.max.y);
  }
  for (const route of Object.values(ROUTES_3D)) {
    for (const point of route) {
      footprint.expandByPoint(point);
      backInCamera = Math.min(backInCamera, point.z - depthPerHeight * point.y);
    }
  }
  const margin = 1.4;
  const halfWidth = Math.max(OVERVIEW.halfWidth, Math.abs(footprint.min.x), Math.abs(footprint.max.x)) + margin;
  const back = Math.min(backInCamera, footprint.min.z) - margin;
  const front = Math.max(scenePoint([0, 472], 0).z, footprint.max.z + margin);
  const width = 2 * halfWidth, depth = front - back;
  const center = (back + front) / 2;
  const floor = new T.Group();
  scene.add(floor);
  const ground = geo.box(floor, [width, 0.27, depth], [0, -0.19, center], geo.base, true);
  ground.castShadow = false;
  const slab = new T.MeshStandardMaterial({ color: readColor('--model-floor'), roughness: 0.99 });
  geo.box(floor, [width - 0.8, 0.035, depth - 0.8], [0, -0.045, center], slab, true).castShadow = false;
  const lines = new T.LineSegments(
    new T.EdgesGeometry(new T.BoxGeometry(width, 0.29, depth)),
    new T.LineBasicMaterial({ color: readColor('--model-line') }),
  );
  lines.position.set(0, -0.18, center);
  scene.add(lines);
  for (const x of [-halfWidth + 0.7, halfWidth - 0.7]) for (const z of [back + 0.7, front - 0.7]) {
    geo.box(floor, [0.48, 0.12, 0.48], [x, 0.045, z], geo.edge);
  }
  const rack = new T.Group();
  scene.add(rack);
  for (const [id, index, fractions] of [['07', 1, [0.52]], ['05', 1, [0.3, 0.7]], ['08M', 0, [0.32, 0.74]], ['04', 0, [0.29, 0.61, 0.83]]]) {
    const points = ROUTES_3D[id];
    for (const fraction of fractions) {
      const { x, y, z } = points[index].clone().lerp(points[index + 1], fraction);
      geo.support(rack, x, z, y - 0.13);
    }
  }
  const cameraUp = initialOffset.clone().normalize().cross(new T.Vector3(1, 0, 0));
  const halfHeight = Math.max(OVERVIEW.halfHeight, ...[back, front].map((z) =>
    Math.abs(new T.Vector3(0, -0.33, z).sub(home).dot(cameraUp))));
  return { halfWidth: halfWidth + 0.55, halfHeight: halfHeight + 0.32 };
}

function buildEquipment(scene) {
  const groups = {}, models = {}, sight = {};
  for (const [key, { position, scale }] of Object.entries(EQUIPMENT_LAYOUT)) {
    const group = new T.Group();
    group.position.copy(position);
    group.userData.area = key;
    groups[key] = group;
    const model = new T.Group();
    if (scale) model.scale.set(...scale);
    models[key] = model;
    group.add(model);
    scene.add(group);
  }
  const skids = geo.feeds(models.feeds, FEED_OFFSETS);
  const feedSkids = new Map(FEEDS.map(([id], index) => {
    const skid = skids[index], tag = FEED_TAGS[id];
    skid.userData.feedTag = tag;
    return [tag, skid];
  }));
  for (const [area, spec] of Object.entries(VESSELS)) {
    sight[area] = { tag: spec.levelTag, ...geo.vessel(models[area], spec) };
  }
  sight.condenser = geo.condenser(models.condenser);
  for (const area of ['product', 'purge']) geo.terminal(models[area], EQUIPMENT_LAYOUT[area].terminalHeight);
  for (const [area, left, right] of [
    ['compressor', ROUTES_3D['08R'][0], ROUTES_3D['08I'].at(-1)],
    ['pump_sep', ROUTES_3D['10S'].at(-1), ROUTES_3D['10'][0]],
    ['pump_prod', ROUTES_3D['11S'].at(-1), ROUTES_3D['11'][0]],
  ]) {
    const model = models[area];
    geo.rotary(model, area === 'compressor' ? 'compressor' : 'pump');
    for (const endpoint of [left, right]) {
      const port = endpoint.clone().sub(groups[area].position).toArray();
      const [x, y, z] = port;
      geo.rod(model, [x * 0.68, y, z], port, 0.135, geo.hardware);
      geo.flange(model, port, [1,0,0], 0.113);
    }
  }
  for (const [area, points] of Object.entries(SAMPLE_ROUTES_3D)) {
    geo.analyzer(models[area]);
    const group = groups[area];
    const geometry = new T.TubeGeometry(pipePath(points), 20, 0.028, 6);
    geometry.translate(-group.position.x, -group.position.y, -group.position.z);
    group.add(new T.Mesh(geometry, geo.hardware));
  }
  return { groups, models, sight, feedSkids };
}

function buildStreams(scene, groups) {
  const markerGeometry = new T.ConeGeometry(0.103, 0.31, 9);
  const markerMaterial = new T.MeshBasicMaterial({ color: readColor('--flow-marker'), toneMapped: false });
  return STREAMS.map((def) => {
    const feedTag = FEED_TAGS[def.id];
    const path = pipePath(ROUTES_3D[def.id]), length = path.getLength();
    const color = new T.Color(readColor(MEDIA[def.media].colorToken));
    // Diagram and 3D pipes use the same media colors under any scene lighting.
    const material = new T.MeshBasicMaterial({ color, toneMapped: false });
    const radius = ['06','07','07B','08','08I','08R','08M','10','10S','11','11S'].includes(def.id) ? 0.114 : 0.079;
    const tube = new T.Mesh(new T.TubeGeometry(path, Math.ceil(Math.max(18, length * 10)), radius, 12), material);
    tube.userData.circuit = def.circuit;
    tube.userData.feedTag = feedTag;
    scene.add(tube);
    const count = Math.max(1, Math.ceil(length / (def.tag ? 2.15 : 4.0)));
    const markers = new T.InstancedMesh(markerGeometry, markerMaterial, count);
    markers.frustumCulled = false;
    scene.add(markers);
    const flanges = [];
    for (const point of [0.18, Math.max(0.31, length - 0.18)]) {
      const fraction = Math.min(0.97, point / length);
      const p = path.getPointAt(fraction), d = path.getTangentAt(fraction);
      if (length > 1.3 && !['M','08I','08B','08R','08M'].includes(def.id)) {
        const flange = geo.flange(scene, p.toArray(), d.toArray(), radius);
        flange.userData.feedTag = feedTag;
        flanges.push(flange);
      }
    }
    let valve = null;
    if (def.valve) {
      const { point, tangent } = valvePosition(def);
      const group = groups[def.area];
      valve = geo.controlValve(group, point.sub(group.position), tangent, def.valve);
      valve.userData.area = def.area;
    }
    return { def, feedTag, path, length, tube, markers, material, color, valve, phase: 0, flanges };
  });
}

function createLabels(host, onSelect) {
  const labels = {};
  for (const [key, position] of Object.entries(LABEL_POINTS)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'model-tag';
    button.classList.toggle('compact', !primaryLabels.has(key));
    button.dataset.area = key;
    button.title = AREAS[key].en;
    button.setAttribute('aria-label', `${AREAS[key].code} ${AREAS[key].name}`);
    button.innerHTML = `<small>${AREAS[key].code}</small><b>${AREAS[key].name}</b><span></span>`;
    button.addEventListener('click', () => onSelect(key));
    host.append(button);
    labels[key] = { button, position, value: button.querySelector('span'), width: 70, height: 36 };
  }
  return labels;
}

function makePortLabels(host, selectFeed) {
  return PORTS_3D.map(([text, point, medium, circuit, feedTag]) => {
    const el = document.createElement(feedTag ? 'button' : 'span');
    el.className = 'model-port';
    el.textContent = text;
    el.style.setProperty('--route-color', `var(${MEDIA[medium].colorToken})`);
    el.dataset.circuit = circuit;
    if (feedTag) {
      el.type = 'button';
      el.dataset.feedTag = feedTag;
      el.setAttribute('aria-label', `${text} 원료 유량 추적`);
      el.setAttribute('aria-pressed', 'false');
      el.addEventListener('click', () => selectFeed(feedTag));
    }
    host.append(el);
    return { el, point, circuit, feedTag, width: Math.max(22, text.length * 6.5 + 10), height: 20 };
  });
}

function makeFlowLabels(host) {
  return STREAMS.filter(({ id }) => FLOW_POINTS[id]).map((def) => {
    const el = document.createElement('span');
    el.className = 'model-flow-reading';
    el.dataset.flowReading = def.id;
    el.hidden = true;
    host.append(el);
    return { el, def, point: FLOW_POINTS[def.id], width: 70, height: 13 };
  });
}

function makePartLabels(host, parts, onPick) {
  return parts.map((part) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'model-part';
    button.dataset.tag = part.tag;
    button.hidden = true;
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', `${AREAS[part.area].code} 내부 ${part.name} · ${part.tag} 추적`);
    button.innerHTML = `<b>${part.name}</b><small>${part.tag}</small>`;
    button.addEventListener('click', () => onPick(part.tag, part.area));
    host.append(button);
    return { ...part, button, width: 64, height: 30, point: new T.Vector3() };
  });
}

export function mountScene(container, host, select, pick, selectCircuit, selectFeed) {
  const scene = new T.Scene();
  scene.background = new T.Color(readColor('--stage'));
  const renderer = new T.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(1.65, window.devicePixelRatio));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFShadowMap;
  renderer.shadowMap.autoUpdate = false;
  renderer.shadowMap.needsUpdate = true;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  const environment = new RoomEnvironment();
  const pmrem = new T.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(environment, 0.05).texture;
  scene.environmentIntensity = 0.38;
  environment.dispose();
  pmrem.dispose();
  renderer.domElement.setAttribute('aria-label', 'Tennessee Eastman 공정 3D 모델');
  container.append(renderer.domElement);
  const camera = new T.OrthographicCamera(-20, 20, 10, -10, 0.1, 130);
  camera.position.copy(home).add(initialOffset);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(home);
  // Left-button drags shift the projection below, leaving the orbit target in place.
  controls.enablePan = false;
  controls.mouseButtons.LEFT = null;
  controls.mouseButtons.MIDDLE = null;
  controls.mouseButtons.RIGHT = T.MOUSE.ROTATE;
  controls.minPolarAngle = initialPolarAngle - verticalOrbitLimit;
  controls.maxPolarAngle = initialPolarAngle + verticalOrbitLimit;
  controls.cursorStyle = 'grab';
  controls.rotateSpeed = 0.35;
  controls.minZoom = 1;
  controls.maxZoom = 3.5;
  renderer.domElement.style.touchAction = 'pan-y';
  controls.update();

  scene.add(new T.HemisphereLight(0xffffff, 0x777777, 1.68));
  const key = new T.DirectionalLight(0xffffff, 1.65);
  key.position.set(-10, 24, 13);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left = -26;
  key.shadow.camera.right = 26;
  key.shadow.camera.top = 22;
  key.shadow.camera.bottom = -21;
  key.shadow.camera.far = 67;
  key.shadow.normalBias = 0.08;
  scene.add(key);
  const rim = new T.DirectionalLight(0xffffff, 0.65);
  rim.position.set(8, 6, -12);
  scene.add(rim);

  const { groups, models, sight, feedSkids } = buildEquipment(scene);
  const streams = buildStreams(scene, groups);
  const pickable = [...Object.values(groups)];
  for (const { def, feedTag, tube, flanges } of streams) {
    if (def.circuit || feedTag) pickable.push(tube);
    if (feedTag) pickable.push(...flanges);
  }
  const valveTargets = streams.filter(({ valve }) => valve).map(({ valve }) => ({ valve, x: 0, y: 0, onScreen: false }));
  const largerTargets = matchMedia('(max-width: 600px), (pointer: coarse)');
  const overviewFrame = setupSite(scene, groups);
  const internal = buildInternals(models, sight);
  const labels = createLabels(host, select);
  const labelEntries = Object.entries(labels);
  const parts = makePartLabels(host, internal.parts, pick);
  const ports = makePortLabels(host, selectFeed);
  const flowLabels = makeFlowLabels(host);
  const sized = new Map([
    ...labelEntries.map(([, label]) => [label.button, label]), ...parts.map((part) => [part.button, part]),
    ...ports.map((port) => [port.el, port]), ...flowLabels.map((label) => [label.el, label]),
  ]);
  const labelSizes = new ResizeObserver((entries) => {
    for (const { target, borderBoxSize } of entries) {
      const { inlineSize, blockSize } = borderBoxSize[0];
      if (!inlineSize || !blockSize) continue;
      Object.assign(sized.get(target), { width: inlineSize, height: blockSize });
    }
  });
  for (const button of sized.keys()) labelSizes.observe(button);
  const selectionColor = readColor('--focus');
  const warningColor = readColor('--warning');
  const trackedColor = new T.Color(readColor('--tracked'));
  const mutedPipeColor = new T.Color(readColor('--pipe-muted'));
  const highlightMat = new T.MeshBasicMaterial({
    color: selectionColor, toneMapped: false, depthWrite: false,
  });
  const highlight = new T.Mesh(new T.RingGeometry(1, 1.07, 56), highlightMat);
  highlight.rotation.x = -Math.PI / 2;
  highlight.visible = false;
  scene.add(highlight);

  const motionReduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const marker = new T.Object3D();
  const projected = new T.Vector3(), pointer = new T.Vector2(), ray = new T.Raycaster();
  const desired = new T.Vector3();
  const cameraOffset = new T.Vector3();
  const cameraOrbit = new T.Spherical(), destinationOrbit = new T.Spherical();
  const box = new T.Box3();
  let width = 1, height = 1, focus = 'plant', showFlow = true, showPressure = true, activeCircuit = '', showLabels = true, showInternals = false, displayed = true, frame = null, data = null;
  let trackedArea = '', trackedTag = '';
  let trackedFeed, focusedValve;
  let down = null, framing = false, valveRadius = 14;
  let panX = 0, cameraDirty = true, lastRender = 0, lastProcessFrame = -Infinity;
  const alertAreas = new Set();
  const geometryBounds = {};
  scene.updateMatrixWorld(true);
  for (const [area, group] of Object.entries(groups)) {
    // Large auxiliary pipes/pumps belong to the unit but the orbit centers on its vessel.
    const spans = { reactor: 2.25, separator: 1.69, stripper: 1.56, feeds: 4.2, condenser: 2.33, compressor: 1.7 };
    geometryBounds[area] = spans[area] ?? 0.77;
    box.setFromObject(models[area]);
    group.userData.top = box.max.y;
  }

  function updateProjection() {
    camera.setViewOffset(width, height, -panX * width, 0, width, height);
    cameraDirty = true;
  }

  new ResizeObserver(() => {
    width = Math.max(1, container.clientWidth);
    height = Math.max(1, container.clientHeight);
    const aspect = width / height;
    const half = Math.max(overviewFrame.halfHeight, overviewFrame.halfWidth / aspect);
    camera.left = -half * aspect;
    camera.right = half * aspect;
    camera.top = half;
    camera.bottom = -half;
    updateProjection();
    renderer.setSize(width, height, false);
  }).observe(container);

  function go(area, resetOrientation = false, tag) {
    focus = area;
    container.dataset.focus = area;
    const group = groups[area];
    focusedValve = streams.find((line) => line.valve?.userData.valve === tag && line.def.area === area)?.valve;
    if (showInternals) applyInternals();
    moveHighlight();
    desired.copy(group ? group.position : home);
    if (group) desired.y = Math.min(4, group.userData.top * 0.47);
    const zoom = group ? Math.min(3.3, Math.max(2.04, 19.2 / (group.userData.top + 1.9))) : 1;
    const target = focusedValve ?? (area === 'feeds' ? feedSkids.get(tag) : undefined);
    if (target) {
      target.getWorldPosition(desired);
      desired.y = Math.max(1.3, desired.y);
    }
    frame = { zoom, offset: resetOrientation ? initialOffset : null, panX: 0 };
  }

  function moveHighlight() {
    const group = groups[focus];
    highlight.visible = Boolean(group);
    if (!group) return;
    const feed = focus === 'feeds' ? trackedFeed : null;
    const valve = feed ? (feed.valve?.userData.valve === trackedTag ? feed.valve : null) : focusedValve;
    const skid = feedSkids.get(feed?.feedTag);
    const target = valve || skid || group;
    target.getWorldPosition(highlight.position);
    highlight.position.y = 0.247;
    highlight.scale.setScalar(valve ? 0.44 : skid ? 1.25 : geometryBounds[focus]);
  }

  // A selected unit with an interior shows only its own section; otherwise every interior is shown.
  function applyInternals() {
    const shown = internal.show(showInternals, focus in sight ? focus : '');
    container.dataset.internals = shown.join(',');
    renderer.shadowMap.needsUpdate = true;
  }

  function projectParts(placed) {
    for (const part of parts) {
      const { button } = part;
      const own = focus === part.area;
      if (!showInternals || !internal.visible(part.area) || (!showLabels && !own)) {
        button.hidden = true;
        continue;
      }
      part.anchor.getWorldPosition(part.point);
      projected.copy(part.point).project(camera);
      const x = (projected.x + 1) * width / 2, y = (1 - projected.y) * height / 2;
      const left = x - part.width / 2, right = x + part.width / 2, top = y - part.height / 2, bottom = y + part.height / 2;
      const outside = Math.abs(projected.x) > 0.97 || Math.abs(projected.y) > 0.95 || projected.z > 1;
      // Overlapping part tags hide; the selected unit keeps a stable button strip over the canvas.
      const blocked = placed.some((other) => left < other.right + 2 && right > other.left - 2 && top < other.bottom + 2 && bottom > other.top - 2);
      button.hidden = outside || blocked;
      if (button.hidden) continue;
      placed.push({ left, right, top, bottom });
      button.style.left = `${x}px`;
      button.style.top = `${y}px`;
      const tracked = part.tag === trackedTag;
      button.classList.toggle('tracked', tracked);
      button.setAttribute('aria-pressed', tracked);
    }
  }

  function projectAnnotation(item, placed, alignEnd = false) {
    projected.copy(item.point).project(camera);
    const x = (projected.x + 1) * width / 2;
    const y = (1 - projected.y) * height / 2;
    const left = x - (alignEnd ? item.width : item.width / 2);
    const right = left + item.width;
    const top = y - item.height * (alignEnd ? 0.88 : 0.65);
    const bottom = top + item.height;
    item.el.hidden = left < 2 || right > width - 2 || top < 2 || bottom > height - 2 || projected.z > 1 ||
      placed.some((other) => left < other.right + 1 && right > other.left - 1 && top < other.bottom + 1 && bottom > other.top - 1);
    if (item.el.hidden) return;
    placed.push({ left, right, top, bottom });
    item.el.style.left = `${left}px`;
    item.el.style.top = `${top}px`;
  }

  function projectLabels() {
    const placed = [];
    // Reserve the valve silhouettes for labels. The larger hit radius helps with aim on the canvas.
    const pixelsPerUnit = height * camera.zoom / (camera.top - camera.bottom);
    const clearance = Math.max(largerTargets.matches ? 12 : 5, 0.4 * pixelsPerUnit);
    valveRadius = Math.max(largerTargets.matches ? 18 : 14, clearance);
    for (const target of valveTargets) {
      target.valve.localToWorld(projected.set(0, 0.16, 0)).project(camera);
      const x = (projected.x + 1) * width / 2, y = (1 - projected.y) * height / 2;
      const left = x - clearance, right = x + clearance, top = y - clearance, bottom = y + clearance;
      const onScreen = Math.abs(projected.z) <= 1 && right > 0 && left < width && bottom > 0 && top < height;
      Object.assign(target, { x, y, onScreen });
      if (onScreen) placed.push({ left, right, top, bottom });
    }
    const priority = (area) => (focus === area || trackedArea === area || alertAreas.has(area) ? 3 :
      CORE_PRESSURES[area] ? 2 : primaryLabels.has(area) ? 1 : 0);
    const ordered = [...labelEntries].sort(([a], [b]) => priority(b) - priority(a));
    for (const [area, label] of ordered) {
      projected.copy(label.position).project(camera);
      const offscreen = Math.abs(projected.x) > 0.96 || Math.abs(projected.y) > 0.95 || projected.z > 1;
      const selected = area === focus || area === trackedArea;
      if (offscreen || (!showLabels && !selected)) {
        label.button.hidden = true;
        continue;
      }
      const half = label.width / 2;
      const x = T.MathUtils.clamp((projected.x + 1) * width / 2, half + 3, width - half - 3);
      const anchor = (1 - projected.y) * height / 2;
      const left = x - half, right = x + half;
      let y = T.MathUtils.clamp(anchor, label.height + 3, height - 5);
      // Reserve space for tracked units and pressure readings before placing nearby tags.
      for (let overlap; (overlap = placed.find((other) => left < other.right + 3 && right > other.left - 3 &&
        y > other.top - 3 && y - label.height < other.bottom + 3));) y = overlap.top - 3;
      label.button.hidden = y < label.height + 3;
      if (label.button.hidden) continue;
      placed.push({ left, right, top: y - label.height, bottom: y });
      label.button.style.left = `${x}px`;
      label.button.style.top = `${y}px`;
      label.button.style.setProperty('--tag-leader', `${Math.max(0, anchor - y)}px`);
      label.button.classList.toggle('selected', focus === area && !(area === 'feeds' && trackedFeed));
      label.button.classList.toggle('in-alarm', alertAreas.has(area));
      label.button.classList.toggle('has-pressure', showPressure && Boolean(CORE_PRESSURES[area]));
      let pressure = '';
      if (showPressure && CORE_PRESSURES[area] && data) {
        const tag = codeFor(CORE_PRESSURES[area]);
        const value = data.values[tag];
        if (Number.isFinite(value)) pressure = `${reading(value, 'kPa(g)')} kPa(g)${tag in data.offsets ? ' · 시험' : ''}`;
      }
      if (label.value.textContent !== pressure) label.value.textContent = pressure;
    }
    projectParts(placed);
    for (const port of ports) {
      if (showLabels && (!activeCircuit || activeCircuit === port.circuit)) projectAnnotation(port, placed);
      else port.el.hidden = true;
    }
    for (const label of flowLabels) {
      if (showFlow && data && (!activeCircuit || activeCircuit === label.def.circuit)) projectAnnotation(label, placed, true);
      else label.el.hidden = true;
    }
  }

  function takeCameraControl() {
    cameraDirty = true;
    if (frame && !framing) {
      // Complete the selected target's move without overriding manual camera input.
      frame.offset = null;
      frame.zoom = null;
      frame.panX = null;
    }
  }

  controls.addEventListener('change', takeCameraControl);

  function hitAt(x, y) {
    pointer.set(x / width * 2 - 1, 1 - y / height * 2);
    ray.setFromCamera(pointer, camera);
    let object = ray.intersectObjects(pickable, true)[0]?.object;
    while (object && !object.userData.area && !object.userData.circuit && !object.userData.feedTag) object = object.parent;
    return object?.userData;
  }

  function nearbyValve(x, y) {
    let closest, distance = valveRadius;
    for (const target of valveTargets) {
      if (!target.onScreen) continue;
      const gap = Math.hypot(x - target.x, y - target.y);
      if (gap > distance) continue;
      // Only extend the hit area of a valve whose center is visible from this angle.
      if (hitAt(target.x, target.y)?.valve !== target.valve.userData.valve) continue;
      closest = target.valve.userData;
      distance = gap;
    }
    return closest;
  }

  renderer.domElement.addEventListener('pointerdown', (event) => {
    if (!event.isPrimary) {
      if (event.pointerType === 'touch') down = null;
      return;
    }
    if (event.button === 0) down = { id: event.pointerId, x: event.clientX, y: event.clientY, dragged: false };
  });
  renderer.domElement.addEventListener('pointermove', (event) => {
    if (down?.id !== event.pointerId || !(event.buttons & 1)) return;
    const dx = event.clientX - down.x;
    if (!down.dragged && Math.hypot(dx, event.clientY - down.y) <= clickSlop) return;
    down.dragged = true;
    down.x = event.clientX;
    if (event.pointerType === 'touch' || dx === 0) return;
    takeCameraControl();
    panX += dx / width;
    updateProjection();
  });
  renderer.domElement.addEventListener('pointerup', (event) => {
    if (event.button !== 0 || down?.id !== event.pointerId) return;
    const isClick = !down.dragged && Math.hypot(event.clientX - down.x, event.clientY - down.y) <= clickSlop;
    down = null;
    if (!isClick) return;
    const bounds = renderer.domElement.getBoundingClientRect();
    const x = event.clientX - bounds.left, y = event.clientY - bounds.top;
    camera.updateMatrixWorld();
    const exact = hitAt(x, y);
    const hit = exact?.valve ? exact : nearbyValve(x, y) ?? exact;
    if (hit?.circuit) selectCircuit(hit.circuit);
    else if (hit?.feedTag) selectFeed(hit.feedTag);
    else if (hit?.area) select(hit.area, hit.valve);
  });
  renderer.domElement.addEventListener('lostpointercapture', (event) => {
    if (down?.id === event.pointerId) down = null;
  });

  function updateVisuals(status) {
    data = status;
    for (const { el, def } of flowLabels) el.textContent = flowReading(status, def);
    alertAreas.clear();
    status.alarms.forEach((a) => alertAreas.add(a.area));
    for (const { tag, fill, bottom, height: max } of Object.values(sight)) {
      if (!tag) continue;
      const channel = codeFor(tag);
      const level = Math.max(0.005, Math.min(1, (status.native_values[channel] ?? status.values[channel] ?? 0) / 100));
      fill.scale.y = max * level;
      fill.position.y = bottom + max * level / 2;
    }
  }

  function animateCamera(dt) {
    cameraOffset.subVectors(camera.position, controls.target);
    const settled = controls.target.distanceTo(desired) < 0.006 &&
      (!frame.offset || cameraOffset.distanceTo(frame.offset) < 0.006) &&
      (frame.zoom === null || Math.abs(camera.zoom - frame.zoom) < 0.006) &&
      (frame.panX === null || Math.abs(panX - frame.panX) * width < 0.5);
    const ease = motionReduced || settled ? 1 : 1 - Math.exp(-8 * dt);
    controls.target.lerp(desired, ease);
    if (frame.offset) {
      // Keep the return path within the pitch limits when the camera is behind the model.
      cameraOrbit.setFromVector3(cameraOffset);
      destinationOrbit.setFromVector3(frame.offset);
      const yawDelta = T.MathUtils.euclideanModulo(destinationOrbit.theta - cameraOrbit.theta + Math.PI, 2 * Math.PI) - Math.PI;
      cameraOrbit.theta += yawDelta * ease;
      cameraOrbit.phi = T.MathUtils.lerp(cameraOrbit.phi, destinationOrbit.phi, ease);
      cameraOrbit.radius = T.MathUtils.lerp(cameraOrbit.radius, destinationOrbit.radius, ease);
      cameraOffset.setFromSpherical(cameraOrbit);
    }
    camera.position.copy(controls.target).add(cameraOffset);
    if (frame.zoom !== null) camera.zoom = T.MathUtils.lerp(camera.zoom, frame.zoom, ease);
    if (frame.panX !== null) panX = T.MathUtils.lerp(panX, frame.panX, ease);
    if (frame.zoom !== null || frame.panX !== null) updateProjection();
    framing = true;
    controls.update();
    framing = false;
    if (ease === 1) frame = null;
  }

  function animateProcess(dt) {
    internal.animate(data, dt, showInternals && data?.running && !motionReduced);
    for (const line of streams) {
      const { def, path, length, markers, material, color, valve, flanges } = line;
      const tag = codeFor(def.tag);
      const measured = data?.native_values[tag] ?? data?.values[tag];
      const ratio = tag && Number.isFinite(measured) ? Math.max(0, measured / def.nominal) : 0.58;
      const dimmed = Boolean(activeCircuit) && activeCircuit !== def.circuit;
      material.color.copy(dimmed ? mutedPipeColor : line.tracked ? trackedColor : color);
      for (const flange of flanges) flange.visible = !dimmed;
      if (valve) {
        const pos = data?.actuators?.[valve.userData.valve];
        if (Number.isFinite(pos)) {
          const stem = valve.userData.stem;
          stem.position.y = T.MathUtils.lerp(stem.position.y, 0.18 + 0.00225 * pos, 1 - Math.exp(-10 * dt));
        }
      }
      markers.visible = showFlow && !dimmed && ratio > 0.012 && Boolean(data);
      if (!markers.visible) continue;
      if (data.running && !motionReduced) line.phase = (line.phase + dt * Math.min(5.3, 0.3 + 2.15 * ratio) / length) % 1;
      for (let i = 0; i < markers.count; i++) {
        const t = (i / markers.count + line.phase + 0.03) % 1;
        marker.position.copy(path.getPointAt(t));
        marker.position.y += 0.08;
        marker.quaternion.setFromUnitVectors(up, path.getTangentAt(t).normalize());
        marker.updateMatrix();
        markers.setMatrixAt(i, marker.matrix);
      }
      markers.instanceMatrix.needsUpdate = true;
    }
    highlightMat.color.set(alertAreas.has(focus) ? warningColor : trackedArea === focus ? trackedColor : selectionColor);
  }

  function animate(now) {
    requestAnimationFrame(animate);
    if (!displayed || !container.isConnected || document.hidden) return;
    const processDue = now - lastProcessFrame >= processFrameInterval;
    // Redraw changed camera poses on the next display frame while process motion keeps its own cadence.
    if (!cameraDirty && !frame && !processDue) return;
    if (frame) animateCamera(Math.min(0.075, (now - lastRender) / 1000));
    if (processDue) {
      animateProcess(Math.min(0.075, (now - lastProcessFrame) / 1000));
      lastProcessFrame = now;
    }
    lastRender = now;
    cameraDirty = false;
    camera.updateMatrixWorld();
    projectLabels();
    renderer.render(scene, camera);
  }

  requestAnimationFrame(animate);
  return {
    go, updateVisuals,
    track(area, tag) {
      trackedArea = area;
      trackedTag = tag;
      trackedFeed = streams.find((line) => line.feedTag && streamMatchesTag(line.def, tag));
      moveHighlight();
      for (const [key, label] of labelEntries) label.button.classList.toggle('tracked', key === area && !trackedFeed);
      for (const line of streams) line.tracked = streamMatchesTag(line.def, tag);
      for (const { el, feedTag } of ports) {
        if (feedTag) el.setAttribute('aria-pressed', feedTag === trackedFeed?.feedTag);
      }
      for (const { el, def } of flowLabels) el.classList.toggle('tracked', streamMatchesTag(def, tag));
    },
    // Camera pose for returning to a previous investigation position.
    view() {
      return { target: controls.target.clone(), offset: camera.position.clone().sub(controls.target), zoom: camera.zoom, panX };
    },
    restoreView(saved) {
      if (!saved) return;
      desired.copy(saved.target);
      frame = { zoom: saved.zoom, offset: saved.offset.clone(), panX: saved.panX ?? 0 };
    },
    flow(enabled) { showFlow = enabled; },
    pressure(enabled) { showPressure = enabled; },
    circuit(id) { activeCircuit = id || ''; container.dataset.circuit = activeCircuit; },
    display(enabled) {
      displayed = enabled;
      if (enabled) cameraDirty = true;
    },
    tags(visible) { showLabels = visible; },
    internals(visible) {
      showInternals = visible;
      applyInternals();
    },
  };
}
