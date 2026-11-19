import { AREAS, CIRCUITS, CORE_PRESSURES, INTERIOR_PARTS, INTERIOR_TAGS, STREAMS, codeFor, streamMatchesTag } from './topology.js';
import { mountDiagram } from './diagram.js';
import { mountScene } from './scene.js';
import { byId, escape, legend } from './ui.js';

export function mountPlantView({ selectArea, selectFeed, selectValve, onCircuit, trackTag }) {
  const canvas = byId('canvas'), labels = byId('model-labels'), diagramRoot = byId('diagram');
  const threeButton = byId('view-3d'), twoButton = byId('view-2d');
  const canvasError = byId('canvas-error');
  const flows = byId('view-flow'), pressure = byId('view-pressure'), internals = byId('toggle-internals'), tags = byId('toggle-tags');
  const tagListButton = byId('toggle-tag-list'), tagList = byId('model-tag-list');
  const internalsLabel = byId('internals-label'), interiorTools = byId('interior-tools'), partStrip = byId('interior-parts');
  const circuitButtons = [...document.querySelectorAll('.circuit-controls button[data-circuit]')];
  let scene, area = 'plant', circuit = '', visible = '3d', tracked = '', focusedTag = '';
  let tagListOpen = false, sceneAvailable = true, resume3D = false;
  const flags = { flows: true, pressure: true, internals: false, tags: true };
  const diagram = mountDiagram(diagramRoot, selectArea, chooseCircuit, selectValve, selectFeed,
    (unit, viewport) => scene?.showCloseup(sceneAvailable ? unit : undefined, viewport));
  try {
    scene = mountScene(canvas, labels, tagList, (unit, valve) => {
      if (valve) selectValve(valve);
      else selectArea(unit);
    }, trackTag, chooseCircuit, selectFeed, onContextChange);
  } catch (error) {
    console.warn('WebGL scene unavailable', error);
    setSceneAvailability(false);
    switchView('2d');
  }

  function setSceneAvailability(available) {
    sceneAvailable = available;
    threeButton.disabled = !available;
    threeButton.title = available ? '' : 'WebGL을 사용할 수 없습니다.';
    canvasError.hidden = available;
  }

  function onContextChange(available) {
    if (!available) {
      resume3D = visible === '3d';
      canvasError.textContent = 'WebGL 연결이 끊겨 3D를 일시적으로 표시할 수 없습니다.';
    }
    setSceneAvailability(available);
    switchView(available && resume3D ? '3d' : '2d');
  }

  // Stable keyboard path to the selected unit's interior tags, independent of projected label overlap.
  function renderInterior() {
    const list = INTERIOR_PARTS[area];
    internalsLabel.textContent = list ? `${AREAS[area].code} 내부` : '내부 전체';
    internals.title = list ? `${AREAS[area].code} 단면만 표시` : '모든 설비 내부 표시';
    interiorTools.hidden = !(flags.internals && visible === '3d' && scene);
    const units = scene?.interiorAreas().filter((unit) => INTERIOR_PARTS[unit]) ?? [];
    partStrip.hidden = interiorTools.hidden || !flags.tags || !units.length;
    if (partStrip.hidden) return;
    const key = units.join(',');
    if (partStrip.dataset.key !== key) {
      const focusedPart = partStrip.contains(document.activeElement) ? document.activeElement.dataset.tag : '';
      partStrip.dataset.key = key;
      partStrip.innerHTML = units.map((unit) => `<div class="interior-unit" role="group" aria-label="${AREAS[unit].code} 내부">
        <span class="toolbar-label">${AREAS[unit].code} 내부</span>${INTERIOR_PARTS[unit].map(([tag, name]) =>
          `<button type="button" data-area="${unit}" data-tag="${tag}">${escape(name)}<code>${tag}</code></button>`).join('')}</div>`).join('');
      if (focusedPart) partStrip.querySelector(`[data-tag="${focusedPart}"]`)?.focus({ preventScroll: true });
    }
    for (const button of partStrip.querySelectorAll('button')) button.setAttribute('aria-pressed', button.dataset.tag === tracked);
  }
  partStrip.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-tag]');
    if (button) trackTag(button.dataset.tag, button.dataset.area);
  });

  function renderTagList() {
    tagListButton.hidden = visible !== '3d' || !scene;
    tagListButton.disabled = !flags.tags;
    tagList.hidden = tagListButton.hidden || tagListButton.disabled || !tagListOpen;
    tagListButton.setAttribute('aria-expanded', !tagList.hidden);
  }
  tagListButton.addEventListener('click', () => {
    tagListOpen = !tagListOpen;
    renderTagList();
  });

  function switchView(next) {
    visible = next;
    const three = next === '3d';
    canvas.hidden = !three;
    if (!scene) labels.hidden = true;
    diagramRoot.hidden = three;
    threeButton.setAttribute('aria-pressed', three);
    twoButton.setAttribute('aria-pressed', !three);
    tags.disabled = internals.disabled = !three || !scene;
    byId('stage').dataset.view = next;
    scene?.display(three);
    diagram.focus(area, area === 'plant', focusedTag);
    renderInterior();
    renderTagList();
  }

  function chooseCircuit(next, refocus = true) {
    if (next && !(next in CIRCUITS)) return;
    if (circuit !== next) {
      circuit = next;
      for (const button of circuitButtons) button.setAttribute('aria-pressed', button.dataset.circuit === next);
      scene?.circuit(next);
      diagram.circuit(next);
      legend(next);
    }
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
  twoButton.addEventListener('click', () => {
    resume3D = false;
    switchView('2d');
  });
  for (const button of circuitButtons) button.addEventListener('click', () => chooseCircuit(button.dataset.circuit));
  toggle(flows, 'flows', (enabled) => { scene?.flow(enabled); diagram.flow(enabled); });
  toggle(pressure, 'pressure', applyPressure);
  toggle(tags, 'tags', (enabled) => {
    tags.title = enabled ? '태그 가리기' : '태그 표시';
    scene?.tags(enabled);
    renderInterior();
    renderTagList();
  });
  // Toggling the interior keeps the selected unit, camera, tracked tag and telemetry view.
  toggle(internals, 'internals', (enabled) => {
    scene?.internals(enabled);
    renderInterior();
  });

  function applyPressure(enabled) {
    scene?.pressure(enabled);
    diagram.pressure(enabled);
  }
  diagram.flow(true);
  applyPressure(true);
  legend('');
  renderInterior();
  renderTagList();
  return {
    focus(next, reset, tag) {
      area = next;
      focusedTag = tag;
      scene?.go(next, reset, tag);
      diagram.focus(next, reset, tag);
      renderInterior();
    },
    circuit: chooseCircuit,
    track(tagArea, tag) {
      tracked = tag;
      scene?.track(tagArea, tag);
      diagram.track(tagArea, tag);
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
      if (saved.view !== visible && (saved.view !== '3d' || sceneAvailable)) switchView(saved.view);
      if (saved.circuit !== circuit) chooseCircuit(saved.circuit, false);
      scene?.restoreView(saved.camera);
      if (saved.scroll && visible === '2d') [diagramRoot.scrollLeft, diagramRoot.scrollTop] = saved.scroll;
    },
    update(status) {
      scene?.updateVisuals(status);
      diagram.update(status);
    },
  };
}
