import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { date, dateTime, DOMAIN_LABELS, humanize, money, ms, n, stageLabel, STAGE_LABELS } from '../../lib/format';
import { caseState } from '../../lib/caseState';
import { changedFields } from '../../lib/auditText';
import { Badge, Button, Card, Dialog, Disclosure, EmptyState, ErrorState, PageHead, Skeleton, useToast } from '../../components/ui';
import { Waterfall } from '../../components/charts';
import { useMeta } from '../../components/AppShell';
import AuditTimeline from '../../components/AuditTimeline';
import CorrectionDialog, { draftFor } from './CorrectionDialog';

/* eslint-disable @typescript-eslint/no-explicit-any */
const WHY: Record<string, string> = {
  BLOCKING: 'It blocks claim preparation: the claim packet cannot be completed until this is corrected (or, for a policy matter, confirmed by a reviewer).',
  REVIEW: 'A person must decide before the claim packet is complete: correct the data, or accept it with a recorded justification.',
};
const ALL_STAGES = Object.keys(STAGE_LABELS);

function Evidence({ f }: { f: any }) {
  const ev = f.evidence || {};
  const extra = Object.entries(ev).filter(([k]) => !['rule_ids'].includes(k));
  return (
    <div className="evidence stack" style={{ gap: 8 }}>
      {(f.expected != null || f.actual != null) && (
        <div className="compare">
          <div><div className="k">Expected</div><div>{f.expected ?? '—'}</div></div>
          <div><div className="k">Recorded</div><div>{f.actual ?? '—'}</div></div>
        </div>
      )}
      {(f.rule_id || f.source_document) && (
        <div className="small">
          Source: {f.rule_id && <>rule <span className="mono">{f.rule_id}</span></>}
          {f.source_document && <> · <Link to={`/policy-source-viewer?document=${encodeURIComponent(f.source_document)}`}>{f.source_document}</Link></>}
          {f.source_page && <> · page {f.source_page}</>}
        </div>
      )}
      {f.amount != null && <div className="small">Amount involved: <strong>{money(f.amount)}</strong></div>}
      {extra.length > 0 && (
        <div className="small muted">{extra.map(([k, v]) => <span key={k} style={{ marginRight: 12 }}>{humanize(k)}: <span className="mono">{Array.isArray(v) ? v.join(', ') : String(v)}</span></span>)}</div>
      )}
    </div>
  );
}

function StageFlow({ stages, hot }: { stages: string[]; hot?: Set<string> }) {
  return (
    <div className="flow">
      {stages.map((s, i) => (
        <span key={s} className="row" style={{ gap: 4 }}>
          {i > 0 && <span className="arrow" aria-hidden="true">→</span>}
          <span className={`stage-chip ${hot?.has(s) ? 'hot' : ''}`}>{stageLabel(s)}</span>
        </span>
      ))}
    </div>
  );
}

