import { useCallback, useState } from 'react';
import { api } from '../../lib/api';
import { useApi, usePoll } from '../../lib/hooks';
import { dateTime, humanize, ms, n, pct } from '../../lib/format';
import { Badge, Button, Card, EmptyState, ErrorState, Field, PageHead, Skeleton, useToast } from '../../components/ui';
import { ArmBars } from '../../components/charts';
import RerunComparison from './RerunComparison';

const RECORDED = [
  { name: 'main', label: 'Synthetic, development seeds 1–3' },
  { name: 'main_heldout', label: 'Synthetic, held-out seeds 4–6' },
  { name: 'mimic_demo', label: 'MIMIC demo encounters, seeds 1–3' },
  { name: 'mimic_demo_heldout', label: 'MIMIC demo encounters, held-out seeds 4–6' },
];

/* eslint-disable @typescript-eslint/no-explicit-any */
const ARMS = ['B_sequential_full', 'C_parallel_targeted', 'C_minus_targeted', 'C_minus_root_routing'];
const ARM_LABEL: Record<string, string> = {
  B_sequential_full: 'B · sequential, full rerun', C_parallel_targeted: 'C · parallel, targeted (treatment)',
  C_minus_targeted: 'C − targeted rerun', C_minus_root_routing: 'C − root routing',
};
const ROWS: [string, string, (v: number) => string][] = [
  ['detection_all_findings.recall', 'Issue detection recall', (v) => pct(v)],
  ['detection_all_findings.precision', 'Precision (all findings)', (v) => pct(v)],
  ['detection_root_findings.precision', 'Precision (root findings)', (v) => pct(v)],
  ['detection_all_findings.f1', 'F1', (v) => n(v, 3)],
  ['root_blocker_exact_match.accuracy', 'Root-blocker exact match', (v) => pct(v)],
  ['first_pass_yield', 'First-pass preparation yield', (v) => pct(v)],
  ['review_rate', 'Review rate', (v) => pct(v)],
  ['first_pass_tasks', 'Review tasks created (first pass)', (v) => n(v, 1)],
  ['review_actions', 'Human review actions', (v) => n(v, 1)],
  ['first_pass_stage_executions', 'Stage executions, first pass', (v) => n(v, 1)],
  ['loop_stage_executions', 'Stage executions, correction loop', (v) => n(v, 1)],
  ['loop_unnecessary_executions', 'Unnecessary re-executions', (v) => n(v, 1)],
  ['loop_stage_reused', 'Stages reused (hash-verified)', (v) => n(v, 1)],
  ['false_clear_rate', 'False-clear rate', (v) => pct(v)],
  ['first_pass_latency_ms.median', 'Median case latency (software)', (v) => ms(v)],
  ['first_pass_latency_ms.p95', 'P95 case latency (software)', (v) => ms(v)],
  ['rerun_latency_ms.median', 'Median rerun latency', (v) => ms(v)],
  ['modelled_human_minutes_per_case', 'Modelled human minutes / case (assumed)', (v) => n(v, 1)],
];

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

