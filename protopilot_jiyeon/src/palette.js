// Canvas and WebGL read the same color tokens as the DOM and SVG.
export function readColor(token) {
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim();
}
