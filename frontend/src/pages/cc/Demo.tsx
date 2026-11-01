import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../../lib/api';
import { DOMAIN_LABELS, humanize, money, ms, stageLabel, STAGE_LABELS } from '../../lib/format';
import { caseState } from '../../lib/caseState';
import { changedFields } from '../../lib/auditText';
import { Badge, Button, Card, EmptyState, ErrorState, Field, PageHead, Skeleton, StepHead } from '../../components/ui';
import { useMeta } from '../../components/AppShell';
import AuditTimeline from '../../components/AuditTimeline';
import { draftFor } from './CorrectionDialog';

/* eslint-disable @typescript-eslint/no-explicit-any */
// Issue types whose correction is fully determined by the case's own evidence, so a first-time
// evaluator can apply it without domain knowledge. Tried in this order.
const DEMO_CODES = ['DUPLICATE_BILL_ITEM', 'BILL_ARITHMETIC_ERROR', 'READINESS_ITEM_INCOMPLETE', 'BILLED_PROCEDURE_NOT_DOCUMENTED', 'RECONCILIATION_RESIDUAL'];
const ALL_STAGES = Object.keys(STAGE_LABELS);
const STEPS = ['Meet the case', 'Predict the re-run', 'Apply the correction', 'Watch the re-run', 'Before and after', 'Audit trail'];

function isDemoReady(d: any) {
  const roots = d?.root_blocker?.roots || [];
  const open = (d?.tasks || []).filter((t: any) => t.status === 'OPEN');
  return d?.case?.status === 'BLOCKED' && roots.length === 1 && DEMO_CODES.includes(roots[0].code)
    && roots[0].correctable && !!draftFor(roots[0], d) && open.length === 1;
}

async function findDemoCase(skip: Set<number>): Promise<any | null> {
  for (const code of DEMO_CODES) {
    const list = await api.cases({ status: 'BLOCKED', root: code, sort: 'case_ref', order: 'asc', page_size: 25 });
    for (const row of list.items) {
      if (skip.has(row.id) || row.open_tasks !== 1) continue;
      const d = await api.caseDetail(row.id);
      if (isDemoReady(d)) return d;
    }
  }
  return null;
}

function describeFix(root: any, draft: any, d: any): string {
  const ev = root.evidence || {};
  const line = (d.billing?.line_results || []).find((l: any) => l.line_no === ev.line_no);
  switch (draft.op) {
    case 'delete_bill_line': return `Remove bill line ${ev.line_no}${line ? ` (${line.item_code}, ${money(line.amount)})` : ''}${ev.duplicate_of ? `, which repeats line ${ev.duplicate_of}` : ''}.`;
    case 'update_bill_line': return `Correct bill line ${ev.line_no} to the value the evidence shows.`;
    case 'complete_checklist_item': return `Mark the discharge checklist item “${humanize(ev.item)}” as complete.`;
    case 'set_bill_header_total': return 'Set the bill header total to the sum of the bill lines.';
    default: return humanize(draft.op);
  }
}