export default function Experiments() {
  const toast = useToast();
  const list = useApi<any[]>(() => api.experiments());
  const [selId, setSelId] = useState<number | null>(null);
  const current = selId ?? list.data?.find((e) => e.status === 'COMPLETED')?.id ?? null;
  const det = useApi<any>(() => (current ? api.experiment(current) : Promise.resolve(null)), [current]);
  const running = (list.data || []).some((e) => e.status === 'RUNNING');
  usePoll(useCallback(() => list.reload(), [list]), running, 4000);
  const [form, setForm] = useState({ seeds: '1', n_encounters: 60, error_rate: 0.35 });
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    try {
      const seeds = form.seeds.split(/[ ,]+/).filter(Boolean).map(Number);
      const r = await api.startExperiment({ name: 'ui', seeds, n_encounters: Number(form.n_encounters), error_rate: Number(form.error_rate) });
      toast(`Experiment #${r.id} started. It runs on isolated databases; live cases are not touched.`);
      list.reload();
    } catch (e) { toast((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  const [recorded, setRecorded] = useState('main');
  const loadRecorded = async () => {
    const path = `results/experiments/${recorded}`;
    const existing = (list.data || []).find((e) => e.raw_results_path === path && e.has_summary);
    if (existing) { setSelId(existing.id); return; }
    try { const r = await api.importSummary(`${path}/summary.json`); toast(`Loaded recorded run “${recorded}” as experiment #${r.id}.`); setSelId(r.id); list.reload(); }
    catch (e) { toast((e as Error).message, 'error'); }
  };

  const agg = det.data?.summary?.aggregate;
  const exportCsv = () => {
    const lines = [['metric', ...ARMS.map((a) => `${a}_mean`), ...ARMS.map((a) => `${a}_sd`)].join(',')];
    ROWS.forEach(([k, label]) => lines.push([JSON.stringify(label), ...ARMS.map((a) => agg?.[a]?.[k]?.mean ?? ''), ...ARMS.map((a) => agg?.[a]?.[k]?.sd ?? '')].join(',')));
    download(`clearclaim-experiment-${current}.csv`, lines.join('\n'), 'text/csv');
  };

  return (
    <>
      <PageHead title="Experiments" sub="Does targeted re-running save work? Controlled comparisons with known injected faults. Every number comes from a stored experiment record." />
      <div className="stack">
        <div className="grid g-main-rail">
          <Card title="Experiment records" flush actions={<form className="row" onSubmit={(e) => { e.preventDefault(); loadRecorded(); }}>
            <label htmlFor="rec-run" className="small">Recorded run</label>
            <select id="rec-run" className="select" value={recorded} onChange={(e) => setRecorded(e.target.value)}>
              {RECORDED.map((r) => <option key={r.name} value={r.name}>{r.label}</option>)}
            </select>
            <Button size="sm" type="submit">Load</Button></form>}>
            {list.error ? <div style={{ padding: 14 }}><ErrorState message={list.error} /></div> : !list.data ? <div style={{ padding: 14 }}><Skeleton /></div>
              : list.data.length === 0 ? <EmptyState title="No experiments yet">Start one, or run <span className="mono">python scripts/run_experiments.py</span> and import its results.</EmptyState> : (
                <div className="table-wrap"><table className="tbl">
                  <thead><tr><th>#</th><th>Name</th><th>Config</th><th>Status</th><th>Started</th><th /></tr></thead>
                  <tbody>{list.data.map((e) => (
                    <tr key={e.id} className={current === e.id ? 'flag' : ''}><td>{e.id}</td><td className="small">{e.name}</td>
                      <td className="small mono">seeds {String(e.config?.seeds)} · n={e.config?.n_encounters}</td>
                      <td><Badge value={e.status} />{e.error && <div className="small" style={{ color: 'var(--blocked)' }}>{e.error}</div>}</td>
                      <td className="small faint">{dateTime(e.started_at)}</td>
                      <td>{e.has_summary && <Button size="sm" onClick={() => setSelId(e.id)}>View</Button>}</td></tr>))}</tbody>
                </table></div>)}
          </Card>
          <Card title="New experiment">
            <div className="stack" style={{ gap: 10 }}>
              <Field label="Seeds" htmlFor="ex-seeds" help="Comma-separated, max 5"><input id="ex-seeds" className="input" value={form.seeds} onChange={(e) => setForm({ ...form, seeds: e.target.value })} /></Field>
              <Field label="Encounters per seed" htmlFor="ex-n" help="10–400"><input id="ex-n" type="number" min={10} max={400} className="input" value={form.n_encounters} onChange={(e) => setForm({ ...form, n_encounters: Number(e.target.value) })} /></Field>
              <Field label="Fault rate" htmlFor="ex-e" help="Share of encounters with injected data faults"><input id="ex-e" type="number" step={0.05} min={0} max={0.9} className="input" value={form.error_rate} onChange={(e) => setForm({ ...form, error_rate: Number(e.target.value) })} /></Field>
              <Button variant="primary" busy={busy} disabled={running} onClick={start}>{running ? 'An experiment is running…' : 'Run all arms'}</Button>
            </div>
          </Card>
        </div>

        {current && (det.error ? <ErrorState message={det.error} /> : !det.data ? <Skeleton rows={6} /> : !agg ? <EmptyState title="No summary for this run" /> : (
          <>
            <RerunComparison summary={det.data.summary} label={`record #${current} · ${det.data.name}`} />
            <Card title={`Experiment #${current}: all metrics, all arms`} meta={`mean (± sd) over ${det.data.summary.config.seeds.length} seed(s) · ${det.data.summary.elapsed_s}s`}
              actions={<><Button size="sm" onClick={exportCsv}>Export CSV</Button>
                <Button size="sm" onClick={() => download(`clearclaim-experiment-${current}.json`, JSON.stringify(det.data.summary, null, 2), 'application/json')}>Export JSON</Button></>} flush>
              <div className="table-wrap"><table className="tbl">
                <thead><tr><th>Metric</th>{ARMS.map((a) => <th key={a} className="num">{ARM_LABEL[a]}</th>)}</tr></thead>
                <tbody>{ROWS.map(([k, label, f]) => (
                  <tr key={k}><td className="small">{label}</td>
                    {ARMS.map((a) => { const m = agg[a]?.[k]; return <td key={a} className="num small">{m?.mean == null ? '—' : f(m.mean)}{m?.sd != null && m.sd > 0 ? <span className="faint"> ±{n(m.sd, 2)}</span> : ''}</td>; })}</tr>))}
                  <tr><td className="small">A · manual process (modelled minutes / case)</td><td colSpan={4} className="small">{n(agg.A_manual_model?.minutes_per_case?.mean, 1)} min — from ASSUMED stage durations, not observed. No detection metrics: no manual process was measured.</td></tr>
                </tbody>
              </table></div>
            </Card>
            <div className="grid g-2">
              <Card title="Stage executions in the correction loop">
                <ArmBars label="Loop stage executions" rows={ARMS.map((a) => ({ arm: ARM_LABEL[a], value: agg[a]?.['loop_stage_executions']?.mean ?? null }))} format={(v) => n(v, 0)} />
                <div className="divider" /><div className="small muted" style={{ marginBottom: 6 }}>…of which reproduced an identical output (unnecessary)</div>
                <ArmBars label="Unnecessary executions" rows={ARMS.map((a) => ({ arm: ARM_LABEL[a], value: agg[a]?.['loop_unnecessary_executions']?.mean ?? null }))} format={(v) => n(v, 0)} />
              </Card>
              <Card title="Review workload">
                <ArmBars label="Review tasks created" rows={ARMS.map((a) => ({ arm: ARM_LABEL[a], value: agg[a]?.['first_pass_tasks']?.mean ?? null }))} format={(v) => n(v, 0)} />
                <div className="divider" /><div className="small muted">Issue coverage by review tasks (recall): {ARMS.map((a) => `${ARM_LABEL[a].split(' ')[0]} ${pct(agg[a]?.['issue_coverage_by_tasks.recall']?.mean)}`).join(' · ')}</div>
              </Card>
            </div>
            <Card title="Detection by issue type" meta="first seed" flush>
              {(() => {
                const s: any = Object.values(det.data.summary.per_seed)[0];
                const by = s?.detection_by_type?.C_parallel_targeted || {};
                return (
                  <div className="table-wrap"><table className="tbl">
                    <thead><tr><th>Issue type</th><th className="num">TP</th><th className="num">FP</th><th className="num">FN</th><th className="num">Precision</th><th className="num">Recall</th></tr></thead>
                    <tbody>{Object.entries(by).map(([k, v]: [string, any]) => (
                      <tr key={k}><td className="small">{humanize(k)}</td><td className="num">{v.tp || 0}</td><td className="num">{v.fp || 0}</td><td className="num">{v.fn || 0}</td>
                        <td className="num">{pct(v.precision)}</td><td className="num">{pct(v.recall)}</td></tr>))}</tbody>
                  </table></div>);
              })()}
            </Card>
            <div className="callout small">Caveat: the fault injector and the detectors were written by the same team, so detection metrics on this synthetic benchmark are an upper bound, not evidence of real-world accuracy. See the Limitations section of README.md.</div>
          </>))}
      </div>
    </>
  );
}