/** Result of a correction run: what changed, which stages re-executed, reused or were not touched. */
export function CorrectionResult({ corr, run, statusBefore, statusAfter }: { corr: any; run: any; statusBefore?: string; statusAfter?: string }) {
  const executed = new Set<string>(run.stages.filter((s: any) => s.status === 'SUCCEEDED').map((s: any) => s.stage));
  const reused = run.stages.filter((s: any) => s.status === 'REUSED').map((s: any) => s.stage);
  const failed = run.stages.filter((s: any) => s.status === 'FAILED' || s.status === 'SKIPPED_DEPENDENCY_FAILED');
  const touched = new Set<string>(run.stages.map((s: any) => s.stage));
  const untouched = ALL_STAGES.filter((s) => !touched.has(s));
  const changes = changedFields(corr.before, corr.after) || [];
  return (
    <div className="stack" style={{ gap: 10 }}>
      {(run.status !== 'SUCCEEDED' || failed.length > 0) && (
        <div className="error-state" role="alert"><strong>The re-run did not succeed.</strong> {run.error || `${failed.length} stage(s) failed: ${failed.map((s: any) => stageLabel(s.stage)).join(', ')}.`} The case is not treated as fixed.</div>
      )}
      <div className="small">
        {humanize(corr.op)} on the <strong>{(DOMAIN_LABELS[corr.domain] || corr.domain).toLowerCase()}</strong>
        {statusBefore && statusAfter && <> · status {humanize(statusBefore)} → <Badge value={statusAfter} /></>}
        {' '}· run #{run.id}, {ms(run.wall_ms)} software time
      </div>
      {changes.length > 0 && (
        <table className="tbl before-after">
          <caption className="visually-hidden">Corrected values</caption>
          <thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
          <tbody>{changes.map((c) => <tr key={c.field}><td>{c.field}</td><td>{c.before}</td><td><strong>{c.after}</strong></td></tr>)}</tbody>
        </table>
      )}
      <div>
        <div className="small muted" style={{ marginBottom: 4 }}>Re-executed ({executed.size})</div>
        <StageFlow stages={Array.from(executed)} hot={executed} />
      </div>
      {reused.length > 0 && <div><div className="small muted" style={{ marginBottom: 4 }}>Re-checked and reused: inputs unchanged ({reused.length})</div>
        <div className="flow">{reused.map((s: string) => <span key={s} className="stage-chip reused">{stageLabel(s)}</span>)}</div></div>}
      {untouched.length > 0 && <div className="small faint">Not touched ({untouched.length}): {untouched.map(stageLabel).join(', ')}. They do not read the {(DOMAIN_LABELS[corr.domain] || corr.domain).toLowerCase()}.</div>}
    </div>
  );
}