export default function Demo() {
  const [sp, setSp] = useSearchParams();
  const { refreshCounts } = useMeta();
  const pinned = Number(sp.get('case') || 0) || null;
  const [before, setBefore] = useState<any>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [none, setNone] = useState(false);
  const [step, setStep] = useState(0);
  const [skip] = useState(() => new Set<number>());
  const [payload, setPayload] = useState<any>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ msg: string; status: number } | null>(null);
  const [res, setRes] = useState<any>(null);
  const [after, setAfter] = useState<any>(null);
  const [events, setEvents] = useState<any[] | null>(null);
  const [shown, setShown] = useState(0);

  const load = useCallback(async () => {
    setLoadErr(null); setNone(false); setBefore(null); setRes(null); setAfter(null); setErr(null); setStep(0); setEvents(null);
    try {
      let d: any = null;
      if (pinned) {
        d = await api.caseDetail(pinned);
        if (!isDemoReady(d) && !(d.corrections || []).length) d = null;
      }
      if (!d) d = await findDemoCase(skip);
      if (!d) { setNone(true); return; }
      if (!isDemoReady(d)) {            // already corrected earlier: show its recorded outcome
        setBefore(d); setAfter(d); setStep(4);
        api.caseEvents(d.case.id).then(setEvents).catch(() => setEvents([]));
        return;
      }
      setBefore(d);
      const draft = draftFor(d.root_blocker.roots[0], d);
      setPayload(draft?.payload);
      setReason(`Guided demo: ${d.root_blocker.roots[0].title.toLowerCase()} confirmed from the evidence`);
      if (!pinned || pinned !== d.case.id) setSp({ case: String(d.case.id) }, { replace: true });
    } catch (e) { setLoadErr((e as Error).message); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinned]);
  useEffect(() => { load(); }, [load]);

  const root = before?.root_blocker?.roots?.[0];
  const draft = useMemo(() => (root && before ? draftFor(root, before) : null), [root, before]);

  // replay the recorded run one stage at a time (instant when the user prefers reduced motion)
  const runStages: any[] = res?.run?.stages || [];
  useEffect(() => {
    if (!res) return undefined;
    const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (reduce) { setShown(ALL_STAGES.length); return undefined; }
    setShown(0);
    const id = window.setInterval(() => setShown((k) => (k >= ALL_STAGES.length ? (window.clearInterval(id), k) : k + 1)), 220);
    return () => window.clearInterval(id);
  }, [res]);

  const apply = async () => {
    setErr(null);
    if (reason.trim().length < 3) { setErr({ msg: 'Please write a reason (at least 3 characters). It is stored in the audit trail.', status: 0 }); return; }
    setBusy(true); setStep(3);
    try {
      const task = before.tasks.find((t: any) => t.status === 'OPEN' && t.code === root.code);
      const r = await api.correct(before.case.id, { op: draft!.op, payload, reason: reason.trim(), review_task_id: task?.id, expected_version: before.case.version });
      setRes(r);
      const [fresh, ev] = await Promise.all([api.caseDetail(before.case.id), api.caseEvents(before.case.id)]);
      setAfter(fresh); setEvents(ev);
      refreshCounts();
    } catch (e) {
      setErr({ msg: (e as Error).message, status: (e as ApiError).status ?? 0 });
      setStep(2);
    } finally { setBusy(false); }
  };

  const anotherCase = () => { if (before) skip.add(before.case.id); setSp({}, { replace: true }); if (!pinned) load(); };

  return (
    <>
      <PageHead title="Guided demo" sub="Fix one real (synthetic) blocked claim step by step and see exactly what the system re-runs." />
      <div className="callout small" style={{ marginBottom: 16 }}>
        This demo uses a <strong>synthetic case from the loaded demo dataset</strong> and applies a <strong>real correction</strong> through the
        ClearClaim API, so it changes this database (reset any time with <span className="mono">python scripts/setup_demo.py --reset</span>).
        Statuses are internal preparation states, not insurer decisions.
      </div>

      <ol className="stepper" aria-label="Demo steps">
        {STEPS.map((s, i) => <li key={s} className={i === step ? 'current' : i < step ? 'done' : ''} aria-current={i === step ? 'step' : undefined}><span>{i + 1}</span>{s}</li>)}
      </ol>

      {loadErr ? <ErrorState message={loadErr} onRetry={load} />
        : none ? (
          <Card><EmptyState title="No suitable demo case is left">
            Every blocked synthetic case with a single, evidence-determined fix has already been corrected. Reset the demo data with
            <span className="mono"> python scripts/setup_demo.py --reset</span>, then reload this page. You can still explore the <Link to="/claims">Cases</Link>.
          </EmptyState></Card>)
        : !before ? <Card><Skeleton rows={5} /><div className="small muted" style={{ marginTop: 8 }}>Finding a blocked synthetic case with one correctable issue…</div></Card>
        : (
          <div className="stack demo">
            {/* ---- 1. meet the case ---- */}
            {step >= 0 && root && (
              <Card>
                <StepHead n={1} title="Meet the case" sub={<>Case <Link to={`/claims/${before.case.id}`}>{before.case.case_ref}</Link> · encounter {before.encounter.hadm_id} · {before.encounter.data_source}</>} />
                <div className="stack" style={{ gap: 10, marginTop: 12 }}>
                  <div className="row"><Badge value={before.case.status} /><span className="small">{caseState(before).sentence}</span></div>
                  <div><div className="small muted">What is wrong?</div><div className="blocker-title">{root.title}</div><p className="plain" style={{ margin: '4px 0 0' }}>{root.message}</p></div>
                  {(root.expected != null || root.actual != null) && (
                    <div className="compare"><div><div className="k">Expected</div><div>{root.expected ?? '—'}</div></div><div><div className="k">Recorded</div><div>{root.actual ?? '—'}</div></div></div>)}
                  <div className="small">Found by <strong>{stageLabel(root.stage)}</strong>; the fix belongs to <strong>{root.owner_team}</strong>.</div>
                  {step === 0 && <div className="row"><Button variant="primary" onClick={() => setStep(1)}>Next: what would a fix re-run?</Button>
                    <Button variant="ghost" size="sm" onClick={anotherCase}>Use a different case</Button></div>}
                </div>
              </Card>
            )}

            {/* ---- 2. predict ---- */}
            {step >= 1 && root && (
              <Card>
                <StepHead n={2} title="Predict the re-run" sub="Read from the workflow dependency graph before anything changes." />
                <ol className="stage-strip" style={{ marginTop: 12 }} aria-label="Planned re-run">
                  {ALL_STAGES.map((s) => <li key={s} className={root.rerun_stages.includes(s) ? 'rechk' : 'skip'}><span>{stageLabel(s)}</span><span className="small">{root.rerun_stages.includes(s) ? 'will be re-checked' : 'not touched'}</span></li>)}
                </ol>
                <p className="small" style={{ marginBottom: 0 }}>
                  The fix changes the <strong>{(DOMAIN_LABELS[root.owner_domain] || root.owner_domain).toLowerCase()}</strong>, so {root.rerun_stages.length} of {ALL_STAGES.length} stages
                  are re-checked. A full rerun would execute all {ALL_STAGES.length}. Re-checked stages whose inputs turn out identical are reused, not executed.
                </p>
                {step === 1 && <div className="row" style={{ marginTop: 10 }}><Button variant="primary" onClick={() => setStep(2)}>Next: apply the correction</Button></div>}
              </Card>
            )}

            {/* ---- 3. apply ---- */}
            {step >= 2 && root && draft && !res && (
              <Card>
                <StepHead n={3} title="Apply the correction" sub="This is the same correction an authorised user would make on the case page." />
                <form className="stack" style={{ gap: 10, marginTop: 12 }} noValidate onSubmit={(e) => { e.preventDefault(); apply(); }}>
                  <div className="evidence small"><strong>Proposed fix:</strong> {describeFix(root, draft, before)} <span className="faint">(operation <span className="mono">{draft.op}</span>)</span></div>
                  {draft.fields.map((fl: any) => {
                    const key = fl.key.split('.');
                    const val = key.reduce((a: any, k: string) => (a == null ? a : a[k]), payload);
                    return (
                      <Field key={fl.key} label={fl.label} help={fl.help} htmlFor={`demo-${fl.key}`}>
                        <input id={`demo-${fl.key}`} className="input" type={fl.type} value={val ?? ''}
                          onChange={(e) => {
                            const copy = JSON.parse(JSON.stringify(payload));
                            let cur = copy; key.slice(0, -1).forEach((k: string) => { cur = cur[k]; });
                            cur[key[key.length - 1]] = fl.type === 'number' ? Number(e.target.value) : e.target.value;
                            setPayload(copy);
                          }} />
                      </Field>);
                  })}
                  <Field label="Reason (recorded in the audit trail)" htmlFor="demo-reason">
                    <textarea id="demo-reason" className="textarea" value={reason} maxLength={500} onChange={(e) => { setReason(e.target.value); setErr(null); }}
                      aria-invalid={!!err && reason.trim().length < 3} />
                  </Field>
                  {err && (
                    <div className="error-state" role="alert">
                      <strong>The correction was not applied.</strong> {err.msg}
                      {err.status === 409 && <div style={{ marginTop: 6 }}><Button size="sm" type="button" onClick={load}>Reload the case</Button></div>}
                    </div>)}
                  <div className="row"><Button variant="primary" type="submit" busy={busy}>Apply correction and re-run</Button></div>
                </form>
              </Card>
            )}

            {res && root && draft && (
              <Card>
                <StepHead n={3} title="Apply the correction" sub="Done." />
                <div className="small" style={{ marginTop: 10 }}>
                  <strong>Applied:</strong> {describeFix(root, draft, before)} Reason recorded: “{reason.trim()}”.
                </div>
              </Card>
            )}

            {/* ---- 4. progress ---- */}
            {step >= 3 && (busy || res) && root && (
              <Card>
                <StepHead n={4} title="Watch the re-run" sub={res ? `Replaying recorded run #${res.run.id}: actual statuses and timings from the server.` : 'Waiting for the server to apply the correction and re-run…'} />
                <ol className="stage-strip progress" style={{ marginTop: 12 }} aria-live="polite" aria-label="Re-run progress">
                  {ALL_STAGES.map((s, i) => {
                    const r = runStages.find((x: any) => x.stage === s);
                    const revealed = res && i < shown;
                    const cls = !res ? (root.rerun_stages.includes(s) ? 'pending' : 'skip')
                      : !revealed ? 'pending' : !r ? 'skip' : r.status === 'SUCCEEDED' ? 'exec' : r.status === 'REUSED' ? 'reuse' : 'fail';
                    const label = !res ? (root.rerun_stages.includes(s) ? 'queued…' : 'not touched')
                      : !revealed ? '…' : !r ? 'not touched' : r.status === 'SUCCEEDED' ? `re-executed · ${ms(r.duration_ms)}` : r.status === 'REUSED' ? 'reused (input unchanged)' : humanize(r.status);
                    return <li key={s} className={cls}><span>{stageLabel(s)}</span><span className="small">{label}</span></li>;
                  })}
                </ol>
                {res && shown >= ALL_STAGES.length && (
                  <div className="small" style={{ marginTop: 10 }}>
                    <strong>{res.run.stages_executed}</strong> stage(s) re-executed, <strong>{res.run.stages_reused}</strong> reused, {ALL_STAGES.length - runStages.length} not touched · {ms(res.run.wall_ms)} software time.
                    {step === 3 && <div className="row" style={{ marginTop: 10 }}><Button variant="primary" onClick={() => setStep(4)}>Next: before and after</Button></div>}
                  </div>)}
              </Card>
            )}

            {/* ---- 5. before / after ---- */}
            {step >= 4 && after && <BeforeAfter before={res ? before : null} after={after} res={res} onNext={step === 4 ? () => setStep(5) : undefined} />}

            {/* ---- 6. audit ---- */}
            {step >= 5 && (
              <Card>
                <StepHead n={6} title="Audit trail" sub="What was recorded for this case, in plain language." />
                <div style={{ marginTop: 12 }}>{events ? <AuditTimeline events={events} keyOnly /> : <Skeleton rows={3} />}</div>
                <div className="row" style={{ marginTop: 12 }}>
                  <Link className="btn" to={`/claims/${after.case.id}`}>Open the full case</Link>
                  <Link className="btn" to={`/audit?case=${after.case.id}`}>Open in the audit trail</Link>
                  <Button variant="ghost" onClick={anotherCase}>Try another case</Button>
                </div>
              </Card>
            )}
          </div>
        )}
    </>
  );
}

