import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { AREAS, CORE_PRESSURES, MEDIA, PORTS, STREAMS, codeFor } from './topology.js';
import { buildInternals } from './internals.js';
import * as geo from './geometry.js';

const initialOffset = new T.Vector3(6.8, 18, 28);
const home = new T.Vector3(0, 2.76, -0.8);
const up = new T.Vector3(0, 1, 0);
const primary = {
  feeds: 2.2, reactor: 7.05, condenser: 4.9, separator: 6.2, compressor: 2.55,
  stripper: 8.95, reboiler: 3.48, purge: 2.02, product: 1.54,
  gc_feed: 2.05, gc_purge: 2.05, gc_product: 2.05, pump_sep: 1.28, pump_prod: 1.28,
};
const visibleLabels = new Set(['feeds', 'reactor', 'condenser', 'separator', 'compressor', 'stripper', 'product', 'purge']);

function pathFor(points) {
  const segments = new T.CurvePath();
  let start = geo.v3(points[0]);
  for (let i = 1; i < points.length - 1; i++) {
    const corner = geo.v3(points[i]), before = geo.v3(points[i - 1]), after = geo.v3(points[i + 1]);
    const radius = Math.min(0.21, corner.distanceTo(before) * 0.35, corner.distanceTo(after) * 0.35);
    const entry = corner.clone().add(before.sub(corner).setLength(radius));
    const exit = corner.clone().add(after.sub(corner).setLength(radius));
    if (start.distanceToSquared(entry) > 1e-6) segments.add(new T.LineCurve3(start, entry));
    segments.add(new T.QuadraticBezierCurve3(entry, corner, exit));
    start = exit;
  }
  segments.add(new T.LineCurve3(start, geo.v3(points.at(-1))));
  return segments;
}

function setupSite(scene) {
  const floor = new T.Group();
  scene.add(floor);
  const ground = geo.box(floor, [40, 0.27, 22], [0, -0.19, -0.63], geo.base, true);
  ground.castShadow = false;
  const slab = new T.MeshStandardMaterial({ color: 0x24333d, roughness: 0.99 });
  geo.box(floor, [38.9, 0.035, 20.9], [0, -0.045, -0.65], slab, true).castShadow = false;
  const grid = new T.GridHelper(40, 40, 0x526a75, 0x3b4e57);
  grid.position.set(0, 0.002, -0.65);
  grid.scale.z = 0.52;
  grid.material.transparent = true;
  grid.material.opacity = 0.27;
  scene.add(grid);
  const lines = new T.LineSegments(
    new T.EdgesGeometry(new T.BoxGeometry(40, 0.29, 22)),
    new T.LineBasicMaterial({ color: 0x466573 }),
  );
  lines.position.set(0, -0.18, -0.63);
  scene.add(lines);
  for (const x of [-18.8, 18.8]) for (const z of [-10.5, 9.35]) {
    geo.box(floor, [0.48, 0.12, 0.48], [x, 0.045, z], geo.edge);
  }
  const rack = new T.Group();
  scene.add(rack);
  for (const x of [-10.5, -6.8, -2.7, 3.4, 7.68]) {
    geo.support(rack, x, -7.58, 2.92);
    geo.rod(rack, [x, 2.83, -8.1], [x, 2.83, -6.82], 0.055, geo.edge);
  }
  for (const x of [-2.7, 3.9, 10.1]) {
    geo.support(rack, x, -7.51, 7.97);
    geo.rod(rack, [x, 7.98, -8.1], [x, 7.98, -6.86], 0.063, geo.edge);
  }
  for (const z of [-6.8, -0.2, 6.25]) geo.support(rack, -11.65, z, 0.38);
}

