import { CIRCUITS, CORE_PRESSURES, STREAMS, codeFor, streamMatchesTag } from './topology.js';
import { mountDiagram } from './diagram.js';
import { mountScene } from './scene.js';
import { byId, legend, renderPressures } from './ui.js';

export function mountPlantView({ selectArea, selectValve, onCircuit }) {
  const canvas = byId('canvas'), labels = byId('model-labels'), diagramRoot = byId('diagram');
  const threeButton = byId('view-3d'), twoButton = byId('view-2d');
  const flows = byId('view-flow'), pressure = byId('view-pressure'), internals = byId('toggle-internals'), tags = byId('toggle-tags');
  const circuitButtons = [...document.querySelectorAll('.circuit-controls button[data-circuit]')];
  let scene, current, area = 'plant', circuit = '', visible = '3d', tracked = '', focusedTag = '';
  const flags = { flows: true, pressure: true, internals: false, tags: true };
  const diagram = mountDiagram(diagramRoot, selectArea, chooseCircuit, selectValve);
  try {
    scene = mountScene(canvas, labels, selectArea);
  } catch (error) {
    console.warn('WebGL scene unavailable', error);
    threeButton.disabled = true;
    threeButton.title = '이 브라우저에서 WebGL 2를 사용할 수 없습니다.';
    switchView('2d');
  }

  function switchView(next) {
    visible = next;
    const three = next === '3d';
    canvas.hidden = labels.hidden = !three;
    diagramRoot.hidden = three;
    threeButton.setAttribute('aria-pressed', three);
    twoButton.setAttribute('aria-pressed', !three);
    tags.disabled = internals.disabled = !three;
    byId('stage').dataset.view = next;
    byId('stage-caption-text').textContent = three ? '공정 모델' : 'Fig. 1 · 공정도';
    byId('canvas-error').hidden = !three || Boolean(scene);
    scene?.display(three);
    diagram.focus(area, area === 'plant', focusedTag);
    legend(circuit, three && flags.internals);
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

  function toggle(button, key, onChange) {
    button.addEventListener('click', () => {
      flags[key] = !flags[key];
      button.setAttribute('aria-pressed', flags[key]);
      onChange(flags[key]);
    });
  }
  threeButton.addEventListener('click', () => switchView('3d'));
  twoButton.addEventListener('click', () => switchView('2d'));
  for (const button of circuitButtons) button.addEventListener('click', () => chooseCircuit(button.dataset.circuit));
  toggle(flows, 'flows', (enabled) => { scene?.flow(enabled); diagram.flow(enabled); });
  toggle(pressure, 'pressure', applyPressure);
  toggle(tags, 'tags', (enabled) => scene?.tags(enabled));
  toggle(internals, 'internals', (enabled) => {
    scene?.internals(enabled);
    if (enabled && area !== 'plant') selectArea('plant', '', true);
    legend(circuit, enabled);
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
  return {
    focus(next, reset, tag) {
      area = next;
      focusedTag = tag;
      scene?.go(next, reset, tag);
      diagram.focus(next, reset, tag);
    },
    circuit: chooseCircuit,
    track(signalArea, tag) {
      tracked = tag;
      scene?.track(signalArea, tag);
      diagram.track(signalArea, tag);
      renderPressures(current, flags.pressure, tracked);
    },
    reveal(tag) {
      if (!flags.pressure && Object.values(CORE_PRESSURES).map(codeFor).includes(tag)) pressure.click();
      if (visible === '3d' && !flags.internals && ['XMEAS(8)', 'XMEAS(12)', 'XMEAS(15)', 'XMV(12)'].includes(tag)) internals.click();
      const stream = STREAMS.find((line) => streamMatchesTag(line, tag));
      if (stream && !flags.flows && !tag.startsWith('XMV')) flows.click();
      if (stream && circuit && stream.circuit !== circuit) chooseCircuit('', false);
    },
    showPressure() {
      if (!flags.pressure) pressure.click();
    },
    update(status) {
      current = status;
      scene?.updateVisuals(status);
      diagram.update(status);
      renderPressures(status, flags.pressure, tracked);
    },
  };
}
