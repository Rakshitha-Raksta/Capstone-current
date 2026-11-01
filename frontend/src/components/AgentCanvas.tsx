import { useMemo, useRef, useState } from 'react';
import { stageLabel } from '../lib/format';
import { fmtMs, STATE_SHORT, type StageSnap } from '../lib/controlRoom';

/* eslint-disable @typescript-eslint/no-explicit-any */
const NW = 196, NH = 104, CG = 56, RG = 84, PAD = 28, COLS = 5, SG = 16;

// Small line icons, one per stage (presentation only)
const ICON: Record<string, string> = {
  ingestion: 'M4 7h16M4 12h16M4 17h10', data_validation: 'M5 12l4 4 10-10', discharge_readiness: 'M7 4h10v16H7zM10 9h4M10 13h4',
  billing: 'M6 4h12v16l-3-2-3 2-3-2-3 2zM9 9h6M9 13h6', eligibility: 'M12 3l7 3v6c0 4-3 7-7 9-4-2-7-5-7-9V6z',
  adjudication: 'M12 4v16M5 8h14M7 8l-3 6h6zM17 8l-3 6h6z', historical: 'M4 19V5M4 19h16M8 15l3-4 3 2 5-6',
  reconciliation: 'M4 9h12l-3-3M20 15H8l3 3', root_blocker: 'M12 4a8 8 0 100 16 8 8 0 000-16zM12 8v5M12 16v.5',
  review_routing: 'M5 6h6v6H5zM13 12h6v6h-6zM11 9h4v3', claim_generation: 'M7 3h7l4 4v14H7zM14 3v4h4M10 13h5M10 17h5',
};