export default function Investigation() {
  const { caseId } = useParams();
  const toast = useToast();
  const { refreshCounts } = useMeta();
  const detail = useApi<any>(() => api.caseDetail(caseId!), [caseId]);
  const runs = useApi<any[]>(() => api.caseRuns(caseId!), [caseId]);
  const events = useApi<any[]>(() => api.caseEvents(caseId!), [caseId]);
  const [sel, setSel] = useState(0);
  const [correcting, setCorrecting] = useState<{ finding: any; taskId?: number } | null>(null);
  const [lastChange, setLastChange] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [packet, setPacket] = useState<any>(null);
  const [allEvents, setAllEvents] = useState(false);

  const reloadAll = () => { detail.reload(); runs.reload(); events.reload(); refreshCounts(); };
  const d = detail.data;
  const rb = d?.root_blocker;
  const roots: any[] = rb?.roots || [];
  // roots can repeat the same code and evidence (e.g. two waiting-period rules), so select by position
  const selected = useMemo(() => roots[sel] || roots[0], [roots, sel]);

  if (detail.error) return <><PageHead title="Case" /><ErrorState message={detail.error} onRetry={detail.reload} /></>;
  if (!d) return <><PageHead title="Case" /><Skeleton rows={8} /></>;

  const c = d.case, e = d.encounter;
  const st = caseState(d);
  const consequencesOf = (code: string) => (rb?.consequences || []).filter((x: any) => x.caused_by === code);
  const IDK = ['line_no', 'seq_num', 'item', 'document', 'phrase', 'field'];
  const taskFor = (f: any) => d.tasks.find((t: any) => t.code === f.code && t.status === 'OPEN'
    && IDK.every((k) => String((t.evidence || {})[k] ?? '') === String((f.evidence || {})[k] ?? '')));

  const process = async () => {
    setBusy(true);
    try {
      const r = await api.processCase(c.id);
      toast(`Run ${r.run.id}: ${r.run.stages_executed} executed, ${r.run.stages_reused} reused → ${humanize(r.case.status)}`);
      reloadAll();
    } catch (err) { toast((err as Error).message, 'error'); } finally { setBusy(false); }
  };
  const showPacket = async () => {
    try { setPacket(await api.casePacket(c.id)); } catch (err) { toast((err as Error).message, 'error'); }
  };

  const findingsByRole = (role: string) => d.findings.filter((f: any) => f.role === role);
  const lastCorrection = d.corrections[d.corrections.length - 1];
  const lastCorrRun = lastCorrection && (runs.data || []).find((r: any) => r.id === lastCorrection.run_id);
  const cons = selected ? consequencesOf(selected.code) : [];
  const task = selected ? taskFor(selected) : undefined;
  const correctable = !!selected && !!draftFor(selected, d) && selected.correctable;
  const rerun: string[] = selected?.rerun_stages || [];
  const notRerun = ALL_STAGES.filter((s) => !rerun.includes(s));
  const resultRun = lastChange?.run || lastCorrRun;
  const resultCorr = lastChange?.correction || lastCorrection;

  return (
    <>
      <PageHead
        title={c.case_ref}
        sub={<span className="row"><Badge value={c.status} /> <span>Encounter {e.hadm_id} · {e.drg_description || 'no DRG'} · </span>
          <span className="badge b-outline" title="Data provenance">{e.data_source}</span></span>}
        actions={<>
          <Link className="btn" to={`/studio?encounter=${e.hadm_id}`}>Open in execution studio</Link>
          <Button onClick={showPacket}>Claim packet</Button>
          <Button busy={busy} onClick={process}>{c.status === 'NEW' ? 'Process case' : 'Re-run all stages'}</Button>
        </>} />

      <div className={`callout status-banner ${st.tone}`} role="status" style={{ marginBottom: 16 }}>
        <strong>{st.headline}.</strong> {st.sentence}
      </div>

      <div className="grid g-main-rail">
        <div className="stack">
          {/* ---------- 1. what is wrong ---------- */}
          <Card title="What is wrong?" className={selected ? `blocker ${selected.severity === 'REVIEW' ? 'review' : ''}` : ''}
            meta={roots.length ? `${roots.length} root blocker${roots.length > 1 ? 's' : ''} · ${rb.consequences.length} downstream consequence${rb.consequences.length === 1 ? '' : 's'}` : undefined}>
            {!rb ? <EmptyState title="Not analysed yet">Process the case to run the checks.</EmptyState>
              : !selected ? (
                <div className="small">Nothing is blocking this case. {st.openTasks.length > 0 ? `${st.openTasks.length} review task(s) are still open; see the panel on the right.` : 'All automated checks passed.'}</div>
              ) : (
                <div className="stack" style={{ gap: 10 }}>
                  {roots.length > 1 && (
                    <div className="row" role="group" aria-label="Choose root blocker">
                      <span className="small muted">Issues to fix:</span>
                      {roots.map((r, i) => (
                        <button key={`${i}:${r.stage}:${r.code}`} className="chip" aria-pressed={selected === r} onClick={() => setSel(i)}>
                          {r.title}{roots.filter((x) => x.title === r.title).length > 1 ? ` (${r.rule_id || `#${i + 1}`})` : ''}</button>
                      ))}
                    </div>
                  )}
                  <div className="row"><span className="blocker-title">{selected.title}</span><Badge value={selected.severity} /><Badge value="ROOT" /></div>
                  <p className="plain">{selected.message}</p>
                  <p className="small muted" style={{ margin: 0 }}><strong>Why it matters:</strong> {WHY[selected.severity] || ''}</p>
                  {cons.length > 0 && (
                    <div className="small">
                      <strong>Knock-on effects.</strong> This root cause also triggers {cons.length} downstream finding{cons.length > 1 ? 's' : ''}. They are not separate problems and clear once it is fixed:
                      <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{cons.map((x: any, i: number) => <li key={i}>{x.title} <span className="faint">({stageLabel(x.stage)})</span></li>)}</ul>
                    </div>
                  )}
                </div>
              )}
          </Card>

          {/* ---------- 2. evidence ---------- */}
          {selected && (
            <Card title="Evidence" meta={`detected by ${stageLabel(selected.stage)}`}>
              <Evidence f={selected} />
            </Card>
          )}

          {/* ---------- 3. what to do next ---------- */}
          {selected && (
            <Card title="What to do next">
              <dl className="kv">
                <dt>Owner</dt><dd><strong>{selected.owner_team}</strong> ({DOMAIN_LABELS[selected.owner_domain] || selected.owner_domain})</dd>
                <dt>Suggested correction</dt><dd>{selected.next_action}</dd>
              </dl>
              <div className="row" style={{ marginTop: 12 }}>
                {correctable && <Button variant="primary" onClick={() => setCorrecting({ finding: selected, taskId: task?.id })}>Apply correction…</Button>}
                {task && <a href="#tasks" className="small">Review task #{task.id} ({task.allowed_decisions.map(humanize).join(' / ')})</a>}
                {!correctable && !task && <span className="small muted">No data correction applies; see the review tasks.</span>}
              </div>
            </Card>
          )}

          {/* ---------- 4. what happens when corrected ---------- */}
          {(selected || resultCorr) && (
            <Card title="What happens when corrected?">
              <div className="stack" style={{ gap: 12 }}>
                {selected && (
                  <div>
                    <div className="small" style={{ marginBottom: 6 }}>
                      A correction to the {(DOMAIN_LABELS[selected.owner_domain] || selected.owner_domain).toLowerCase()} re-checks <strong>{rerun.length} of {ALL_STAGES.length}</strong> workflow stages:
                    </div>
                    <StageFlow stages={rerun} />
                    <div className="small faint" style={{ marginTop: 6 }}>
                      Re-checked stages whose inputs turn out unchanged are reused instead of executed again.
                      {notRerun.length > 0 && <> Not touched: {notRerun.map(stageLabel).join(', ')}.</>}
                    </div>
                  </div>
                )}
                {resultCorr && (
                  <div className="result-block">
                    <h3 style={{ marginBottom: 8 }}>Result of the last correction <span className="small faint">{lastCorrection ? dateTime(lastCorrection.created_at) : ''}</span></h3>
                    {resultRun ? <CorrectionResult corr={resultCorr} run={resultRun} statusBefore={lastChange?.status_before} statusAfter={lastChange?.case?.status} />
                      : <Skeleton rows={2} />}
                  </div>
                )}
              </div>
            </Card>
          )}

          {/* ---------- technical details ---------- */}
          <section aria-labelledby="tech-title" className="stack" style={{ gap: 8 }}>
            <h2 id="tech-title">Technical details</h2>
            <Disclosure title="All findings" summary={`${d.findings.length} finding(s), roots and consequences`}>
              <div className="stack">
                {[['ROOT', 'Root blockers'], ['CONSEQUENCE', 'Downstream consequences'], ['ADVISORY', 'Advisory'], ['INFO', 'Adjustments & information']].map(([role, label]) => {
                  const fs = findingsByRole(role);
                  if (!fs.length) return null;
                  return (
                    <div key={role}>
                      <h3 style={{ marginBottom: 6 }}>{label} <span className="faint">{fs.length}</span></h3>
                      <div className="card">{fs.map((f: any) => (
                        <div className="finding" key={f.id}>
                          <Badge value={f.severity} />
                          <div><div><strong>{f.title}</strong> <span className="faint small">· {stageLabel(f.stage)}{f.caused_by ? ` · caused by ${humanize(f.caused_by)}` : ''}</span></div>
                            {f.role === 'CONSEQUENCE' && rb?.consequences && (() => {
                              const cc = rb.consequences.find((x: any) => x.code === f.code && x.caused_by === f.caused_by);
                              return cc?.causal_rule ? <div className="src">Why a consequence: {cc.causal_rule}.</div> : null;
                            })()}
                            <div className="msg">{f.message}</div>
                            {(f.rule_id || f.source_document) && <div className="src">{f.rule_id} {f.source_document && `· ${f.source_document}`} {f.source_page && `p.${f.source_page}`}</div>}
                          </div>
                          {f.amount != null ? <span className="num small">{money(f.amount)}</span> : <span />}
                        </div>
                      ))}</div>
                    </div>
                  );
                })}
                {d.findings.length === 0 && <EmptyState title="No findings" />}
              </div>
            </Disclosure>
            <Disclosure title="Hospital bill lines" summary={d.billing ? `${money(d.billing.lines_total)} billed · ${money(d.billing.disputed_amount)} disputed` : 'not run'}>
              {d.billing ? (
                <div className="stack">
                  <div className="grid g-4">
                    <div><div className="small muted">Recorded bill</div><div style={{ fontWeight: 650 }}>{money(d.billing.lines_total)}</div></div>
                    <div><div className="small muted">Disputed</div><div style={{ fontWeight: 650, color: 'var(--blocked)' }}>{money(d.billing.disputed_amount)}</div></div>
                    <div><div className="small muted">Verified</div><div style={{ fontWeight: 650 }}>{money(d.billing.verified_total)}</div></div>
                    <div><div className="small muted">Bed-days / LOS</div><div style={{ fontWeight: 650 }}>{d.billing.bed_days} / {d.data_validation?.los_days ?? '—'}</div></div>
                  </div>
                  <div className="table-wrap"><table className="tbl">
                    <thead><tr><th>#</th><th>Item</th><th className="num">Qty</th><th className="num">Unit price</th><th className="num">Rate card</th><th className="num">Amount</th><th className="num">Expected</th><th>Checks</th></tr></thead>
                    <tbody>{d.billing.line_results.map((l: any) => (
                      <tr key={l.line_no} className={l.codes.length ? 'flag' : ''}>
                        <td className="num">{l.line_no}</td>
                        <td><span className="mono small">{l.item_code}</span><div className="small faint">{l.description}{l.linked_icd_code ? ` · ${l.linked_icd_code}` : ''}</div></td>
                        <td className="num">{l.quantity}</td><td className="num">{money(l.unit_price)}</td><td className="num">{money(l.expected_unit_price)}</td>
                        <td className="num">{money(l.amount)}</td><td className="num">{money(l.expected_amount)}</td>
                        <td>{l.codes.length ? l.codes.map((x: string) => <span key={x} className="badge b-blocked" style={{ marginRight: 4 }}>{humanize(x)}</span>) : <span className="badge b-complete">OK</span>}</td>
                      </tr>))}</tbody>
                  </table></div>
                  <div className="small faint">Rate card: synthetic demo tariff (not real hospital prices).</div>
                </div>) : <EmptyState title="Billing has not run yet" />}
            </Disclosure>
            <Disclosure title="Coverage & reconciliation" summary="rule-based estimate with policy citations">
              {d.adjudication ? (
                <div className="stack">
                  <div className="callout small">{d.adjudication.label}. {d.adjudication.eligibility_blocked && <strong>Eligibility is blocked: the estimate shows what would be payable if eligible.</strong>}</div>
                  <Waterfall steps={d.adjudication.waterfall} />
                  <div className="table-wrap"><table className="tbl">
                    <thead><tr><th>Step</th><th className="num">Amount</th><th>Basis</th><th>Citation</th></tr></thead>
                    <tbody>{d.adjudication.waterfall.map((s: any, i: number) => (
                      <tr key={i}><td>{humanize(s.step)}</td><td className="num">{money(s.amount)}</td><td className="small">{s.basis}</td>
                        <td className="small">{s.rule_id ? `${s.rule_id} · ${s.source_document} p.${s.source_page}` : '—'}</td></tr>))}</tbody>
                  </table></div>
                  {d.reconciliation && <div className="small">Waterfall residual: <strong>{money(d.reconciliation.waterfall_residual)}</strong> {d.reconciliation.balanced ? <Badge value="SUCCEEDED" title="balanced" /> : <Badge value="FAILED" />} · Bill header {money(d.reconciliation.header_total)} vs lines {money(d.reconciliation.lines_total)}</div>}
                  <h3>Policy rule results <span className="faint">({d.eligibility?.policy_version_id}, {d.eligibility?.n_active_rules} active rules{d.eligibility?.n_quarantined_rules ? `, ${d.eligibility.n_quarantined_rules} quarantined` : ''})</span></h3>
                  <div className="table-wrap"><table className="tbl">
                    <thead><tr><th>Rule</th><th>Result</th><th>Reason</th><th>Expected</th><th>Actual</th><th>Source</th></tr></thead>
                    <tbody>{[...(d.eligibility?.rule_results || []), ...(d.adjudication?.rule_results || [])].map((r: any, i: number) => (
                      <tr key={i}><td className="mono small">{r.rule_id}</td><td><span className={`badge ${r.severity === 'PASS' ? 'b-complete' : r.severity === 'FAIL' ? 'b-blocked' : r.severity === 'WARNING' ? 'b-review' : 'b-info'}`}>{humanize(r.severity)}</span></td>
                        <td className="small">{r.reason}</td><td className="small">{r.expected}</td><td className="small">{r.actual}</td>
                        <td className="small">{r.source_document} {r.source_page && `p.${r.source_page}`}</td></tr>))}</tbody>
                  </table></div>
                </div>) : <EmptyState title="Adjudication has not run yet" />}
            </Disclosure>
            <Disclosure title="Encounter record" summary="diagnoses, procedures, checklist, documents, discharge note">
              <div className="grid g-2">
                <div className="stack">
                  <h3>Coded diagnoses</h3>
                  <table className="tbl"><tbody>{e.diagnoses.map((x: any) => <tr key={x.seq_num}><td className="num">{x.seq_num}</td><td className="mono">{x.icd_code}</td><td className="small">{x.long_title || <span className="faint">not in code reference</span>}</td></tr>)}</tbody></table>
                  <h3>Procedures</h3>
                  {e.procedures.length ? <table className="tbl"><tbody>{e.procedures.map((x: any) => <tr key={x.seq_num}><td className="mono">{x.icd_code}</td><td className="small">{x.long_title}</td><td className="small">{date(x.chartdate)}</td></tr>)}</tbody></table> : <span className="small faint">None</span>}
                  <h3>Administrative checklist</h3>
                  <div className="row">{e.checklist.map((x: any) => <span key={x.item} className={`badge ${x.status === 'COMPLETE' ? 'b-complete' : 'b-review'}`}>{humanize(x.item)}</span>)}</div>
                  <h3>Documents submitted</h3>
                  <div className="row">{e.documents.map((x: string) => <span key={x} className="badge b-outline">{x}</span>)}</div>
                </div>
                <div className="stack">
                  <h3>Discharge note <span className="faint small">{e.note?.provenance}</span></h3>
                  {e.note ? <div className="note-text" tabIndex={0}>{e.note.text}</div> : <span className="faint">No note</span>}
                  {d.data_validation?.note_diagnoses?.length > 0 && <div className="small muted">Diagnoses extracted from note: {d.data_validation.note_diagnoses.join('; ')}</div>}
                </div>
              </div>
            </Disclosure>
            <Disclosure title="Workflow runs" summary={`${runs.data?.length ?? '…'} run(s), stage by stage`}>
              {runs.data ? (
                <div className="stack">{runs.data.map((r: any) => (
                  <div key={r.id} className="card"><div className="card-head"><strong>Run #{r.id}</strong><Badge value={r.status} /><span className="small muted">{humanize(r.trigger)} · {r.execution_mode} · {r.rerun_policy}{r.changed_domains?.length ? ` · changed: ${r.changed_domains.map((x: string) => DOMAIN_LABELS[x] || x).join(', ')}` : ''}</span>
                    <span className="meta">{dateTime(r.started_at)} · {ms(r.wall_ms)} · {r.stages_executed} executed / {r.stages_reused} reused</span></div>
                    <div className="card-body"><div className="flow">{r.stages.map((s: any) => (
                      <span key={s.id} className={`stage-chip ${s.status === 'SUCCEEDED' ? 'hot' : s.status === 'REUSED' ? 'reused' : ''}`} title={`${s.status} · ${ms(s.duration_ms)} · in ${s.input_hash}`}>
                        {stageLabel(s.stage)}{s.status === 'FAILED' ? ' ✕' : ''}</span>))}</div>
                      {r.error && <div className="error-state small" style={{ marginTop: 8 }}>{r.error}</div>}</div></div>))}</div>) : <Skeleton />}
            </Disclosure>
            <Disclosure title="Audit history" summary="corrections, decisions and re-runs in plain language" id="audit">
              <div className="row" style={{ marginBottom: 8 }}>
                <label className="small row" style={{ gap: 6 }}><input type="checkbox" checked={allEvents} onChange={(ev) => setAllEvents(ev.target.checked)} /> Show routine events too</label>
                <Link className="small" to={`/audit?case=${c.id}`} style={{ marginLeft: 'auto' }}>Open in audit trail →</Link>
              </div>
              {events.data ? <AuditTimeline events={events.data} keyOnly={!allEvents} /> : <Skeleton rows={3} />}
            </Disclosure>
            <Disclosure title="Policy explainer" summary="ask about this case's policy text (not authoritative)">
              <Explainer caseId={c.id} />
            </Disclosure>
          </section>
        </div>

        {/* ---------- right rail ---------- */}
        <div className="stack">
          <Card title="Case facts">
            <dl className="kv">
              <dt>Patient</dt><dd>{e.gender || '—'}, {e.anchor_age ?? '—'} y <span className="faint small">(de-identified)</span></dd>
              <dt>Admission</dt><dd>{dateTime(e.admittime)}<div className="small faint">{e.admission_type}</div></dd>
              <dt>Discharge</dt><dd>{e.dischtime ? dateTime(e.dischtime) : <span style={{ color: 'var(--blocked)' }}>missing</span>}</dd>
              <dt>Policy</dt><dd>{d.policy ? <>{d.policy.policy_version_id}<div className="small faint">SI {money(d.policy.sum_insured)} · since {date(d.policy.policy_start_date)}</div></> : '—'}</dd>
              <dt>Billed</dt><dd>{money(c.amount_billed)}</dd>
              <dt>At risk</dt><dd>{money(c.amount_at_risk)}</dd>
              <dt>Est. eligible</dt><dd>{money(c.estimated_eligible)}</dd>
              <dt>Priority</dt><dd>{n(c.priority, 0)}</dd>
            </dl>
          </Card>
          <div id="tasks"><Card title="Review tasks" meta={`${d.tasks.filter((t: any) => t.status === 'OPEN').length} open`}>
            {d.tasks.length === 0 ? <EmptyState title="No review tasks" /> : (
              <div className="stack" style={{ gap: 10 }}>
                {d.tasks.map((t: any) => <TaskItem key={t.id} t={t} detail={d} onCorrect={(f) => setCorrecting({ finding: f, taskId: t.id })} onDone={reloadAll} />)}
              </div>)}
          </Card></div>
        </div>
      </div>

      {correcting && <CorrectionDialog open onClose={() => setCorrecting(null)} finding={correcting.finding} taskId={correcting.taskId} detail={d}
        onApplied={(res) => { setLastChange(res); reloadAll(); }} />}
      <Dialog open={!!packet} onClose={() => setPacket(null)} title="Claim-preparation packet">
        {packet && <>
          <div className="row"><Badge value={packet.status} /><span className="small muted">{packet.summary?.disclaimer}</span></div>
          <div className="small">{packet.summary?.status_meaning}</div>
          <pre className="note-text" style={{ maxHeight: 420 }}>{JSON.stringify(packet.fhir_claim, null, 2)}</pre>
        </>}
      </Dialog>
    </>
  );
}

function TaskItem({ t, detail, onCorrect, onDone }: { t: any; detail: any; onCorrect: (f: any) => void; onDone: () => void }) {
  const toast = useToast();
  const [decision, setDecision] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // only decisions the API allows for THIS task; data corrections go through the correction form
  const decisions = (t.allowed_decisions || []).filter((x: string) => x !== 'CORRECT_DATA');
  const finding = detail.findings.find((f: any) => f.code === t.code) || { code: t.code, evidence: t.evidence, title: t.title };
  const titleId = `task-${t.id}-title`;
  const submit = async () => {
    setError(null);
    if (!decision) { setError('Choose a decision.'); return; }
    if (note.trim().length < 5) { setError('Write a justification of at least 5 characters; it is stored in the audit trail.'); return; }
    setBusy(true);
    try {
      const r = await api.decide(t.id, { decision, note: note.trim() });
      toast(`Decision recorded. Case is now ${humanize(r.case.status)}.`);
      onDone();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div role="group" aria-labelledby={titleId} data-task-id={t.id} style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
      <div className="row"><Badge value={t.severity} /><strong className="small" id={titleId}>{t.title}</strong><Badge value={t.status} /></div>
      <div className="small muted" style={{ marginTop: 4 }}>{t.recommended_action}</div>
      <div className="small faint">Owner: {stageLabel(t.owner_stage)}</div>
      {t.status === 'OPEN' && (
        <form className="stack" style={{ gap: 6, marginTop: 8 }} noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
          {t.allowed_decisions.includes('CORRECT_DATA') && draftFor(finding, detail) && (
            <Button type="button" size="sm" variant="primary" aria-label={`Correct data for: ${t.title}`} onClick={() => onCorrect(finding)}>
              Correct data…</Button>)}
          {decisions.length > 0 && (<>
            <div className="field">
              <label htmlFor={`dec-${t.id}`}>Decision<span className="visually-hidden"> for: {t.title}</span></label>
              <select id={`dec-${t.id}`} className="select" value={decision} aria-invalid={!!error && !decision}
                onChange={(e) => { setDecision(e.target.value); setError(null); }}>
                <option value="">Choose…</option>
                {decisions.map((x: string) => <option key={x} value={x}>{humanize(x)}</option>)}
              </select>
            </div>
            {decision && (
              <div className="field">
                <label htmlFor={`note-${t.id}`}>Justification (audited)<span className="visually-hidden"> for: {t.title}</span></label>
                <textarea id={`note-${t.id}`} className="textarea" value={note} maxLength={1000}
                  aria-invalid={!!error && note.trim().length < 5} aria-describedby={error ? `err-${t.id}` : undefined}
                  onChange={(e) => { setNote(e.target.value); setError(null); }} />
              </div>)}
            {error && <div id={`err-${t.id}`} className="err small" role="alert" style={{ color: 'var(--blocked)' }}>{error}</div>}
            {decision && <Button type="submit" size="sm" busy={busy} aria-label={`Record decision for: ${t.title}`}>Record decision</Button>}
          </>)}
        </form>
      )}
      {t.resolution_note && <div className="small faint" style={{ marginTop: 4 }}>“{t.resolution_note}” — {t.resolved_by}</div>}
    </div>
  );
}

function Explainer({ caseId }: { caseId: number }) {
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const ask = async () => {
    setBusy(true); setErr(null);
    try { setRes(await api.explain(caseId, q)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="stack">
      <div className="callout small">Explanations are retrieved from the policy text for this case's policy version. They are <strong>not</strong> authoritative and cannot change any rule result.</div>
      <form className="row" onSubmit={(e) => { e.preventDefault(); if (q.trim().length >= 3) ask(); }}>
        <label htmlFor="explain-q" className="visually-hidden">Question about the policy</label>
        <input id="explain-q" className="input" style={{ flex: 1, minWidth: 220 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. What is the waiting period for pre-existing diseases?" maxLength={500} />
        <Button variant="primary" busy={busy} type="submit" disabled={q.trim().length < 3}>Ask</Button>
      </form>
      {err && <ErrorState message={err} />}
      {res && <div className="stack" style={{ gap: 8 }}>
        <div className="small muted">{res.notice}</div>
        <div className="evidence" style={{ whiteSpace: 'pre-wrap' }}>{res.answer}</div>
        {res.citations?.map((c: any, i: number) => <div key={i} className="small">[{i + 1}] {c.document} p.{c.page} <span className="faint">relevance {c.relevance_score}</span></div>)}
      </div>}
    </div>
  );
}
