import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { AREAS, CORE_PRESSURES, MEDIA, STREAMS, codeFor, streamMatchesTag } from './topology.js';
import { buildInternals } from './internals.js';
import { EQUIPMENT_LAYOUT, FEED_OFFSETS, LABEL_POINTS, OVERVIEW, PORTS_3D, ROUTES_3D, SAMPLE_ROUTES_3D,
  VESSELS, pipePath, scenePoint, valvePosition } from './layout.js';
import { reading } from './ui.js';
import { readColor } from './palette.js';
import * as geo from './geometry.js';

const initialOffset = geo.v3(OVERVIEW.offset);
const initialOrbit = new T.Spherical().setFromVector3(initialOffset);
const orbitLimit = T.MathUtils.degToRad(25);
const home = geo.v3(OVERVIEW.target);
const up = new T.Vector3(0, 1, 0);
const visibleLabels = new Set(['feeds', 'reactor', 'condenser', 'separator', 'compressor', 'stripper', 'product', 'purge']);

function setupSite(scene) {
  const floor = new T.Group();
  scene.add(floor);
  const start = scenePoint([10, 30], 0);
  const end = scenePoint([1130, 472], 0);
  const width = end.x - start.x, depth = end.z - start.z;
  const center = (start.z + end.z) / 2;
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
  for (const x of [start.x + 0.7, end.x - 0.7]) for (const z of [start.z + 0.7, end.z - 0.7]) {
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
  geo.feeds(models.feeds, FEED_OFFSETS);
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
  return { groups, models, sight };
}

function buildStreams(scene, groups) {
  const markerGeometry = new T.ConeGeometry(0.103, 0.31, 9);
  const markerMaterial = new T.MeshBasicMaterial({ color: readColor('--flow-marker'), toneMapped: false });
  return STREAMS.map((def) => {
    const path = pipePath(ROUTES_3D[def.id]), length = path.getLength();
    const color = new T.Color(readColor(MEDIA[def.media].colorToken));
    // Diagram and 3D pipes use the same media colors under any scene lighting.
    const material = new T.MeshBasicMaterial({ color, toneMapped: false });
    const radius = ['06','07','07B','08','08I','08R','08M','10','10S','11','11S'].includes(def.id) ? 0.114 : 0.079;
    const tube = new T.Mesh(new T.TubeGeometry(path, Math.ceil(Math.max(18, length * 10)), radius, 12), material);
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
        flanges.push(geo.flange(scene, p.toArray(), d.toArray(), radius));
      }
    }
    let valve = null;
    if (def.valve) {
      const { point, tangent } = valvePosition(def);
      const group = groups[def.area];
      valve = geo.controlValve(group, point.sub(group.position), tangent, def.valve);
    }
    return { def, path, length, markers, material, color, valve, phase: 0, flanges };
  });
}

function createLabels(host, onSelect) {
  const labels = {};
  for (const [key, position] of Object.entries(LABEL_POINTS)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'model-tag';
    button.dataset.area = key;
    button.title = AREAS[key].en;
    button.innerHTML = `<small>${AREAS[key].code}</small><b>${AREAS[key].name}</b><span></span>`;
    button.addEventListener('click', () => onSelect(key));
    host.append(button);
    labels[key] = { button, position, value: button.querySelector('span'), width: 70, height: 36 };
  }
  return labels;
}