function buildEquipment(scene) {
  const groups = {}, sight = {};
  for (const [key, area] of Object.entries(AREAS)) {
    if (key === 'plant') continue;
    const group = new T.Group();
    group.position.copy(geo.v3(area.position));
    group.userData.area = key;
    groups[key] = group;
    scene.add(group);
  }
  geo.feeds(groups.feeds);
  sight.reactor = { tag: 8, ...geo.vessel(groups.reactor, { radius: 1.47, bottom: 0.9, top: 5.28, deck: 4.35, seams: [2.5, 3.65], agitator: true }) };
  sight.separator = { tag: 12, ...geo.vessel(groups.separator, { radius: 1.19, bottom: 0.84, top: 4.55, deck: 3.4, seams: [2.31] }) };
  sight.stripper = { tag: 15, ...geo.vessel(groups.stripper, { radius: 1.04, bottom: 0.83, top: 7.22, deck: 4.45, seams: [1.85, 3.36, 5.56, 6.4] }) };
  sight.reboiler = geo.vessel(groups.reboiler, { radius: 0.38, bottom: 0.51, top: 2.93, seams: [1.25, 2.24] });
  sight.condenser = geo.condenser(groups.condenser);
  geo.rotary(groups.compressor, 'compressor');
  geo.terminal(groups.product, 0.70);
  geo.terminal(groups.purge, 1.2);
  for (const area of ['gc_feed', 'gc_purge', 'gc_product']) geo.analyzer(groups[area]);

  for (const area of ['pump_sep', 'pump_prod']) {
    const model = new T.Group();
    groups[area].add(model);
    geo.rotary(model, 'pump');
    for (const z of [-0.76,0.80]) geo.flange(groups[area], [0,0.70,z], [0,0,1], 0.113);
  }
  for (const [key, tap] of [['gc_feed', [-10.5,0.8,1.4]], ['gc_purge', [12.1,1.2,-5.6]], ['gc_product', [14.5,0.70,5.19]]]) {
    const g = groups[key];
    const endpoint = geo.v3(tap).sub(g.position).toArray();
    g.add(new T.Mesh(new T.TubeGeometry(pathFor([[0.22,1.84,0],[0.22,2.04,0],[endpoint[0],2.04,endpoint[2]],endpoint]), 20, 0.028, 6),
      geo.gold));
  }
  return { groups, sight };
}

function buildStreams(scene, groups) {
  const markerGeometry = new T.ConeGeometry(0.103, 0.31, 9);
  return STREAMS.map((def) => {
    const path = pathFor(def.points), length = path.getLength();
    const color = new T.Color(MEDIA[def.media].color);
    const pipeColor = color.clone().lerp(new T.Color(0x334d58), 0.55);
    const material = new T.MeshStandardMaterial({
      color: pipeColor, emissive: color, emissiveIntensity: 0.08, roughness: 0.50, metalness: 0.32,
    });
    const radius = ['06','07','07B','08','08I','08R','08M','10','10S','11','11S'].includes(def.id) ? 0.114 : 0.079;
    const tube = new T.Mesh(new T.TubeGeometry(path, Math.ceil(Math.max(18, length * 10)), radius, 12), material);
    scene.add(tube);
    const geom = new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.98 });
    const count = Math.max(1, Math.ceil(length / (def.tag ? 2.15 : 4.0)));
    const markers = new T.InstancedMesh(markerGeometry, geom, count);
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
      const [number, meter] = def.valve;
      const fraction = Math.min(0.88, meter / length);
      const point = path.getPointAt(fraction), tangent = path.getTangentAt(fraction);
      const group = groups[def.area];
      valve = geo.controlValve(group, point.sub(group.position), tangent, number);
    }
    return { def, path, length, markers, material, pipeColor, color, valve, phase: 0, flanges };
  });
}

function createLabels(host, onSelect) {
  const labels = {};
  for (const [key, y] of Object.entries(primary)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'model-tag';
    button.dataset.area = key;
    button.title = AREAS[key].en;
    button.innerHTML = `<small>${AREAS[key].code}</small><b>${AREAS[key].name}</b><span></span>`;
    button.addEventListener('click', () => onSelect(key));
    host.append(button);
    const position = geo.v3(AREAS[key].position);
    position.y = y;
    if (key === 'compressor') position.x -= 1.0;
    if (key === 'separator') position.x += 0.54;
    labels[key] = { button, position, value: button.querySelector('span') };
  }
  return labels;
}

function makePortLabels(host) {
  return PORTS.map(([text, origin, medium, circuit]) => {
    const el = document.createElement('span');
    el.className = 'model-port';
    el.textContent = text;
    el.style.setProperty('--route-color', MEDIA[medium].color);
    el.dataset.circuit = circuit;
    host.append(el);
    const point = geo.v3(origin);
    point.y += circuit ? 0.44 : 0.02;
    return { el, point, circuit };
  });
}

