import { ms, stageLabel } from '../lib/format';

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface NodeView { key: string; label: string; detail?: string; ev?: any; order?: number; t?: number }

const NODE_W = 178, NODE_H = 76, COL_GAP = 48, ROW_GAP = 70, PAD = 20, COLS = 5, STACK_GAP = 14;
const PROCESSED = new Set(['done', 'issue', 'blocked', 'reused', 'notrun', 'failed']);

/**
 * The real workflow (stages and dependency edges from the API) drawn as connected nodes on a
 * slightly tilted plane. Every visual state comes from `views`, which are derived only from the
 * events the orchestrator reported; nothing here animates on a timer.
 */
export default function StageGraph({ stages, waves, views, selected, onSelect }: {
  stages: { id: string; deps: string[] }[]; waves: string[][]; views: Record<string, NodeView>;
  selected: string | null; onSelect: (id: string) => void;
}) {
  // serpentine layout: first row left→right, second row right→left, so 10 waves fit a laptop width
  const rows = [waves.slice(0, COLS), waves.slice(COLS)];
  const slotH = rows.map((r) => Math.max(1, ...r.map((w) => w.length))).map((n) => n * NODE_H + (n - 1) * STACK_GAP);
  const pos: Record<string, { x: number; y: number; row: number; col: number }> = {};
  rows.forEach((r, ri) => {
    const top = PAD + (ri === 0 ? 0 : slotH[0] + ROW_GAP);
    r.forEach((w, wi) => {
      const col = ri === 0 ? wi : COLS - 1 - wi;
      const x = PAD + col * (NODE_W + COL_GAP);
      const h = w.length * NODE_H + (w.length - 1) * STACK_GAP;
      w.forEach((s, j) => { pos[s] = { x, y: top + (slotH[ri] - h) / 2 + j * (NODE_H + STACK_GAP), row: ri, col }; });
    });
  });
  const W = PAD * 2 + COLS * NODE_W + (COLS - 1) * COL_GAP;
  const H = PAD * 2 + slotH[0] + ROW_GAP + (slotH[1] || 0) + 8;

  const edge = (a: string, b: string) => {
    const p = pos[a], q = pos[b];
    if (p.row === q.row && q.col > p.col) {
      const x1 = p.x + NODE_W, y1 = p.y + NODE_H / 2, x2 = q.x - 6, y2 = q.y + NODE_H / 2, m = (x1 + x2) / 2;
      return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`;
    }
    if (p.row === q.row) {
      const x1 = p.x, y1 = p.y + NODE_H / 2, x2 = q.x + NODE_W + 6, y2 = q.y + NODE_H / 2, m = (x1 + x2) / 2;
      return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`;
    }
    const x1 = p.x + NODE_W / 2, y1 = p.y + NODE_H, x2 = q.x + NODE_W / 2, y2 = q.y - 6, m = (y1 + y2) / 2;
    return `M${x1},${y1} C${x1},${m} ${x2},${m} ${x2},${y2}`;
  };

  return (
    <div className="graph-stage">
      <div className="floor" aria-hidden="true" />
      <div className="graph-scroll">
        <div className="graph-tilt">
          <svg className="wf-graph" viewBox={`0 0 ${W} ${H}`} role="group"
            aria-label="Workflow stages as connected nodes, in execution order; select a stage for its details">
            <defs>
              <marker id="wf-arr" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path className="wf-arrow" d="M0,0 L8,4 L0,8 z" />
              </marker>
            </defs>
            {stages.flatMap((s) => s.deps.map((d) => {
              if (!pos[d] || !pos[s.id]) return null;
              const a = views[d]?.key, b = views[s.id]?.key;
              const cls = PROCESSED.has(a) && b && b !== 'pending' ? (b === 'notrun' ? 'dim' : 'done') : '';
              return <path key={`${d}-${s.id}`} className={`wf-edge ${cls}`} d={edge(d, s.id)} markerEnd="url(#wf-arr)" />;
            }))}
            {stages.map((s) => {
              const p = pos[s.id];
              const v = views[s.id] || { key: 'pending', label: 'Pending' };
              const fin = v.ev?.type === 'stage_finished' && v.ev.status !== 'FAILED';
              const sub = fin ? `${ms(v.ev.duration_ms)} · ${v.ev.findings.length} finding(s)`
                : v.key === 'reused' ? `reused from #${v.ev?.reused_from_execution ?? '?'}`
                  : v.key === 'notrun' ? 'previous result kept'
                    : v.key === 'failed' ? `failed · ${ms(v.ev?.duration_ms)}`
                      : v.key === 'skipped' ? 'dependency failed'
                        : v.key === 'running' ? `started at +${ms(v.t)}` : 'no event yet';
              const pillW = v.label.length * 6.6 + 18;
              return (
                <g key={s.id} className={`stage-node ${selected === s.id ? 'sel' : ''}`} data-stage={s.id} data-state={v.key}
                  transform={`translate(${p.x},${p.y})`} role="button" tabIndex={0} aria-pressed={selected === s.id}
                  aria-label={`${stageLabel(s.id)}: ${v.label}${fin ? `, ${ms(v.ev.duration_ms)}` : ''}`}
                  onClick={() => onSelect(s.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(s.id); } }}>
                  <rect className="shadow" x={3} y={6} width={NODE_W} height={NODE_H} rx={12} />
                  <rect className="ring" x={-4} y={-4} width={NODE_W + 8} height={NODE_H + 8} rx={15} />
                  <rect className="body" width={NODE_W} height={NODE_H} rx={12} />
                  <rect className="bar" x={0} y={10} width={4} height={NODE_H - 20} rx={2} />
                  <text className="t" x={14} y={24}>{stageLabel(s.id)}</text>
                  <rect className="pill" x={14} y={34} width={pillW} height={18} rx={9} />
                  <text className="pill-t" x={14 + pillW / 2} y={47} textAnchor="middle">{v.label}</text>
                  <text className="s" x={14} y={67}>{sub}</text>
                  {v.order ? <text className="s" x={NODE_W - 12} y={24} textAnchor="end">#{v.order}</text> : null}
                </g>
              );
            })}
          </svg>
        </div>
      </div>
      <div className="graph-legend" aria-hidden="true">
        <span style={{ ['--lg' as string]: 'var(--advisory)' }}>Running</span>
        <span style={{ ['--lg' as string]: 'var(--complete)' }}>Completed</span>
        <span style={{ ['--lg' as string]: 'var(--review)' }}>Issue found</span>
        <span style={{ ['--lg' as string]: 'var(--blocked)' }}>Blocked / failed</span>
        <span style={{ ['--lg' as string]: 'var(--reuse-bg)', ['--lgb' as string]: 'var(--reuse-border)' }}>Reused</span>
        <span style={{ ['--lg' as string]: 'var(--sunken)', ['--lgb' as string]: 'var(--border-strong)' }}>Not re-run / pending</span>
        <span>#n = order in which stages started</span>
      </div>
    </div>
  );
}