function makePortLabels(host) {
  return PORTS_3D.map(([text, point, medium, circuit]) => {
    const el = document.createElement('span');
    el.className = 'model-port';
    el.textContent = text;
    el.style.setProperty('--route-color', `var(${MEDIA[medium].colorToken})`);
    el.dataset.circuit = circuit;
    host.append(el);
    return { el, point, circuit };
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

export function mountScene(container, host, select, pick = () => {}) {
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
  controls.enablePan = false;
  controls.mouseButtons.MIDDLE = null;
  controls.mouseButtons.RIGHT = null;
  controls.minAzimuthAngle = initialOrbit.theta - orbitLimit;
  controls.maxAzimuthAngle = initialOrbit.theta + orbitLimit;
  controls.minPolarAngle = controls.maxPolarAngle = initialOrbit.phi;
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

  setupSite(scene);
  const { groups, models, sight } = buildEquipment(scene);
  const streams = buildStreams(scene, groups);
  const internal = buildInternals(models, sight);
  const labels = createLabels(host, select);
  const labelEntries = Object.entries(labels);
  const parts = makePartLabels(host, internal.parts, pick);
  const sized = new Map([...labelEntries.map(([, label]) => [label.button, label]), ...parts.map((part) => [part.button, part])]);
  const labelSizes = new ResizeObserver((entries) => {
    for (const { target, borderBoxSize } of entries) {
      const { inlineSize, blockSize } = borderBoxSize[0];
      if (!inlineSize || !blockSize) continue;
      Object.assign(sized.get(target), { width: inlineSize, height: blockSize });
    }
  });
  for (const button of sized.keys()) labelSizes.observe(button);
  const ports = makePortLabels(host);
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
  const box = new T.Box3();
  let width = 1, height = 1, focus = 'plant', showFlow = true, showPressure = true, activeCircuit = '', showLabels = true, showInternals = false, displayed = true, frame = null, data = null;
  let trackedArea = '', trackedTag = '';
  let down = null, last = 0, framing = false;
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

  new ResizeObserver(() => {
    width = Math.max(1, container.clientWidth);
    height = Math.max(1, container.clientHeight);
    const aspect = width / height;
    const half = Math.max(OVERVIEW.halfHeight, OVERVIEW.halfWidth / aspect);
    camera.left = -half * aspect;
    camera.right = half * aspect;
    camera.top = half;
    camera.bottom = -half;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
  }).observe(container);

  function go(area, resetOrientation = false, tag) {
    focus = area;
    container.dataset.focus = area;
    const group = groups[area];
    desired.copy(group ? group.position : home);
    if (area !== 'plant') desired.y = Math.min(4, groups[area].userData.top * 0.47);
    const valve = streams.find((line) => line.valve?.userData.valve === tag && line.def.area === area)?.valve;
    const zoom = area === 'plant' ? 1 : Math.min(3.3, Math.max(2.04, 19.2 / (groups[area].userData.top + 1.9)));
    if (valve) {
      valve.getWorldPosition(desired);
      desired.y = Math.max(1.3, desired.y);
    }
    frame = { zoom, offset: resetOrientation ? initialOffset : null };
    if (showInternals) applyInternals();
    highlight.visible = area !== 'plant';
    if (highlight.visible) {
      highlight.position.copy(valve ? valve.getWorldPosition(new T.Vector3()) : group.position);
      highlight.position.y = 0.247;
      highlight.scale.setScalar(valve ? 0.44 : geometryBounds[area]);
    }
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
      // Overlapping part tags hide; the selected unit keeps a stable button strip outside the canvas.
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

  function projectLabels() {
    const placed = [];
    const priority = (area) => (focus === area || trackedArea === area || alertAreas.has(area) ? 2 : CORE_PRESSURES[area] ? 1 : 0);
    const ordered = [...labelEntries].sort(([a], [b]) => priority(b) - priority(a));
    for (const [area, label] of ordered) {
      projected.copy(label.position).project(camera);
      const offscreen = Math.abs(projected.x) > 0.96 || Math.abs(projected.y) > 0.95 || projected.z > 1;
      const selected = area === focus || area === trackedArea;
      if (offscreen || (!showLabels && !selected) || (!visibleLabels.has(area) && !selected && !alertAreas.has(area))) {
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
      label.button.classList.toggle('selected', focus === area);
      label.button.classList.toggle('in-alarm', alertAreas.has(area));
      label.button.classList.toggle('has-pressure', showPressure && Boolean(CORE_PRESSURES[area]));
      if (showPressure && CORE_PRESSURES[area] && data) {
        const tag = codeFor(CORE_PRESSURES[area]);
        const value = data.values[tag];
        label.value.textContent = Number.isFinite(value) ? `${reading(value, 'kPa(g)')} kPa(g)${tag in data.offsets ? ' · 시험' : ''}` : '';
      } else {
        label.value.textContent = '';
      }
    }
    projectParts(placed);
    for (const { el, point, circuit } of ports) {
      projected.copy(point).project(camera);
      el.hidden = !showLabels || Math.abs(projected.x) > 0.95 || Math.abs(projected.y) > 0.94 ||
        (activeCircuit && activeCircuit !== circuit) || (circuit && !activeCircuit);
      if (!el.hidden) {
        el.style.left = `${(projected.x+1)*width/2}px`;
        el.style.top = `${(1-projected.y)*height/2}px`;
      }
    }
  }

  controls.addEventListener('change', () => {
    if (frame && !framing) {
      // Manual orbit and zoom can take over while the selected target stays centered.
      frame.offset = null;
      frame.zoom = null;
    }
  });

  renderer.domElement.addEventListener('pointerdown', (event) => {
    if (!event.isPrimary) {
      if (event.pointerType === 'touch') down = null;
      return;
    }
    if (event.button === 0) down = { id: event.pointerId, x: event.clientX, y: event.clientY };
  });
  renderer.domElement.addEventListener('pointermove', (event) => {
    if (down?.id === event.pointerId && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) down = null;
  });
  renderer.domElement.addEventListener('pointerup', (event) => {
    if (event.button !== 0 || down?.id !== event.pointerId) return;
    const withinClickDistance = Math.hypot(event.clientX - down.x, event.clientY - down.y) <= 5;
    down = null;
    if (!withinClickDistance) return;
    const bounds = renderer.domElement.getBoundingClientRect();
    pointer.set((event.clientX - bounds.left) / width * 2 - 1, 1 - (event.clientY - bounds.top) / height * 2);
    ray.setFromCamera(pointer, camera);
    const hit = ray.intersectObjects(Object.values(groups), true)[0];
    if (!hit) return;
    let obj = hit.object, valve;
    while (!obj.userData.area && obj.parent) {
      valve ||= obj.userData.valve;
      obj = obj.parent;
    }
    if (obj.userData.area) select(obj.userData.area, valve);
  });
  renderer.domElement.addEventListener('lostpointercapture', (event) => {
    if (down?.id === event.pointerId) down = null;
  });

  function updateVisuals(status) {
    data = status;
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

  function animate(now) {
    requestAnimationFrame(animate);
    if (now - last < 24 || !container.isConnected || !displayed || document.hidden) return;
    const dt = Math.min(0.075, (now - last) / 1000);
    last = now;
    if (frame) {
      cameraOffset.subVectors(camera.position, controls.target);
      const settled = controls.target.distanceTo(desired) < 0.006 &&
        (!frame.offset || cameraOffset.distanceTo(frame.offset) < 0.006) &&
        (frame.zoom === null || Math.abs(camera.zoom - frame.zoom) < 0.006);
      const ease = motionReduced || settled ? 1 : 1 - Math.exp(-8 * dt);
      controls.target.lerp(desired, ease);
      if (frame.offset) cameraOffset.lerp(frame.offset, ease);
      camera.position.copy(controls.target).add(cameraOffset);
      if (frame.zoom !== null) {
        camera.zoom = T.MathUtils.lerp(camera.zoom, frame.zoom, ease);
        camera.updateProjectionMatrix();
      }
      framing = true;
      controls.update();
      framing = false;
      if (ease === 1) frame = null;
    }
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
    projectLabels();
    renderer.render(scene, camera);
  }

  requestAnimationFrame(animate);
  return {
    go, updateVisuals,
    track(area, tag) {
      trackedArea = area;
      trackedTag = tag;
      for (const [key, label] of Object.entries(labels)) label.button.classList.toggle('tracked', key === area);
      for (const line of streams) line.tracked = streamMatchesTag(line.def, tag);
    },
    // Camera pose for returning to a previous investigation position.
    view() {
      return { target: controls.target.clone(), offset: camera.position.clone().sub(controls.target), zoom: camera.zoom };
    },
    restoreView(saved) {
      if (!saved) return;
      desired.copy(saved.target);
      frame = { zoom: saved.zoom, offset: saved.offset.clone() };
    },
    flow(enabled) { showFlow = enabled; },
    pressure(enabled) { showPressure = enabled; },
    circuit(id) { activeCircuit = id || ''; container.dataset.circuit = activeCircuit; },
    display(enabled) { displayed = enabled; },
    tags(visible) { showLabels = visible; },
    internals(visible) {
      showInternals = visible;
      applyInternals();
    },
  };
}
