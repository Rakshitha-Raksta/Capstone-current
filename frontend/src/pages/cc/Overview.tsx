import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { dateTime, humanize, money, ms, n, stageLabel } from '../../lib/format';
import { Badge, Button, Card, EmptyState, ErrorState, PageHead, Skeleton, Stat, useToast } from '../../components/ui';
import { BarList, StatusBar } from '../../components/charts';
import { useMeta } from '../../components/AppShell';

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function Overview() {
  const { data, error, loading, reload } = useApi<any>(() => api.overview());
  const { refreshCounts } = useMeta();
  const toast = useToast();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);

  if (error) return <><PageHead title="Operations dashboard" /><ErrorState message={error} onRetry={reload} /></>;
  if (loading && !data) return <><PageHead title="Operations dashboard" /><Skeleton rows={6} /></>;
  const sc = data.status_counts || {};
  const pending = sc.NEW || 0;

  const processPending = async () => {
    setBusy(true);
    try {
      const r = await api.processPending({ limit: 500 });
      toast(`Processed ${r.processed} case(s)${r.failed ? `, ${r.failed} failed` : ''}.`, r.failed ? 'error' : 'ok');
      reload(); refreshCounts();
    } catch (e) { toast((e as Error).message, 'error'); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHead title="Operations dashboard" sub="Backlog, work queues, root-blocker frequency and processing health, from live pipeline results."
        actions={pending > 0 && <Button variant="primary" busy={busy} onClick={processPending}>Process {pending} new case{pending > 1 ? 's' : ''}</Button>} />

      {data.total_cases === 0 ? (
        <Card><EmptyState title="No encounters imported yet">Run <span className="mono">python scripts/setup_demo.py</span> to load the synthetic demo, or see the Data laboratory.</EmptyState></Card>
      ) : (
        <div className="stack">
          <div className="grid g-4">
            <Stat label="Cases" value={n(data.total_cases, 0)} hint={`${pending} not yet processed`} />
            <Stat label="Blocked" value={n(sc.BLOCKED || 0, 0)} hint="need a correction or are confirmed non-claimable" />
            <Stat label="Needs review" value={n(sc.NEEDS_REVIEW || 0, 0)} hint="open human decisions" />
            <Stat label="Preparation complete" value={n(sc.PREPARATION_COMPLETE || 0, 0)} hint="ready for submission packet" />
          </div>

          <Card title="Backlog by status"><StatusBar counts={sc} /></Card>

          <div className="grid g-main-rail">
            <Card title="Needs attention" meta="highest priority first" flush
              actions={<Link to="/claims?status=BLOCKED,NEEDS_REVIEW">All flagged claims →</Link>}>
              {data.attention.length === 0 ? <EmptyState title="Nothing needs attention" /> : (
                <div className="table-wrap">
                  <table className="tbl">
                    <thead><tr><th>Case</th><th>Status</th><th>Root blocker</th><th>Owner stage</th><th className="num">At risk</th><th className="num">Open tasks</th></tr></thead>
                    <tbody>
                      {data.attention.map((c: any) => (
                        <tr key={c.id} className="clickable" onClick={() => nav(`/claims/${c.id}`)}>
                          <td><Link to={`/claims/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.case_ref}</Link><div className="small faint">{c.drg_description || '—'}</div></td>
                          <td><Badge value={c.status} /></td>
                          <td className="small">{c.root_blocker_title || humanize(c.root_blocker_code)}</td>
                          <td className="small">{stageLabel(c.root_blocker_stage)}</td>
                          <td className="num">{money(c.amount_at_risk)}</td>
                          <td className="num">{c.open_tasks}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card title="Work queues" meta="open review tasks by owner">
              {data.work_queues.length === 0 ? <EmptyState title="Review queue is empty" /> : (
                <BarList label="Open tasks by owner stage" items={Object.entries(
                  data.work_queues.reduce((acc: Record<string, number>, q: any) => ({ ...acc, [q.owner_stage]: (acc[q.owner_stage] || 0) + q.open }), {}),
                ).map(([k, v]) => ({ key: k, label: stageLabel(k), value: v as number }))
                  .sort((a, b) => b.value - a.value)} />
              )}
              <div className="divider" />
              <Link to="/review">Open review queue →</Link>
            </Card>
          </div>

          <div className="grid g-2">
            <Card title="Most frequent root blockers" meta="cases · amount at risk">
              {data.top_root_blockers.length === 0 ? <EmptyState title="No root blockers" /> : (
                <BarList label="Root blockers" items={data.top_root_blockers.map((r: any) => ({
                  key: r.code, label: r.title || humanize(r.code), value: r.cases, sub: money(r.amount_at_risk),
                }))} />
              )}
            </Card>
            <Card title="Processing health" meta={`last ${data.processing.recent_runs} pipeline runs`}>
              <dl className="kv">
                <dt>Median run latency</dt><dd className="num" style={{ textAlign: 'left' }}>{ms(data.processing.median_ms)}</dd>
                <dt>P95 run latency</dt><dd>{ms(data.processing.p95_ms)}</dd>
                <dt>Failed runs</dt><dd>{data.processing.failed}</dd>
                <dt>Stages executed / run</dt><dd>{n(data.processing.mean_stages_executed)} of 11</dd>
                <dt>Billed (all cases)</dt><dd>{money(data.amounts.billed)}</dd>
                <dt>Estimated eligible</dt><dd>{money(data.amounts.estimated_eligible)} <span className="faint small">rule-based estimate</span></dd>
              </dl>
              <div className="small faint" style={{ marginTop: 8 }}>Latency is software execution time only; it is not discharge time.</div>
            </Card>
          </div>

          <Card title="Recent system events" flush actions={<Link to="/workflow#audit">Full audit trail →</Link>}>
            {data.recent_events.length === 0 ? <EmptyState title="No events yet" /> : (
              <div className="table-wrap"><table className="tbl">
                <tbody>
                  {data.recent_events.map((e: any) => (
                    <tr key={e.id}>
                      <td className="small faint" style={{ whiteSpace: 'nowrap' }}>{dateTime(e.ts)}</td>
                      <td><span className="badge b-outline">{humanize(e.event_type)}</span></td>
                      <td className="small">{e.summary}</td>
                      <td className="small">{e.case_id ? <Link to={`/claims/${e.case_id}`}>case {e.case_id}</Link> : <span className="faint">system</span>}</td>
                      <td className="small faint">{e.actor}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