function BeforeAfter({ before, after, res, onNext }: { before: any | null; after: any; res: any; onNext?: () => void }) {
  const sa = caseState(after);
  const corr = res?.correction || after.corrections?.[after.corrections.length - 1];
  const run = res?.run;
  const rows: [string, (d: any) => ReactNode][] = [
    ['Case status', (d) => <Badge value={d.case.status} />],
    ['Root blockers', (d) => { const r = d.root_blocker?.roots || []; return r.length ? r.map((x: any) => x.title).join(', ') : 'none'; }],
    ['Open review tasks', (d) => (d.tasks || []).filter((t: any) => t.status === 'OPEN').length],
    ['Recorded bill total', (d) => money(d.billing?.lines_total)],
    ['Disputed amount', (d) => money(d.billing?.disputed_amount)],
    ['Estimated eligible (rule-based)', (d) => money(d.case.estimated_eligible)],
  ];
  const changes = corr ? changedFields(corr.before, corr.after) || [] : [];
  return (
    <Card>
      <StepHead n={5} title="Before and after" sub={before ? 'Live values fetched from the API before and after the correction.' : 'This case was corrected earlier; showing its current state and recorded correction.'} />
      <div className="stack" style={{ gap: 12, marginTop: 12 }}>
        <div className="table-wrap"><table className="tbl before-after">
          <caption className="visually-hidden">Case before and after the correction</caption>
          <thead><tr><th>What</th>{before && <th>Before</th>}<th>{before ? 'After' : 'Now'}</th></tr></thead>
          <tbody>{rows.map(([label, f]) => <tr key={label}><td>{label}</td>{before && <td>{f(before)}</td>}<td><strong>{f(after)}</strong></td></tr>)}</tbody>
        </table></div>
        {changes.length > 0 && (
          <div><div className="small muted" style={{ marginBottom: 4 }}>Corrected data</div>
            <table className="tbl before-after"><caption className="visually-hidden">Corrected values</caption>
              <thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
              <tbody>{changes.map((c) => <tr key={c.field}><td>{c.field}</td><td>{c.before}</td><td><strong>{c.after}</strong></td></tr>)}</tbody></table></div>)}
        {!run && corr && <div className="small muted">Recorded run for that correction: see “Result of the last correction” on the <Link to={`/claims/${after.case.id}`}>case page</Link>.</div>}
        {run && (run.status !== 'SUCCEEDED') && <ErrorState message={`The re-run finished with status ${run.status}${run.error ? `: ${run.error}` : ''}. The case is not treated as fixed.`} />}
        <div className={`callout ${sa.tone}`} role="status">
          {sa.complete
            ? <><strong>Claim preparation complete.</strong> No root blockers and no open review tasks remain. This is an internal preparation status, not an insurer decision.</>
            : <><strong>Not complete yet: {sa.headline.toLowerCase()}.</strong> {sa.sentence} {sa.blockers.length > 0 && <>Remaining: {sa.blockers.map((b: any) => b.title).join(', ')}.</>} <Link to={`/claims/${after.case.id}`}>Continue on the case page →</Link></>}
        </div>
        {onNext && <div className="row"><Button variant="primary" onClick={onNext}>Next: see the audit trail</Button></div>}
        {!before && corr && <CorrectionHint corr={corr} />}
      </div>
    </Card>
  );
}

function CorrectionHint({ corr }: { corr: any }) {
  return <div className="small faint">Last correction: {humanize(corr.op)} on {DOMAIN_LABELS[corr.domain] || corr.domain}{corr.reason ? ` · “${corr.reason}”` : ''}.</div>;
}

