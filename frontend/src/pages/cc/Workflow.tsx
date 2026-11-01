import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { dateTime, DOMAIN_LABELS, humanize, ms, stageLabel } from '../../lib/format';
import { Badge, Button, Card, EmptyState, ErrorState, Field, PageHead, Pager, Skeleton, useToast } from '../../components/ui';
import { DagGraph } from '../../components/charts';

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function Workflow() {
  const toast = useToast();
  const [sp, setSp] = useSearchParams();
  const caseId = sp.get('case') || '';
  const [caseInput, setCaseInput] = useState(caseId);
  const [selStage, setSelStage] = useState<string | null>(null);
  const [domains, setDomains] = useState<string[]>(['bill']);
  const [mode, setMode] = useState('parallel');
  const [policy, setPolicy] = useState('targeted');
  const [busy, setBusy] = useState(false);
  const [auditPage, setAuditPage] = useState(1);
  const graph = useApi<any>(() => api.graph());
  const runs = useApi<any[]>(() => (caseId ? api.caseRuns(caseId) : Promise.resolve([])), [caseId]);
  const audit = useApi<any>(() => api.audit({ page: auditPage, page_size: 25 }), [auditPage]);
  const chain = useApi<any>(() => api.auditVerify(), [auditPage]);

  if (graph.error) return <><PageHead title="Workflow" /><ErrorState message={graph.error} onRetry={graph.reload} /></>;
  if (!graph.data) return <><PageHead title="Workflow" /><Skeleton rows={6} /></>;
  const g = graph.data;
  const latest = runs.data?.[0];
  const status: Record<string, { cls: string; text: string }> = {};
  if (latest) {
    latest.stages.forEach((s: any) => {
      const cls = s.status === 'FAILED' || s.status === 'SKIPPED_DEPENDENCY_FAILED' ? 'failed'
        : s.status === 'REUSED' ? 'reused' : s.stage_status === 'BLOCKED' ? 'blocked' : s.stage_status === 'WARN' ? 'warn' : 'ok';
      status[s.stage] = { cls, text: s.status === 'REUSED' ? 'reused (unchanged)' : `${humanize(s.status)} · ${ms(s.duration_ms)}` };
    });
  }
  const spec = g.stages.find((s: any) => s.id === selStage);
  const preview = Array.from(new Set(domains.flatMap((d) => g.rerun_plans[d] || [])));

  const rerun = async () => {
    setBusy(true);
    try {
      const r = await api.rerunCase(caseId, { domains, execution_mode: mode, rerun_policy: policy });
      toast(`Run #${r.run.id}: ${r.run.stages_executed} executed, ${r.run.stages_reused} reused → ${humanize(r.case.status)}`);
      runs.reload(); audit.reload();
    } catch (e) { toast((e as Error).message, 'error'); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHead title="Workflow" sub="Stage dependencies, per-case execution, targeted reruns and the audit trail." />
      <div className="stack">
        <Card title="Pipeline dependency graph" meta="stages in the same column run concurrently in parallel mode">
          <DagGraph stages={g.stages} waves={g.waves} status={latest ? status : undefined} selected={selStage} onSelect={setSelStage} />
          {latest && <div className="legend"><span><i className="lg-ok" />passed</span><span><i className="lg-warn" />needs review</span>
            <span><i className="lg-blocked" />blocking finding</span><span><i className="lg-reused" />reused (dashed)</span></div>}
          {spec && (
            <div className="callout small" style={{ marginTop: 10 }}>
              <strong>{stageLabel(spec.id)}</strong> · owner: {spec.owner} · depends on: {spec.deps.map(stageLabel).join(', ') || 'nothing'} ·
              reads: {spec.domains.map((d: string) => DOMAIN_LABELS[d] || d).join(', ') || 'upstream results only'} ·
              consumes: {Object.keys(spec.consumes).map((k) => stageLabel(k.split('#')[0])).join(', ') || '—'}
            </div>
          )}
        </Card>

        <div className="grid g-main-rail">
          <Card title="Case execution" meta={caseId ? `case ${caseId}` : 'choose a case'}>
            <form className="row" onSubmit={(e) => { e.preventDefault(); setSp(caseInput ? { case: caseInput } : {}); }}>
              <label htmlFor="wf-case" className="small muted">Case id</label>
              <input id="wf-case" className="input" style={{ width: 120 }} value={caseInput} onChange={(e) => setCaseInput(e.target.value.replace(/\D/g, ''))} inputMode="numeric" />
              <Button type="submit">Show</Button>
              {caseId && <Link to={`/claims/${caseId}`} className="small">Open investigation →</Link>}
            </form>
            <div className="divider" />
            {!caseId ? <EmptyState title="Pick a case to colour the graph with its latest run" />
              : runs.error ? <ErrorState message={runs.error} />
              : !runs.data ? <Skeleton /> : runs.data.length === 0 ? <EmptyState title="No runs yet for this case" /> : (
                <div className="table-wrap"><table className="tbl">
                  <thead><tr><th>Run</th><th>Trigger</th><th>Mode</th><th>Changed</th><th className="num">Executed</th><th className="num">Reused</th><th className="num">Latency</th><th>Outcome</th></tr></thead>
                  <tbody>{runs.data.map((r: any) => (
                    <tr key={r.id}><td>#{r.id}<div className="small faint">{dateTime(r.started_at)}</div></td><td className="small">{humanize(r.trigger)}</td>
                      <td className="small">{r.execution_mode} / {r.rerun_policy}</td><td className="small">{(r.changed_domains || []).map((x: string) => DOMAIN_LABELS[x] || x).join(', ') || '—'}</td>
                      <td className="num">{r.stages_executed}</td><td className="num">{r.stages_reused}</td><td className="num">{ms(r.wall_ms)}</td>
                      <td><Badge value={r.outcome_status || r.status} /></td></tr>))}</tbody>
                </table></div>)}
          </Card>

          <Card title="Retry / targeted rerun">
            <div className="stack" style={{ gap: 10 }}>
              <div className="small muted">Treat these inputs as changed. Stages that read them, and their dependents, are re-checked; any stage whose inputs hash identically is reused.</div>
              <fieldset style={{ border: 'none', padding: 0, margin: 0 }}>
                <legend className="small" style={{ fontWeight: 600, marginBottom: 4 }}>Changed inputs</legend>
                <div className="row">{Object.keys(g.rerun_plans).map((d) => (
                  <button type="button" key={d} className="chip" aria-pressed={domains.includes(d)}
                    onClick={() => setDomains((x) => (x.includes(d) ? x.filter((y) => y !== d) : [...x, d]))}>{DOMAIN_LABELS[d] || d}</button>))}</div>
              </fieldset>
              <div className="small">Will re-check: {preview.length ? preview.map(stageLabel).join(' → ') : '—'}</div>
              <div className="grid g-2">
                <Field label="Execution" htmlFor="wf-mode"><select id="wf-mode" className="select" value={mode} onChange={(e) => setMode(e.target.value)}><option value="parallel">Parallel</option><option value="sequential">Sequential</option></select></Field>
                <Field label="Rebuild policy" htmlFor="wf-pol"><select id="wf-pol" className="select" value={policy} onChange={(e) => setPolicy(e.target.value)}><option value="targeted">Targeted</option><option value="full">Full (baseline)</option></select></Field>
              </div>
              <Button variant="primary" busy={busy} disabled={!caseId || domains.length === 0} onClick={rerun}>Run</Button>
            </div>
          </Card>
        </div>

        <div id="audit"><Card title="Audit trail" flush meta={chain.data ? (chain.data.ok
          ? `hash chain verified · ${chain.data.checked} events${chain.data.unchained_legacy_events ? ` (+${chain.data.unchained_legacy_events} pre-chain)` : ''}`
          : `hash chain BROKEN at event ${chain.data.first_broken_event_id}`) : 'append-only'}>
          {audit.error ? <div style={{ padding: 14 }}><ErrorState message={audit.error} /></div> : !audit.data ? <div style={{ padding: 14 }}><Skeleton /></div> : (
            <>
              <div className="table-wrap"><table className="tbl">
                <thead><tr><th>Time</th><th>Event</th><th>Summary</th><th>Case</th><th>Actor</th></tr></thead>
                <tbody>{audit.data.items.map((e: any) => (
                  <tr key={e.id}><td className="small faint" style={{ whiteSpace: 'nowrap' }}>{dateTime(e.ts)}</td><td><span className="badge b-outline">{humanize(e.event_type)}</span></td>
                    <td className="small">{e.summary}</td><td className="small">{e.case_id ? <Link to={`/claims/${e.case_id}`}>{e.case_id}</Link> : '—'}</td><td className="small faint">{e.actor}</td></tr>))}</tbody>
              </table></div>
              <Pager page={auditPage} pageSize={25} total={audit.data.total} onPage={setAuditPage} />
            </>)}
        </Card></div>
      </div>
    </>
  );
}
