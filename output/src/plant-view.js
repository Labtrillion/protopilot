import { AREAS, CIRCUITS, CORE_PRESSURES, INTERIOR_PARTS, INTERIOR_TAGS, STREAMS, codeFor, streamMatchesTag } from './topology.js';
import { mountDiagram } from './diagram.js';
import { mountScene } from './scene.js';
import { byId, escape, legend, renderPressures } from './ui.js';

export function mountPlantView({ selectArea, selectValve, onCircuit, trackTag }) {
  const canvas = byId('canvas'), labels = byId('model-labels'), diagramRoot = byId('diagram');
  const threeButton = byId('view-3d'), twoButton = byId('view-2d');
  const flows = byId('view-flow'), pressure = byId('view-pressure'), internals = byId('toggle-internals'), tags = byId('toggle-tags');
  const internalsLabel = byId('internals-label'), partStrip = byId('interior-parts');
  const circuitButtons = [...document.querySelectorAll('.circuit-controls button[data-circuit]')];
  let scene, current, area = 'plant', circuit = '', visible = '3d', tracked = '', focusedTag = '';
  const flags = { flows: true, pressure: true, internals: false, tags: true };
  const diagram = mountDiagram(diagramRoot, selectArea, chooseCircuit, selectValve);
  try {
    scene = mountScene(canvas, labels, selectArea, trackTag);
  } catch (error) {
    console.warn('WebGL scene unavailable', error);
    threeButton.disabled = true;
    threeButton.title = '이 브라우저에서 WebGL 2를 사용할 수 없습니다.';
    switchView('2d');
  }

  // Stable keyboard path to the selected unit's interior tags, independent of projected label overlap.
  function renderInterior() {
    const list = INTERIOR_PARTS[area];
    internalsLabel.textContent = list ? `${AREAS[area].code} 내부` : '내부 전체';
    internals.title = list ? `${AREAS[area].code} 단면만 표시` : '모든 설비 내부 표시';
    const show = Boolean(list && flags.internals && visible === '3d' && scene);
    partStrip.hidden = !show;
    if (!show) return;
    const key = `${area}/${tracked}`;
    if (partStrip.dataset.key === key) return;
    partStrip.dataset.key = key;
    partStrip.setAttribute('aria-label', `${AREAS[area].code} 내부 부품 태그`);
    const focusedPart = partStrip.contains(document.activeElement) ? document.activeElement.dataset.tag : '';
    partStrip.innerHTML = `<span class="toolbar-label">${AREAS[area].code} 내부</span>${list.map(([tag, name]) =>
      `<button type="button" data-tag="${tag}" aria-pressed="${tag === tracked}">${escape(name)}<code>${tag}</code></button>`).join('')}`;
    if (focusedPart) partStrip.querySelector(`[data-tag="${focusedPart}"]`)?.focus({ preventScroll: true });
  }
  partStrip.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-tag]');
    if (button) trackTag(button.dataset.tag, area);
  });

  function switchView(next) {
    visible = next;
    const three = next === '3d';
    canvas.hidden = labels.hidden = !three;
    diagramRoot.hidden = three;
    threeButton.setAttribute('aria-pressed', three);
    twoButton.setAttribute('aria-pressed', !three);
    tags.disabled = internals.disabled = !three || !scene;
    byId('stage').dataset.view = next;
    byId('stage-caption-text').textContent = three ? '공정 모델' : '공정도';
    byId('canvas-error').hidden = !three || Boolean(scene);
    scene?.display(three);
    diagram.focus(area, area === 'plant', focusedTag);
    legend(circuit, three && flags.internals);
    renderInterior();
  }

  function chooseCircuit(next, refocus = true) {
    if (next && !(next in CIRCUITS)) return;
    circuit = next;
    for (const button of circuitButtons) button.setAttribute('aria-pressed', button.dataset.circuit === next);
    scene?.circuit(next);
    diagram.circuit(next);
    legend(next, visible === '3d' && flags.internals);
    if (refocus && next) selectArea('plant', '', true);
    onCircuit(next);
  }

  // Layer flags are set through one path that does not depend on the button being enabled,
  // so 3D-only layers (tags, interior) restore correctly even while the 2D view is shown.
  const layers = {};
  function setFlag(key, value) {
    if (flags[key] === value) return;
    flags[key] = value;
    layers[key].button.setAttribute('aria-pressed', value);
    layers[key].onChange(value);
  }
  function toggle(button, key, onChange) {
    layers[key] = { button, onChange };
    button.addEventListener('click', () => setFlag(key, !flags[key]));
  }
  threeButton.addEventListener('click', () => switchView('3d'));
  twoButton.addEventListener('click', () => switchView('2d'));
  for (const button of circuitButtons) button.addEventListener('click', () => chooseCircuit(button.dataset.circuit));
  toggle(flows, 'flows', (enabled) => { scene?.flow(enabled); diagram.flow(enabled); });
  toggle(pressure, 'pressure', applyPressure);
  toggle(tags, 'tags', (enabled) => scene?.tags(enabled));
  // Toggling the interior keeps the selected unit, camera, tracked tag and telemetry view.
  toggle(internals, 'internals', (enabled) => {
    scene?.internals(enabled);
    legend(circuit, visible === '3d' && enabled);
    renderInterior();
  });

  function applyPressure(enabled) {
    scene?.pressure(enabled);
    diagram.pressure(enabled);
    renderPressures(current, enabled, tracked);
  }
  byId('pressure-overview').addEventListener('click', (event) => {
    const button = event.target.closest('[data-pressure-tag]');
    if (button) selectArea(button.dataset.area, button.dataset.pressureTag);
  });
  diagram.flow(true);
  applyPressure(true);
  legend('');
  renderInterior();
  return {
    focus(next, reset, tag) {
      area = next;
      focusedTag = tag;
      scene?.go(next, reset, tag);
      diagram.focus(next, reset, tag);
      renderInterior();
    },
    circuit: chooseCircuit,
    track(signalArea, tag) {
      tracked = tag;
      scene?.track(signalArea, tag);
      diagram.track(signalArea, tag);
      renderPressures(current, flags.pressure, tracked);
      renderInterior();
    },
    reveal(tag) {
      if (Object.values(CORE_PRESSURES).map(codeFor).includes(tag)) setFlag('pressure', true);
      if (visible === '3d' && scene && INTERIOR_TAGS.has(tag)) setFlag('internals', true);
      const stream = STREAMS.find((line) => streamMatchesTag(line, tag));
      if (stream && !tag.startsWith('XMV')) setFlag('flows', true);
      if (stream && circuit && stream.circuit !== circuit) chooseCircuit('', false);
    },
    showPressure() {
      setFlag('pressure', true);
    },
    snapshot() {
      return {
        view: visible, flags: { ...flags }, circuit, camera: scene?.view(),
        scroll: visible === '2d' ? [diagramRoot.scrollLeft, diagramRoot.scrollTop] : null,
      };
    },
    // Called after focus() so the saved camera pose and 2D scroll win over the default unit framing.
    restore(saved) {
      if (!saved) return;
      // Restore every layer flag first (3D-only ones included), then switch the view.
      for (const key of Object.keys(layers)) setFlag(key, saved.flags[key]);
      if (saved.view !== visible && !(saved.view === '3d' && !scene)) switchView(saved.view);
      if (saved.circuit !== circuit) chooseCircuit(saved.circuit, false);
      scene?.restoreView(saved.camera);
      if (saved.scroll && visible === '2d') [diagramRoot.scrollLeft, diagramRoot.scrollTop] = saved.scroll;
    },
    update(status) {
      current = status;
      scene?.updateVisuals(status);
      diagram.update(status);
      renderPressures(status, flags.pressure, tracked);
    },
  };
}
