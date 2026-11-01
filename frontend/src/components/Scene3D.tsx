import { useEffect, useMemo, useRef, useState } from 'react';
import { stageLabel } from '../lib/format';
import { fmtMs, STATE_SHORT, type StageSnap } from '../lib/controlRoom';

/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Spatial view of the REAL workflow graph, built with CSS 3D transforms (no WebGL dependency):
 * a tilted floor, extruded stage blocks, billboard labels that always face the camera, glowing
 * dependency paths and a camera that can orbit, zoom and focus a stage. Every visual state comes
 * from `snaps`, which are derived only from recorded backend events.
 */
const DX = 138, DY = 64, BR = { x: -34, y: 78 }, PAD = 110, BW = 96, BH = 58, THICK = 16;

const ICON: Record<string, string> = {
  ingestion: 'M4 7h16M4 12h16M4 17h10', data_validation: 'M5 12l4 4 10-10', discharge_readiness: 'M7 4h10v16H7zM10 9h4M10 13h4',
  billing: 'M6 4h12v16l-3-2-3 2-3-2-3 2zM9 9h6M9 13h6', eligibility: 'M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z',
  adjudication: 'M12 4v16M5 8h14M7 8l-3 6h6zM17 8l-3 6h6z', historical: 'M4 19V5M4 19h16M8 15l3-4 3 2 5-6',
  reconciliation: 'M4 9h12l-3-3M20 15H8l3 3', root_blocker: 'M12 4a8 8 0 100 16 8 8 0 000-16zM12 8v5M12 16v.5',
  review_routing: 'M5 6h6v6H5zM13 12h6v6h-6zM11 9h4v3', claim_generation: 'M7 3h7l4 4v14H7zM14 3v4h4M10 13h5M10 17h5',
};
const DONE = new Set(['completed', 'issue', 'blocked']);

function useReducedMotion() {
  const [r, setR] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const m = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!m) return undefined;
    const f = () => setR(m.matches);
    m.addEventListener?.('change', f);
    return () => m.removeEventListener?.('change', f);
  }, []);
  return r;
}

