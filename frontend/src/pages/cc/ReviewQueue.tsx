import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { dateTime, DOMAIN_LABELS, humanize, money, stageLabel } from '../../lib/format';
import { Badge, Button, Card, EmptyState, ErrorState, Field, PageHead, Pager, Skeleton, Tabs, useToast } from '../../components/ui';
import { useMeta } from '../../components/AppShell';

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function ReviewQueue() {
  const toast = useToast();
  const { refreshCounts } = useMeta();
  const [view, setView] = useState('OPEN');
  const [severity, setSeverity] = useState('');
  const [owner, setOwner] = useState('');
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const status = view === 'OPEN' ? 'OPEN' : 'RESOLVED_BY_CORRECTION,ACCEPTED_WITH_JUSTIFICATION,CONFIRMED_BLOCKED,SUPERSEDED';
  const { data, error, loading, reload } = useApi<any>(() => api.tasks({ status, severity, owner_stage: owner, page, page_size: 20 }),
    [status, severity, owner, page]);
  const [decision, setDecision] = useState<Record<number, { d: string; note: string }>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [errors, setErrors] = useState<Record<number, string>>({});

  const decide = async (t: any) => {
    const v = decision[t.id];
    const err = !v?.d ? 'Choose a decision.'
      : (v.note || '').trim().length < 5 ? 'Write a justification of at least 5 characters; it is stored in the audit trail.' : '';
    setErrors((x) => ({ ...x, [t.id]: err }));
    if (err) return;
    setBusy(t.id);
    try {
      const r = await api.decide(t.id, { decision: v.d, note: v.note.trim() });
      toast(`${humanize(v.d)} recorded on ${t.case_ref}; case now ${humanize(r.case.status)}.`);
      reload(); refreshCounts();
    } catch (e) { setErrors((x) => ({ ...x, [t.id]: (e as Error).message })); } finally { setBusy(null); }
  };

  return (
    <>
      <PageHead title="Review queue" sub="Human decisions routed from root blockers, with the evidence and the stages each decision will re-run." />
      <Card flush>
        <div style={{ padding: '6px 14px 0' }}>
          <Tabs value={view} onChange={(v) => { setView(v); setPage(1); }} tabs={[{ id: 'OPEN', label: 'Open' }, { id: 'DONE', label: 'History' }]} />
          <div className="row" style={{ paddingBottom: 12 }}>
            <label htmlFor="rq-sev" className="small muted">Severity</label>
            <select id="rq-sev" className="select" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }}>
              <option value="">Any</option><option value="BLOCKING">Blocking</option><option value="REVIEW">Review</option>
            </select>
            <label htmlFor="rq-owner" className="small muted">Owner stage</label>
            <select id="rq-owner" className="select" value={owner} onChange={(e) => { setOwner(e.target.value); setPage(1); }}>
              <option value="">Any</option>
              {['data_validation', 'discharge_readiness', 'billing', 'eligibility', 'review_routing'].map((s) => <option key={s} value={s}>{stageLabel(s)}</option>)}
            </select>
          </div>
        </div>
        {error ? <div style={{ padding: 14 }}><ErrorState message={error} onRetry={reload} /></div>
          : loading && !data ? <div style={{ padding: 14 }}><Skeleton rows={6} /></div>
          : data.items.length === 0 ? <EmptyState title={view === 'OPEN' ? 'No open review tasks' : 'No review history yet'} />
          : (
            <>
              <div className="table-wrap"><table className="tbl">
                <thead><tr><th>Severity</th><th>Issue</th><th>Case</th><th>Owner</th><th>Status</th><th>Created</th><th /></tr></thead>
                <tbody>
                  {data.items.map((t: any) => (
                    <Fragment key={t.id}>
                      <tr>
                        <td><Badge value={t.severity} /></td>
                        <td><strong className="small">{t.title}</strong><div className="small muted" style={{ maxWidth: 520 }}>{t.reason}</div></td>
                        <td className="small"><Link to={`/claims/${t.case_id}`}>{t.case_ref}</Link><div><Badge value={t.case_status} /></div></td>
                        <td className="small">{stageLabel(t.owner_stage)}<div className="faint">{DOMAIN_LABELS[t.input_domain] || t.input_domain}</div></td>
                        <td><Badge value={t.status} /></td>
                        <td className="small faint">{dateTime(t.created_at)}</td>
                        <td><Button size="sm" aria-expanded={open === t.id} onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? 'Hide' : 'Details'}</Button></td>
                      </tr>
                      {open === t.id && (
                        <tr className="expand-row"><td colSpan={7}>
                          <div className="grid g-2">
                            <div className="stack" style={{ gap: 8 }}>
                              <div className="small"><strong>Recommended action:</strong> {t.recommended_action}</div>
                              <div className="evidence small">
                                {t.evidence?.rule_id && <div>Rule <span className="mono">{t.evidence.rule_id}</span> · {t.evidence.source_document} {t.evidence.source_page && `p.${t.evidence.source_page}`}</div>}
                                {(t.evidence?.expected || t.evidence?.actual) && <div>Expected: {t.evidence.expected ?? '—'} · Recorded: {t.evidence.actual ?? '—'}</div>}
                                {t.evidence?.amount != null && <div>Amount: {money(t.evidence.amount)}</div>}
                              </div>
                              <div className="small">Re-runs on resolution: {(t.required_reruns || []).map(stageLabel).join(' → ')}</div>
                              {t.resolution_note && <div className="small faint">“{t.resolution_note}” — {t.resolved_by}, {dateTime(t.resolved_at)}</div>}
                            </div>
                            {t.status === 'OPEN' && (
                              <div className="stack" style={{ gap: 8 }}>
                                {t.allowed_decisions.includes('CORRECT_DATA') && <div className="small">This needs a data correction: <Link to={`/claims/${t.case_id}`}>open the investigation workspace →</Link></div>}
                                {t.allowed_decisions.filter((x: string) => x !== 'CORRECT_DATA').length > 0 && (<>
                                  <Field label={<>Decision<span className="visually-hidden"> for: {t.title}, {t.case_ref}</span></>} htmlFor={`rq-d-${t.id}`}>
                                    <select id={`rq-d-${t.id}`} className="select" value={decision[t.id]?.d || ''} aria-invalid={!!errors[t.id] && !decision[t.id]?.d}
                                      onChange={(e) => setDecision((x) => ({ ...x, [t.id]: { d: e.target.value, note: x[t.id]?.note || '' } }))}>
                                      <option value="">Choose…</option>
                                      {t.allowed_decisions.filter((x: string) => x !== 'CORRECT_DATA').map((x: string) => <option key={x} value={x}>{humanize(x)}</option>)}
                                    </select>
                                  </Field>
                                  <Field label={<>Justification (recorded in the audit trail)<span className="visually-hidden"> for: {t.title}, {t.case_ref}</span></>} htmlFor={`rq-n-${t.id}`}>
                                    <textarea id={`rq-n-${t.id}`} className="textarea" value={decision[t.id]?.note || ''} maxLength={1000}
                                      aria-invalid={!!errors[t.id] && (decision[t.id]?.note || '').trim().length < 5}
                                      aria-describedby={errors[t.id] ? `rq-e-${t.id}` : undefined}
                                      onChange={(e) => setDecision((x) => ({ ...x, [t.id]: { d: x[t.id]?.d || '', note: e.target.value } }))} />
                                  </Field>
                                  {errors[t.id] && <div id={`rq-e-${t.id}`} className="small" role="alert" style={{ color: 'var(--blocked)' }}>{errors[t.id]}</div>}
                                  <Button variant="primary" size="sm" busy={busy === t.id} aria-label={`Record decision for: ${t.title}, ${t.case_ref}`}
                                    onClick={() => decide(t)}>Record decision</Button>
                                </>)}
                              </div>)}
                          </div>
                        </td></tr>)}
                    </Fragment>
                  ))}
                </tbody>
              </table></div>
              <Pager page={page} pageSize={20} total={data.total} onPage={setPage} />
            </>)}
      </Card>
    </>
  );
}
