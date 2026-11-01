import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { n } from '../../lib/format';
import { Button, Card, ErrorState, PageHead, Pager, Skeleton } from '../../components/ui';
import AuditTimeline from '../../components/AuditTimeline';

/* eslint-disable @typescript-eslint/no-explicit-any */
const PAGE_SIZE = 100;

export default function AuditTrail() {
  const [sp, setSp] = useSearchParams();
  const caseId = sp.get('case') || '';
  // local state so the checkbox responds immediately; the URL is kept in sync for sharing
  const [all, setAll] = useState(sp.get('all') === '1');
  const page = Number(sp.get('page') || 1);
  const [caseInput, setCaseInput] = useState(caseId);
  useEffect(() => setCaseInput(caseId), [caseId]);
  const events = useApi<any>(() => api.audit({ case_id: caseId || undefined, page, page_size: PAGE_SIZE }), [caseId, page]);
  const chain = useApi<any>(() => api.auditVerify(), []);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (!('page' in patch)) next.delete('page');
    setSp(next);
  };

  return (
    <>
      <PageHead title="Audit trail" sub="Every correction, review decision and workflow re-run, in plain language, newest first." />
      <div className="stack">
        <div className="grid g-main-rail">
          <Card title="History" meta={events.data ? `${n(events.data.total, 0)} recorded event(s)${caseId ? ` for case #${caseId}` : ''}` : undefined}>
            <div className="row" style={{ marginBottom: 12, gap: 12 }}>
              <form className="row" onSubmit={(e) => { e.preventDefault(); set({ case: caseInput.trim() }); }}>
                <label htmlFor="audit-case" className="small">Case #</label>
                <input id="audit-case" className="input" style={{ width: 110 }} inputMode="numeric" value={caseInput}
                  onChange={(e) => setCaseInput(e.target.value.replace(/[^0-9]/g, ''))} placeholder="all cases" />
                <Button size="sm" type="submit">Filter</Button>
                {caseId && <Button size="sm" variant="ghost" type="button" onClick={() => set({ case: '' })}>Show all cases</Button>}
              </form>
              <label className="small row" style={{ gap: 6 }}>
                <input type="checkbox" checked={all} onChange={(e) => { setAll(e.target.checked); set({ all: e.target.checked ? '1' : '', page: String(page) }); }} /> Show routine events too
              </label>
              {caseId && <Link className="small" to={`/claims/${caseId}`} style={{ marginLeft: 'auto' }}>Open case #{caseId} →</Link>}
            </div>
            {events.error ? <ErrorState message={events.error} onRetry={events.reload} />
              : !events.data ? <Skeleton rows={6} />
              : <>
                  <AuditTimeline events={events.data.items} keyOnly={!all} showCase={!caseId} />
                  {events.data.total > PAGE_SIZE && <Pager page={page} pageSize={PAGE_SIZE} total={events.data.total} onPage={(p) => set({ page: String(p) })} />}
                </>}
          </Card>
          <div className="stack">
            <Card title="Is the history intact?">
              {chain.error ? <ErrorState message={chain.error} onRetry={chain.reload} /> : !chain.data ? <Skeleton rows={2} /> : chain.data.ok ? (
                <div className="callout complete small" role="status"><strong>Hash chain verified.</strong> {n(chain.data.checked, 0)} events checked; none has been altered or removed since it was written.</div>
              ) : (
                <div className="callout blocked small" role="alert"><strong>Hash chain broken</strong> at event #{chain.data.first_broken_event_id}. An earlier record was changed after it was written.</div>
              )}
              <p className="small muted" style={{ marginBottom: 0 }}>Each event stores a fingerprint (SHA-256) of itself and of the previous event, so editing or deleting an old record breaks every later fingerprint. This makes tampering visible; it does not prevent it.</p>
            </Card>
            <Card title="How to read this">
              <dl className="kv legend-kv">
                <dt>✎ Data corrected</dt><dd>Who changed which record, why, and the values before and after.</dd>
                <dt>⚖ Reviewer decision</dt><dd>A person accepted an item with a justification, or confirmed it as blocked.</dd>
                <dt>⟳ Workflow run</dt><dd>How many stages were re-executed, how many were reused unchanged, and the resulting status.</dd>
                <dt>☐ Review task</dt><dd>Opened when an issue needs a person; closed automatically when a re-run no longer finds it.</dd>
                <dt>! Failure</dt><dd>A stage or run that did not succeed.</dd>
              </dl>
              <p className="small faint" style={{ marginBottom: 0 }}>“Routine events” are bookkeeping entries (run started, status set during processing, tasks opened on first processing).</p>
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
