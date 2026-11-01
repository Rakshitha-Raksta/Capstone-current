// Small, dependency-free SVG/CSS charts. All values come from API data.
import { money, stageLabel } from '../lib/format';

export function BarList({ items, format = (v: number) => String(v), label }: {
  items: { key: string; label: string; value: number; sub?: string }[]; format?: (v: number) => string; label: string;
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="barlist" role="list" aria-label={label}>
      {items.map((i) => (
        <div className="item" role="listitem" key={i.key}>
          <span title={i.label} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{i.label}</span>
          <div className="track" aria-hidden="true"><div className="fill" style={{ width: `${(i.value / max) * 100}%` }} /></div>
          <span className="num small">{format(i.value)}{i.sub && <span className="faint"> · {i.sub}</span>}</span>
        </div>
      ))}
    </div>
  );
}

const STATUS_COLORS: Record<string, string> = {
  PREPARATION_COMPLETE: 'var(--complete)', NEEDS_REVIEW: 'var(--review)', BLOCKED: 'var(--blocked)', ERROR: 'var(--blocked-ink)',
  NEW: 'var(--faint)', PROCESSING: 'var(--muted)',
};

export function StatusBar({ counts }: { counts: Record<string, number> }) {
  const order = ['PREPARATION_COMPLETE', 'NEEDS_REVIEW', 'BLOCKED', 'ERROR', 'PROCESSING', 'NEW'];
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
  const present = order.filter((k) => counts[k]);
  return (
    <div>
      <div className="stacked" role="img"
        aria-label={present.map((k) => `${k.toLowerCase().replace(/_/g, ' ')} ${counts[k]}`).join(', ')}>
        {present.map((k) => <div key={k} style={{ width: `${(counts[k] / total) * 100}%`, background: STATUS_COLORS[k] }} />)}
      </div>
      <div className="legend">
        {present.map((k) => (
          <span key={k}><i style={{ background: STATUS_COLORS[k] }} />{k.replace(/_/g, ' ').toLowerCase()} · {counts[k]}</span>
        ))}
      </div>
    </div>
  );
}

/** Horizontal waterfall from gross bill to estimated eligible amount. */
export function Waterfall({ steps }: { steps: { step: string; amount: number; basis?: string }[] }) {
  if (!steps?.length) return null;
  const gross = steps[0].amount || 1;
  const W = 560, rowH = 26, labelW = 170, valW = 96, barW = W - labelW - valW;
  let running = gross;
  const rows = steps.map((s, i) => {
    if (i === 0) return { ...s, x0: 0, x1: gross, kind: 'total' };
    if (s.step === 'estimated_eligible') return { ...s, x0: 0, x1: s.amount, kind: 'total' };
    const x1 = running;
    running += s.amount;
    return { ...s, x0: running, x1, kind: 'minus' };
  });
  const sx = (v: number) => (Math.max(0, v) / gross) * barW;
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${rows.length * rowH + 6}`} width="100%" role="img"
      aria-label={`Financial waterfall: ${rows.map((r) => `${r.step} ${money(r.amount)}`).join('; ')}`}>
      {rows.map((r, i) => {
        const y = i * rowH + 4;
        const w = Math.max(1, sx(r.x1) - sx(r.x0));
        const color = r.kind === 'total' ? (r.step === 'gross_bill' ? 'var(--muted)' : 'var(--accent)') : (r.amount === 0 ? 'var(--border-strong)' : 'var(--blocked)');
        return (
          <g key={r.step + i}>
            <text x={0} y={y + 15}>{r.step.replace(/_/g, ' ')}</text>
            <rect x={labelW + sx(r.x0)} y={y + 4} width={w} height={rowH - 10} rx={2} style={{ fill: color }} opacity={r.amount === 0 && r.kind === 'minus' ? 0.5 : 1} />
            <text x={W} y={y + 15} textAnchor="end" className="val">{r.kind === 'minus' && r.amount !== 0 ? '−' : ''}{money(Math.abs(r.amount))}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Layered DAG of pipeline stages, laid out by topological waves. */
export function DagGraph({ stages, waves, status, selected, onSelect }: {
  stages: { id: string; label: string; owner: string; deps: string[] }[];
  waves: string[][];
  status?: Record<string, { cls: string; text: string }>;
  selected?: string | null;
  onSelect?: (id: string) => void;
}) {
  const nodeW = 150, nodeH = 46, gapX = 26, gapY = 14;
  const pos: Record<string, { x: number; y: number }> = {};
  const maxRows = Math.max(...waves.map((w) => w.length));
  const H = maxRows * nodeH + (maxRows - 1) * gapY + 20;
  waves.forEach((w, i) => {
    const colH = w.length * nodeH + (w.length - 1) * gapY;
    w.forEach((s, j) => { pos[s] = { x: 10 + i * (nodeW + gapX), y: (H - colH) / 2 + j * (nodeH + gapY) }; });
  });
  const W = 20 + waves.length * (nodeW + gapX) - gapX;
  return (
    <div className="dag">
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role={onSelect ? "group" : "img"}
        aria-label={onSelect ? "Pipeline stage dependency graph; select a stage for details" : "Pipeline stage dependency graph"}>
        <defs>
          <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" />
          </marker>
        </defs>
        {stages.flatMap((s) => s.deps.map((d) => {
          const a = pos[d], b = pos[s.id];
          if (!a || !b) return null;
          const x1 = a.x + nodeW, y1 = a.y + nodeH / 2, x2 = b.x - 2, y2 = b.y + nodeH / 2, mx = (x1 + x2) / 2;
          return <path key={`${d}-${s.id}`} className="edge" d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} markerEnd="url(#arr)" />;
        }))}
        {stages.map((s) => {
          const p = pos[s.id];
          const st = status?.[s.id];
          return (
            <g key={s.id} className={`node ${st?.cls || ''} ${selected === s.id ? 'sel' : ''}`} transform={`translate(${p.x},${p.y})`}
              tabIndex={onSelect ? 0 : -1} role={onSelect ? 'button' : undefined} aria-label={`${s.label}${st ? `: ${st.text}` : ''}`}
              onClick={() => onSelect?.(s.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect?.(s.id); }}
              style={{ cursor: onSelect ? 'pointer' : 'default' }}>
              <rect width={nodeW} height={nodeH} rx={6} />
              <text className="t" x={10} y={19}>{stageLabel(s.id)}</text>
              <text className="s" x={10} y={35}>{st ? st.text : s.owner}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Grouped horizontal bars to compare experiment arms on one metric. */
export function ArmBars({ rows, format = (v: number) => String(v), label }: {
  rows: { arm: string; value: number | null }[]; format?: (v: number) => string; label: string;
}) {
  const vals = rows.map((r) => r.value ?? 0);
  const max = Math.max(1, ...vals);
  return (
    <div className="barlist" aria-label={label} role="list">
      {rows.map((r) => (
        <div className="item" role="listitem" key={r.arm}>
          <span className="small">{r.arm}</span>
          <div className="track" aria-hidden="true"><div className="fill" style={{ width: `${((r.value ?? 0) / max) * 100}%` }} /></div>
          <span className="num small">{r.value === null ? '—' : format(r.value)}</span>
        </div>
      ))}
    </div>
  );
}