export default function Scene3D({ stages, waves, snaps, selected, onSelect, playing = false, interactive = true, height = 560, overlay, label }: {
  stages: { id: string; deps: string[] }[]; waves: string[][]; snaps: Record<string, StageSnap>; selected?: string | null;
  onSelect?: (id: string) => void; playing?: boolean; interactive?: boolean; height?: number; overlay?: React.ReactNode; label?: string;
}) {
  const reduced = useReducedMotion();
  const box = useRef<HTMLDivElement>(null);
  const { pos, W, H, order } = useMemo(() => {
    const p: Record<string, { x: number; y: number; n: number }> = {};
    let n = 0;
    waves.forEach((w, i) => {
      const cx = PAD + i * DX, cy = PAD + i * DY;
      w.forEach((s, j) => {
        n += 1;
        const off = w.length === 1 ? 0 : (j - (w.length - 1) / 2) * 2;      // -1 / +1 for the parallel pair
        p[s] = { x: cx + off * BR.x * -1, y: cy + off * BR.y * -1, n };
      });
    });
    const xs = Object.values(p).map((v) => v.x), ys = Object.values(p).map((v) => v.y);
    const minX = Math.min(...xs) - PAD, minY = Math.min(...ys) - PAD;
    Object.values(p).forEach((v) => { v.x -= minX; v.y -= minY; });
    return { pos: p, W: Math.max(...xs) - minX + PAD, H: Math.max(...ys) - minY + PAD, order: waves.flat() };
  }, [waves]);

  const [cam, setCam] = useState({ tilt: 54, spin: -16, zoom: 1, panX: 0, panY: 0 });
  const [fitK, setFitK] = useState(0.7);
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const fit = () => { const w = el.clientWidth; setFitK(Math.max(0.35, Math.min(1.2, (w * 0.86) / W))); };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [W]);
  // camera-like focus on the selected stage
  const focus = selected && pos[selected] ? pos[selected] : null;
  const tx = focus ? W / 2 - focus.x : cam.panX, ty = focus ? H / 2 - focus.y : cam.panY;
  const k = fitK * cam.zoom * (focus ? 1.35 : 1);

  const drag = useRef<{ x: number; y: number; tilt: number; spin: number; panX: number; panY: number; pan: boolean } | null>(null);
  const reset = () => { setCam({ tilt: 54, spin: -16, zoom: 1, panX: 0, panY: 0 }); if (selected) onSelect?.(selected); };

  const sel = selected ? stages.find((s) => s.id === selected) : null;
  const up = new Set(sel?.deps || []);
  const down = new Set(stages.filter((s) => selected && s.deps.includes(selected)).map((s) => s.id));
  const running = order.filter((s) => snaps[s]?.state === 'running');

  return (
    <div className={`s3d ${reduced ? 'reduced' : ''}`} ref={box} style={{ height }}
      role="group" aria-label={label || 'Spatial view of the 11 workflow stages and their real dependencies. Select a stage to inspect it.'}
      onWheel={(e) => { if (interactive && (e.ctrlKey || e.metaKey)) { e.preventDefault(); setCam((c) => ({ ...c, zoom: Math.min(2.4, Math.max(0.5, c.zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08))) })); } }}
      onPointerDown={(e) => {
        if (!interactive || (e.target as Element).closest('.s3d-node, .s3d-ui')) return;
        drag.current = { x: e.clientX, y: e.clientY, tilt: cam.tilt, spin: cam.spin, panX: cam.panX, panY: cam.panY, pan: e.shiftKey || e.button === 1 };
        (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        const dx = e.clientX - d.x, dy = e.clientY - d.y;
        if (d.pan) setCam((c) => ({ ...c, panX: d.panX + dx / k, panY: d.panY + dy / k }));
        else setCam((c) => ({ ...c, spin: Math.max(-40, Math.min(25, d.spin + dx * 0.2)), tilt: Math.max(25, Math.min(66, d.tilt - dy * 0.15)) }));
      }}
      onPointerUp={() => { drag.current = null; }}>
      <div className="s3d-sky" aria-hidden="true" />
      <div className="s3d-cam" style={{ width: W, height: H, marginLeft: -W / 2, marginTop: -H / 2, ['--tilt' as string]: `${cam.tilt}deg`, ['--spin' as string]: `${cam.spin}deg`,
        transform: `scale(${k}) rotateX(${cam.tilt}deg) rotateZ(${cam.spin}deg) translate(${tx}px, ${ty}px)` }}>
        <div className="s3d-floor" aria-hidden="true" />
        <svg className="s3d-edges" width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
          <defs><filter id="s3d-glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="4" /></filter></defs>
          {stages.flatMap((s) => s.deps.map((d) => {
            const a = pos[d], b = pos[s.id];
            if (!a || !b) return null;
            const mx = (a.x + b.x) / 2;
            const path = `M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`;
            const sa = snaps[d]?.state, sb = snaps[s.id]?.state;
            const delivered = DONE.has(sa) && ['running', 'completed', 'issue', 'blocked', 'failed'].includes(sb);
            const flowing = playing && delivered && sb === 'running';
            const hi = !!selected && ((s.id === selected && up.has(d)) || (d === selected && down.has(s.id)));
            const cls = `${delivered ? 'on' : ''} ${flowing ? 'flow' : ''} ${hi ? 'hi' : ''} ${selected && !hi ? 'dim' : ''}`;
            return (
              <g key={`${d}-${s.id}`} className={`s3d-edge ${cls}`}>
                {(delivered || hi) && <path className="glow" d={path} filter="url(#s3d-glow)" />}
                <path className="line" d={path} />
                {flowing && !reduced && <circle className="pkt" r={5}><animateMotion dur="0.8s" repeatCount="indefinite" path={path} /></circle>}
              </g>);
          }))}
        </svg>
        {order.map((id) => {
          const p = pos[id];
          const sn = snaps[id] || ({ state: 'waiting', label: 'Waiting' } as StageSnap);
          const rel = !selected ? '' : id === selected ? 'sel' : up.has(id) ? 'up' : down.has(id) ? 'down' : 'other';
          const fin = sn.duration !== undefined && ['completed', 'issue', 'blocked', 'failed'].includes(sn.state);
          const nf = (sn.findings || []).length;
          return (
            <div key={id} className={`s3d-node st-${sn.state} ${rel}`} data-stage={id} data-state={sn.state}
              style={{ left: p.x - BW / 2, top: p.y - BH / 2 }}>
              <div className="s3d-shadow" />
              <div className="s3d-blk">
                <div className="f-top"><svg viewBox="0 0 24 24" aria-hidden="true"><path d={ICON[id] || ''} /></svg></div>
                <div className="f-front" style={{ height: THICK }} />
                <div className="f-side" style={{ width: THICK }} />
                {sn.state === 'running' && <div className="f-ring" />}
              </div>
              <button type="button" className="s3d-bb" aria-pressed={selected === id} tabIndex={interactive ? 0 : -1}
                aria-label={`Stage ${p.n}, ${stageLabel(id)}: ${STATE_SHORT[sn.state]}${fin ? `, ${fmtMs(sn.duration)}` : ''}${nf ? `, ${nf} finding${nf > 1 ? 's' : ''}` : ''}`}
                onClick={() => interactive && onSelect?.(id)}>
                <span className="bb-top"><span className="bb-n">{p.n}</span><span className="bb-name">{stageLabel(id)}</span></span>
                <span className="bb-row"><span className="bb-state">{STATE_SHORT[sn.state]}</span>
                  {fin ? <span className="bb-ms">{fmtMs(sn.duration)}</span> : null}
                  {nf ? <span className="bb-f" title={`${nf} finding(s)`}>{nf}</span> : null}</span>
              </button>
            </div>
          );
        })}
      </div>
      {interactive && (
        <div className="s3d-ui s3d-tools" role="toolbar" aria-label="Camera">
          <button type="button" aria-label="Zoom in" onClick={() => setCam((c) => ({ ...c, zoom: Math.min(2.4, c.zoom * 1.2) }))}>＋</button>
          <button type="button" aria-label="Zoom out" onClick={() => setCam((c) => ({ ...c, zoom: Math.max(0.5, c.zoom / 1.2) }))}>－</button>
          <button type="button" onClick={reset}>Reset view</button>
          <button type="button" aria-label="Tilt camera" onClick={() => setCam((c) => ({ ...c, tilt: c.tilt > 40 ? 30 : 54 }))}>Tilt</button>
          <span className="s3d-hint">Drag to orbit · Shift+drag to pan · Ctrl+wheel to zoom</span>
        </div>)}
      {running.length > 1 && <div className="s3d-ui s3d-conc" role="status">{running.length} stages executing at the same instant: {running.map(stageLabel).join(' ∥ ')}</div>}
      {overlay}
    </div>
  );
}
