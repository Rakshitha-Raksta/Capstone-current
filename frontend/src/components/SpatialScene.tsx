import { Component, lazy, Suspense, useMemo, type ReactNode } from 'react';
import Scene3D from './Scene3D';
import type { SceneProps } from './WorkflowScene3D';

// Three.js is loaded only when a 3D scene is shown (separate chunk).
const WebGLScene = lazy(() => import('./WorkflowScene3D'));

function hasWebGL(): boolean {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch { return false; }
}

/** If the WebGL scene throws while starting, show the CSS fallback instead of a blank area. */
class Guard extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

/** Real WebGL scene when available; the CSS 3D scene otherwise (labelled). */
export default function SpatialScene(props: SceneProps) {
  const webgl = useMemo(hasWebGL, []);
  const fallback = (
    <div className="scene-fallback">
      <Scene3D {...props} height={typeof props.height === 'number' ? props.height : 560} />
      <div className="small faint" role="note" style={{ marginTop: 4 }}>WebGL is not available in this browser, so a CSS 3D rendering of the same workflow is shown.</div>
    </div>);
  if (!webgl) return fallback;
  return (
    <Guard fallback={fallback}>
      <Suspense fallback={<div className="wg wg-loading" style={{ height: props.height }} role="status">Loading 3D scene…</div>}>
        <WebGLScene {...props} />
      </Suspense>
    </Guard>
  );
}