export default function AgentCanvas({ stages, waves, snaps, selected, onSelect, replayT }: {
  stages: any[]; waves: string[][]; snaps: Record<string, StageSnap>; selected: string | null;
  onSelect: (id: string) => void; replayT: number | null;
}) {
  const layout = useMemo(() => {
    const rows = [waves.slice(0, COLS), waves.slice(COLS)];
    const slotH = rows.map((r) => Math.max(1, ...r.map((w) => w.length))).map((n) => n * NH + (n - 1) * SG);
    const pos: Record<string, { x: number; y: number; row: number; col: number; n: number }> = {};
    let n = 0;
    rows.forEach((r, ri) => {
      const top = PAD + (ri === 0 ? 0 : slotH[0] + RG);
      r.forEach((w, wi) => {
        const col = ri === 0 ? wi : COLS - 1 - wi;
        const h = w.length * NH + (w.length - 1) * SG;
        w.forEach((s, j) => { n += 1; pos[s] = { x: PAD + col * (NW + CG), y: top + (slotH[ri] - h) / 2 + j * (NH + SG), row: ri, col, n }; });
      });
    });
    return { pos, W: PAD * 2 + COLS * NW + (COLS - 1) * CG, H: PAD * 2 + slotH[0] + RG + (slotH[1] || 0) };
  }, [waves]);
  const { pos, W, H } = layout;
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ px: number; py: number; x: number; y: number } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const zoom = (f: number) => setView((v) => {
    const k = Math.min(3, Math.max(0.5, v.k * f));
    return { k, x: v.x + (W / v.k - W / k) / 2, y: v.y + (H / v.k - H / k) / 2 };
  });
  const fit = () => setView({ x: 0, y: 0, k: 1 });

  const sel = selected ? stages.find((s) => s.id === selected) : null;
  const up = new Set<string>(sel?.deps || []);
  const down = new Set<string>(sel?.downstream || []);
  const running = Object.entries(snaps).filter(([, s]) => s.state === 'running').map(([k]) => k);

  const path = (a: string, b: string) => {
    const p = pos[a], q = pos[b];
    if (p.row === q.row && q.col > p.col) {
      const x1 = p.x + NW, y1 = p.y + NH / 2, x2 = q.x - 8, y2 = q.y + NH / 2, m = (x1 + x2) / 2;
      return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`;
    }
    if (p.row === q.row) {
      const x1 = p.x, y1 = p.y + NH / 2, x2 = q.x + NW + 8, y2 = q.y + NH / 2, m = (x1 + x2) / 2;
      return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`;
    }
    const x1 = p.x + NW / 2, y1 = p.y + NH, x2 = q.x + NW / 2, y2 = q.y - 8, m = (y1 + y2) / 2;
    return `M${x1},${y1} C${x1},${m} ${x2},${m} ${x2},${y2}`;
  };
  const DONE = new Set(['completed', 'issue', 'blocked']);

  return (
    <div className="ac-wrap">
      <div className="ac-tools" role="toolbar" aria-label="Canvas view">
        <button type="button" onClick={() => zoom(1.2)} aria-label="Zoom in">＋</button>
        <button type="button" onClick={() => zoom(1 / 1.2)} aria-label="Zoom out">－</button>
        <button type="button" onClick={fit}>Fit</button>
        <span className="ac-hint">Drag to pan · Ctrl + wheel to zoom</span>
        {running.length > 1 && <span className="ac-conc" role="status">{running.length} stages running concurrently: {running.map(stageLabel).join(' ∥ ')}</span>}
      </div>
      <svg ref={svg} className="ac-svg" viewBox={`${view.x} ${view.y} ${W / view.k} ${H / view.k}`} role="group"
        aria-label="Workflow orchestration canvas: 11 rule-based stages and their real dependencies. Select a stage to inspect it."
        onWheel={(e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1); } }}
        onPointerDown={(e) => { if ((e.target as Element).closest('.ac-node')) return; drag.current = { px: e.clientX, py: e.clientY, x: view.x, y: view.y }; (e.target as Element).setPointerCapture?.(e.pointerId); }}
        onPointerMove={(e) => {
          if (!drag.current || !svg.current) return;
          const r = svg.current.getBoundingClientRect();
          const sx = (W / view.k) / r.width;
          setView((v) => ({ ...v, x: drag.current!.x - (e.clientX - drag.current!.px) * sx, y: drag.current!.y - (e.clientY - drag.current!.py) * sx }));
        }}
        onPointerUp={() => { drag.current = null; }}>
        <defs>
          <marker id="ac-arr" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" className="ac-arrow" /></marker>
          <marker id="ac-arr-hi" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" className="ac-arrow-hi" /></marker>
        </defs>
        {stages.flatMap((s) => s.deps.map((d: string) => {
          if (!pos[d] || !pos[s.id]) return null;
          const a = snaps[d], b = snaps[s.id];
          // data actually delivered: source finished and the target then ran (or is running) on it
          const delivered = DONE.has(a?.state) && ['running', 'completed', 'issue', 'blocked', 'failed'].includes(b?.state);
          const flowing = replayT !== null && delivered && b.state === 'running';
          const hi = selected && ((s.id === selected && up.has(d)) || (d === selected && down.has(s.id)));
          return <path key={`${d}-${s.id}`} d={path(d, s.id)} markerEnd={hi ? 'url(#ac-arr-hi)' : 'url(#ac-arr)'}
            className={`ac-edge ${delivered ? 'delivered' : ''} ${flowing ? 'flowing' : ''} ${hi ? 'hi' : ''} ${selected && !hi ? 'dim' : ''}`} />;
        }))}
        {stages.map((s) => {
          const p = pos[s.id];
          const sn = snaps[s.id] || { state: 'waiting', label: 'Waiting for dependencies' };
          const rel = !selected ? '' : s.id === selected ? 'sel' : up.has(s.id) ? 'up' : down.has(s.id) ? 'down' : 'other';
          const detail = sn.duration !== undefined && ['completed', 'issue', 'blocked', 'failed'].includes(sn.state)
            ? `${fmtMs(sn.duration)} · ${(sn.findings || []).length} finding(s)`
            : sn.state === 'running' ? 'executing now (replay)' : sn.state === 'reused' ? `output reused (#${sn.reusedFrom ?? '?'})`
              : sn.state === 'notrun' ? 'previous result kept' : sn.state === 'skipped' ? 'dependency failed' : `${s.deps.length} dependenc${s.deps.length === 1 ? 'y' : 'ies'}`;
          return (
            <g key={s.id} className={`ac-node st-${sn.state} ${rel}`} data-stage={s.id} data-state={sn.state} transform={`translate(${p.x},${p.y})`}
              role="button" tabIndex={0} aria-pressed={selected === s.id} aria-label={`Stage ${p.n}, ${stageLabel(s.id)}: ${sn.label}${sn.duration !== undefined ? `, ${fmtMs(sn.duration)}` : ''}`}
              onClick={() => onSelect(s.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(s.id); } }}>
              <rect className="ac-shadow" x={4} y={8} width={NW} height={NH} rx={16} />
              <rect className="ac-ring" x={-5} y={-5} width={NW + 10} height={NH + 10} rx={20} />
              <rect className="ac-body" width={NW} height={NH} rx={16} />
              <circle className="ac-num" cx={22} cy={24} r={13} />
              <text className="ac-num-t" x={22} y={28} textAnchor="middle">{p.n}</text>
              <g transform="translate(162,44) scale(0.85)" className="ac-icon" aria-hidden="true"><path d={ICON[s.id] || ''} /></g>
              <text className="ac-title" x={42} y={29}>{stageLabel(s.id)}</text>
              <rect className="ac-pill" x={14} y={46} width={STATE_SHORT[sn.state].length * 6.6 + 20} height={20} rx={10} />
              <text className="ac-pill-t" x={24} y={60}>{STATE_SHORT[sn.state]}</text>
              <text className="ac-meta" x={14} y={88}>{detail}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
