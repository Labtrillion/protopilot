import { placeAnnotation } from './annotation-layout.js';
import { AREAS } from './topology.js';

export function mountDiagramPreview(host, svg, draw) {
  const card = document.createElement('div');
  card.className = 'diagram-preview';
  card.hidden = true;
  card.setAttribute('role', 'img');
  card.innerHTML = '<canvas width="128" height="112" aria-hidden="true"></canvas><span><code></code><b></b></span>';
  host.after(card);
  const canvas = card.querySelector('canvas');
  const code = card.querySelector('code'), name = card.querySelector('b');
  const chips = new Map([...svg.querySelectorAll('.pfd-chip')].map((chip) => [chip.parentElement.dataset.area, chip]));
  const obstacles = [...svg.querySelectorAll('.pfd-chip, .pfd-valve path, .pfd-pressure')];
  let anchor, symbol, renderedArea;

  function position() {
    if (card.hidden) return;
    const viewport = host.getBoundingClientRect();
    const width = host.clientWidth, height = host.clientHeight;
    const localRect = (node) => {
      const rect = node.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      return { left: rect.left - viewport.left, right: rect.right - viewport.left,
        top: rect.top - viewport.top, bottom: rect.bottom - viewport.top };
    };
    const { left, right, top, bottom } = localRect(anchor) ?? {};
    const x = (left + right) / 2, y = (top + bottom) / 2;
    const w = card.offsetWidth, h = card.offsetHeight;
    const onScreen = x >= 0 && x <= width && y >= 0 && y <= height;
    const occupied = (symbol ? [...obstacles, symbol] : obstacles).map(localRect).filter(Boolean);
    const box = onScreen && placeAnnotation(x - w / 2, top - h - 8, w, h, width, height, occupied, 120);
    card.toggleAttribute('data-placed', Boolean(box));
    if (!box) return;

    card.style.left = `${box.left}px`;
    card.style.top = `${box.top}px`;
    const startX = Math.max(box.left, Math.min(box.right, x));
    const startY = Math.max(box.top, Math.min(box.bottom, y));
    const dx = Math.max(left, Math.min(right, startX)) - startX;
    const dy = Math.max(top, Math.min(bottom, startY)) - startY;
    card.style.setProperty('--leader-x', `${startX - box.left}px`);
    card.style.setProperty('--leader-y', `${startY - box.top}px`);
    card.style.setProperty('--leader-length', `${Math.hypot(dx, dy)}px`);
    card.style.setProperty('--leader-angle', `${Math.atan2(dy, dx)}rad`);
  }

  host.addEventListener('scroll', position, { passive: true });
  const sizes = new ResizeObserver(position);
  sizes.observe(host);
  sizes.observe(svg);
  sizes.observe(card);
  return {
    show(area) {
      anchor = chips.get(area);
      if (host.hidden || !anchor) {
        card.hidden = true;
        return;
      }
      if (renderedArea !== area) {
        if (!draw(area, canvas)) {
          card.hidden = true;
          return;
        }
        renderedArea = area;
        code.textContent = AREAS[area].code;
        name.textContent = AREAS[area].name;
        card.setAttribute('aria-label', `${AREAS[area].code} ${AREAS[area].name} 3D 미리보기`);
      }
      symbol = svg.querySelector(`[data-symbol="${area}"]`);
      card.hidden = false;
      position();
    },
    position,
  };
}
