export function populateAnnotationList(host, items) {
  for (const item of items) {
    const clickable = item.el instanceof HTMLButtonElement;
    const entry = document.createElement(clickable ? 'button' : 'div');
    entry.className = 'tag-list-entry';
    const caption = document.createElement('span');
    caption.textContent = item.caption;
    entry.append(caption);
    if (item.value || item.def) {
      item.listReading = document.createElement('small');
      if (item.value) {
        item.listReading.className = 'tag-list-pressure';
        item.listReading.textContent = '— kPa(g)';
      }
      entry.append(item.listReading);
    }
    if (clickable) {
      entry.type = 'button';
      entry.addEventListener('click', () => item.el.click());
    }
    item.listEntry = entry;
    host.append(entry);
  }
}
