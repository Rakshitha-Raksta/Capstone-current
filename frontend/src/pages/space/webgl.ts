/** WebGL feature test without importing Three.js (the 3D bundle is loaded only when this passes). */
export function webglSupported(): boolean {
  try {
    if (new URLSearchParams(window.location.search).get('webgl') === 'off') return false;   // test hook for the fallback
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch { return false; }
}