export function mountScene(container, host, select) {
  const scene = new T.Scene();
  const renderer = new T.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(1.65, window.devicePixelRatio));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFShadowMap;
  renderer.shadowMap.autoUpdate = false;
  renderer.shadowMap.needsUpdate = true;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.13;
  const environment = new RoomEnvironment();
  const pmrem = new T.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(environment, 0.05).texture;
  scene.environmentIntensity = 0.64;
  environment.dispose();
  pmrem.dispose();
  renderer.domElement.setAttribute('aria-label', 'Tennessee Eastman 공정 3D 모델');
  container.append(renderer.domElement);
  const camera = new T.OrthographicCamera(-20, 20, 10, -10, 0.1, 130);
  camera.position.copy(home).add(initialOffset);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(home);
  controls.minZoom = 0.6;
  controls.maxZoom = 4.8;
  controls.maxPolarAngle = Math.PI / 2.04;
  controls.enableDamping = true;
  controls.dampingFactor = 0.13;
  controls.update();

  scene.add(new T.HemisphereLight(0xdef8ff, 0x6e899b, 2.27));
  const key = new T.DirectionalLight(0xe5f5ff, 2.55);
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
  const rim = new T.DirectionalLight(0x77cbe0, 1.05);
  rim.position.set(8, 6, -12);
  scene.add(rim);

  setupSite(scene);
  const { groups, sight } = buildEquipment(scene);
  const streams = buildStreams(scene, groups);
  const internal = buildInternals(groups, sight);
  const labels = createLabels(host, select);
  const ports = makePortLabels(host);
  const highlightMat = new T.MeshBasicMaterial({ color: 0x53eff1, transparent: true, opacity: 0.85, depthWrite: false });
  const highlight = new T.Mesh(new T.RingGeometry(1, 1.07, 56), highlightMat);
  highlight.rotation.x = -Math.PI / 2;
  highlight.visible = false;
  scene.add(highlight);

  const motionReduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const marker = new T.Object3D();
  const projected = new T.Vector3(), pointer = new T.Vector2(), ray = new T.Raycaster();
  const desired = new T.Vector3();
  const desiredCamera = new T.Vector3();
  const box = new T.Box3();
  let width = 1, height = 1, focus = 'plant', showFlow = true, showPressure = true, activeCircuit = '', showLabels = true, showInternals = false, displayed = true, frame = null, data = null;
  let down = null, last = 0, dragging = false;
  const alertAreas = new Set();
  const geometryBounds = {};
  scene.updateMatrixWorld(true);
  for (const [area, group] of Object.entries(groups)) {
    // Large auxiliary pipes/pumps belong to the unit but the orbit centers on its vessel.
    const spans = { reactor: 2.25, separator: 1.69, stripper: 1.56, feeds: 4.2, condenser: 2.33, compressor: 1.7 };
    geometryBounds[area] = spans[area] ?? 0.77;
    box.setFromObject(group);
    group.userData.top = primary[area] ?? Math.min(box.max.y, 6);
  }

  new ResizeObserver(() => {
    width = Math.max(1, container.clientWidth);
    height = Math.max(1, container.clientHeight);
    const aspect = width / height;
    const half = Math.max(8.9, 22.2 / aspect);
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
    const equipment = AREAS[area];
    desired.copy(area === 'plant' ? home : geo.v3(equipment.position));
    if (area !== 'plant') desired.y = Math.min(4, groups[area].userData.top * 0.47);
    const offset = resetOrientation ? initialOffset : camera.position.clone().sub(controls.target);
    desiredCamera.copy(desired).add(offset);
    const valve = streams.find((line) => line.valve?.userData.valve === tag && line.def.area === area)?.valve;
    const zoom = area === 'plant' ? 1 : Math.min(3.3, Math.max(2.04, 19.2 / (groups[area].userData.top + 1.9)));
    if (valve) {
      valve.getWorldPosition(desired);
      desired.y = Math.max(1.3, desired.y);
      desiredCamera.copy(desired).add(offset);
    }
    frame = { zoom };
    highlight.visible = area !== 'plant';
    if (highlight.visible) {
      highlight.position.copy(valve ? valve.getWorldPosition(new T.Vector3()) : geo.v3(equipment.position));
      highlight.position.y = 0.247;
      highlight.scale.setScalar(valve ? 0.44 : geometryBounds[area]);
    }
  }

  function projectLabels() {
    for (const [area, label] of Object.entries(labels)) {
      projected.copy(label.position).project(camera);
      const offscreen = Math.abs(projected.x) > 0.96 || Math.abs(projected.y) > 0.95 || projected.z > 1;
      label.button.hidden = offscreen || (!showLabels && focus !== area) ||
        (!visibleLabels.has(area) && focus !== area && !alertAreas.has(area));
      if (label.button.hidden) continue;
      const active = area === focus;
      const x = (projected.x + 1) * width / 2;
      const y = (1 - projected.y) * height / 2;
      label.button.style.left = `${active ? T.MathUtils.clamp(x, 58, width - 58) : x}px`;
      label.button.style.top = `${active ? Math.max(showPressure ? 64 : 51, y) : y}px`;
      label.button.classList.toggle('selected', focus === area);
      label.button.classList.toggle('in-alarm', alertAreas.has(area));
      label.button.classList.toggle('has-pressure', showPressure && Boolean(CORE_PRESSURES[area]));
      if (showPressure && CORE_PRESSURES[area] && data) {
        const value = data.values[codeFor(CORE_PRESSURES[area])];
        label.value.textContent = Number.isFinite(value) ? `${value.toFixed(1)} kPa(g)` : '';
      } else {
        label.value.textContent = '';
      }
    }
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

  renderer.domElement.addEventListener('pointerdown', (event) => {
    down = [event.clientX, event.clientY];
    dragging = true;
    frame = null;
  });
  renderer.domElement.addEventListener('pointerup', (event) => {
    dragging = false;
    if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
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
  renderer.domElement.addEventListener('pointercancel', () => { dragging = false; });
  renderer.domElement.addEventListener('wheel', () => { frame = null; }, { passive: true });

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
    if (frame && !dragging) {
      const ease = motionReduced ? 1 : 1 - Math.exp(-8 * dt);
      controls.target.lerp(desired, ease);
      camera.position.lerp(desiredCamera, ease);
      camera.zoom = T.MathUtils.lerp(camera.zoom, frame.zoom, ease);
      camera.updateProjectionMatrix();
      if (controls.target.distanceTo(desired) < 0.006 && Math.abs(camera.zoom - frame.zoom) < 0.006) frame = null;
    }
    controls.update();
    internal.animate(data, dt, showInternals && data?.running && !motionReduced);
    for (const line of streams) {
      const { def, path, length, markers, material, pipeColor, color, valve, flanges } = line;
      const tag = codeFor(def.tag);
      const measured = data?.native_values[tag] ?? data?.values[tag];
      const ratio = tag && Number.isFinite(measured) ? Math.max(0, measured / def.nominal) : 0.58;
      const selected = activeCircuit && activeCircuit === def.circuit;
      const dimmed = Boolean(activeCircuit && !selected);
      material.color.copy(selected ? color : pipeColor);
      material.color.multiplyScalar(dimmed ? 0.28 : 1);
      material.emissive.copy(color);
      material.emissiveIntensity = dimmed ? 0 : selected ? 0.44 : 0.08 + Math.min(ratio, 2) * 0.10;
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
    highlightMat.color.set(alertAreas.has(focus) ? 0xff8c58 : 0x53eff1);
    highlightMat.opacity = alertAreas.has(focus) && !motionReduced ? 0.68 + 0.26 * Math.sin(now / 240) : 0.86;
    projectLabels();
    renderer.render(scene, camera);
  }

  requestAnimationFrame(animate);
  return {
    go, updateVisuals,
    flow(enabled) { showFlow = enabled; },
    pressure(enabled) { showPressure = enabled; },
    circuit(id) { activeCircuit = id || ''; container.dataset.circuit = activeCircuit; },
    display(enabled) { displayed = enabled; },
    tags(visible) { showLabels = visible; },
    internals(visible) {
      showInternals = visible;
      internal.show(visible);
      container.dataset.internals = visible ? Object.keys(sight).join(',') : '';
      renderer.shadowMap.needsUpdate = true;
    },
  };
}
