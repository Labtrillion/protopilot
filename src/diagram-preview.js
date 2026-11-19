import { placeAnnotation } from './annotation-layout.js';
import { AREAS, LEVEL_TAGS, levelReading } from './topology.js';
import { reading } from './ui.js';

export function mountDiagramPreview(host, svg, draw) {
  const card = document.createElement('div');
  card.className = 'diagram-preview';
  card.hidden = true;
  card.setAttribute('role', 'img');
  card.innerHTML = `<div class="diagram-preview-heading"><code></code><b></b></div>
    <canvas width="400" height="300" aria-hidden="true"></canvas><div class="diagram-preview-level" hidden></div>`;
  host.after(card);
  const canvas = card.querySelector('canvas');
  const code = card.querySelector('code'), name = card.querySelector('b'), level = card.querySelector('.diagram-preview-level');
  const chips = new Map([...svg.querySelectorAll('.pfd-chip')].map((chip) => [chip.parentElement.dataset.area, chip]));
  const obstacles = [...svg.querySelectorAll('.pfd-chip, .pfd-valve path, .pfd-pressure')];
  let area, anchor, symbol, status, renderedArea, renderedLevel;

  function render() {
    if (!area) return;
    const value = levelReading(status, area);
    if (renderedArea === area && Object.is(renderedLevel, value)) return;
    if (!draw(area, canvas)) {
      card.hidden = true;
      return;
    }
    renderedArea = area;
    renderedLevel = value;
    code.textContent = AREAS[area].code;
    name.textContent = AREAS[area].name;
    level.hidden = !(area in LEVEL_TAGS);
    level.textContent = `액위 ${Number.isFinite(value) ? `${reading(value)}%` : '—'}`;
    card.setAttribute('aria-label', `${code.textContent} ${name.textContent} 3D 미리보기${level.hidden ? '' : `, ${level.textContent}`}`);
    card.hidden = false;
  }

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
    const selected = (symbol ? [anchor, symbol] : [anchor]).map(localRect).filter(Boolean);
    const occupied = [...obstacles.map(localRect).filter(Boolean), ...selected.slice(1)];
    const place = (blocked, range) => placeAnnotation(x - w / 2, top - h - 8, w, h, width, height, blocked, range);
    const box = onScreen && (place(occupied, w / 2) ?? place(selected, w) ?? place(selected.slice(0, 1), w));
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
    show(next) {
      anchor = chips.get(next);
      if (host.hidden || !anchor) {
        area = renderedArea = undefined;
        card.hidden = true;
        return;
      }
      area = next;
      symbol = svg.querySelector(`[data-symbol="${area}"]`);
      render();
      position();
    },
    update(next) {
      status = next;
      render();
    },
    position,
  };
}
