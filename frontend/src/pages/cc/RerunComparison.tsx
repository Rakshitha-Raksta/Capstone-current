import { n, pct } from '../../lib/format';

/* eslint-disable @typescript-eslint/no-explicit-any */
const B = 'B_sequential_full';
const C = 'C_parallel_targeted';

/** Mean over seeds of BLOCKED / NEEDS_REVIEW cases left after the correction loop, from the stored per-seed record. */
function remaining(summary: any, arm: string) {
  const seeds = Object.values(summary?.per_seed || {}) as any[];
  const vals = seeds.map((s) => s?.arms?.[arm]?.final_status).filter(Boolean);
  if (!vals.length) return null;
  const mean = (k: string) => vals.reduce((a, v) => a + (v[k] || 0), 0) / vals.length;
  return { blocked: mean('BLOCKED'), review: mean('NEEDS_REVIEW'), cases: seeds.map((s) => s?.arms?.[arm]?.n_cases).find(Boolean) };
}

/** Plain-language comparison of a full rerun (baseline) and a targeted rerun, from one stored experiment record. */
export default function RerunComparison({ summary, label }: { summary: any; label: string }) {
  const agg = summary?.aggregate || {};
  const m = (arm: string, k: string) => agg?.[arm]?.[k]?.mean as number | undefined;
  const rows: { name: string; help: string; key?: string; b?: number; c?: number; fmt: (v: number) => string; lowerBetter?: boolean }[] = [
    { name: 'Stages executed while fixing', key: 'loop_stage_executions', fmt: (v) => n(v, 1), lowerBetter: true,
      help: 'Workflow stages that actually ran during the correction loop, after every injected fault was corrected or decided.' },
    { name: 'Stages reused', key: 'loop_stage_reused', fmt: (v) => n(v, 1),
      help: 'Stages that were re-checked but skipped, because their inputs were identical (compared by hash).' },
    { name: 'Unnecessary re-executions', key: 'loop_unnecessary_executions', fmt: (v) => n(v, 1), lowerBetter: true,
      help: 'Executions that produced exactly the same output as before: work that did not need to happen.' },
    { name: 'Human review actions', key: 'review_actions', fmt: (v) => n(v, 1),
      help: 'Corrections and decisions made by the simulated reviewer. The same in both arms.' },
  ];
  rows.forEach((r) => { r.b = m(B, r.key!); r.c = m(C, r.key!); });
  const rb = remaining(summary, B), rc = remaining(summary, C);
  const seeds = summary?.config?.seeds?.length;
  const synthetic = summary?.config?.clinical_source === 'SYNTHETIC';
  return (
    <section className="card" aria-labelledby="cmp-title">
      <div className="card-head"><h2 id="cmp-title">Full rerun vs targeted rerun</h2><span className="meta">{label} · mean per seed over {seeds} seed(s)</span></div>
      <div className="card-body stack" style={{ gap: 12 }}>
        <p className="small" style={{ margin: 0 }}>
          Both arms start from the same {synthetic ? 'synthetic' : 'MIMIC-demo (real encounters, synthetic billing and insurance)'} cases and apply the same corrections.
          The <strong>full rerun</strong> re-executes all 11 stages after each correction. The <strong>targeted rerun</strong> re-checks only the stages that read the changed data,
          and reuses those whose inputs did not change.
        </p>
        <div className="table-wrap"><table className="tbl cmp">
          <caption className="visually-hidden">Full rerun compared with targeted rerun</caption>
          <thead><tr><th>Measure</th><th className="num">Full rerun</th><th className="num">Targeted rerun</th><th className="num">Difference</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td><strong>{r.name}</strong><div className="small muted">{r.help}</div></td>
                <td className="num">{r.b == null ? '—' : r.fmt(r.b)}</td>
                <td className="num"><strong>{r.c == null ? '—' : r.fmt(r.c)}</strong></td>
                <td className="num small">{r.lowerBetter && r.b ? `${pct(1 - (r.c || 0) / r.b)} fewer` : r.b === r.c ? 'same' : '—'}</td>
              </tr>))}
            <tr>
              <td><strong>Cases still not complete afterwards</strong><div className="small muted">Blocked or needing review after the correction loop, mostly confirmed policy blockers (for example a waiting period) that no data correction can fix.</div></td>
              <td className="num">{rb ? `${n(rb.blocked, 1)} blocked · ${n(rb.review, 1)} review` : '—'}</td>
              <td className="num"><strong>{rc ? `${n(rc.blocked, 1)} blocked · ${n(rc.review, 1)} review` : '—'}</strong></td>
              <td className="num small">{rb && rc ? (rb.blocked === rc.blocked && rb.review === rc.review ? 'same' : 'differs') : '—'}</td>
            </tr>
          </tbody>
        </table></div>
        <div className="small faint">
          All values come from the stored experiment record; the difference column is computed from them. Detection results are identical across arms because both run the same checks.
          This is a controlled benchmark: the faults and the detectors were written by the same team, and it is not a hospital measurement.
        </div>
      </div>
    </section>
  );
}
