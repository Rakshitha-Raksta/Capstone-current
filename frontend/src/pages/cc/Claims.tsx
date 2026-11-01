import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { humanize, stageLabel } from '../../lib/format';
import { nextStep } from '../../lib/caseState';
import { Badge, Card, EmptyState, ErrorState, PageHead, Pager, Skeleton } from '../../components/ui';

/* eslint-disable @typescript-eslint/no-explicit-any */
const STATUSES = ['BLOCKED', 'NEEDS_REVIEW', 'PREPARATION_COMPLETE', 'NEW', 'ERROR'];
const COLS: { key: string; label: string; sortable?: boolean; num?: boolean }[] = [
  { key: 'case_ref', label: 'Case ID', sortable: true },
  { key: 'status', label: 'Status' },
  { key: 'root', label: 'Main issue' },
  { key: 'priority', label: 'Priority', sortable: true, num: true },
  { key: 'next', label: 'Next action' },
];

export default function Claims() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const status = sp.get('status') || '';
  const q = sp.get('q') || '';
  const root = sp.get('root') || '';
  const sort = sp.get('sort') || 'priority';
  const order = sp.get('order') || 'desc';
  const page = Number(sp.get('page') || 1);
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);

  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (!('page' in patch)) next.delete('page');
    setSp(next);
  };
  const { data, error, loading, reload } = useApi<any>(
    () => api.cases({ status, q, root, sort, order, page, page_size: 25 }), [status, q, root, sort, order, page]);
  const overview = useApi<any>(() => api.overview());
  const statusSet = new Set(status ? status.split(',') : []);
  const toggleStatus = (s: string) => {
    const n = new Set(statusSet);
    if (n.has(s)) n.delete(s); else n.add(s);
    set({ status: Array.from(n).join(',') });
  };
  const sortBy = (k: string) => set({ sort: k, order: sort === k && order === 'desc' ? 'asc' : 'desc' });

  return (
    <>
      <PageHead title="Cases" sub="One row per hospital encounter. Open a case to see the evidence and how to fix it. Statuses are internal preparation states, not insurer decisions." />
      <Card flush>
        <div className="row" style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)', gap: 12 }}>
          <form role="search" onSubmit={(e) => { e.preventDefault(); set({ q: search.trim() }); }} className="row">
            <label htmlFor="claim-search" className="visually-hidden">Search claims</label>
            <input id="claim-search" className="input" placeholder="Case ref, encounter id, DRG, blocker…" value={search}
              onChange={(e) => setSearch(e.target.value)} style={{ width: 260 }} maxLength={64} />
          </form>
          <div className="row" role="group" aria-label="Filter by status">
            {STATUSES.map((s) => (
              <button key={s} type="button" className="chip" aria-pressed={statusSet.has(s)} onClick={() => toggleStatus(s)}>
                {humanize(s)}
              </button>
            ))}
          </div>
          <div className="row">
            <label htmlFor="root-filter" className="small muted">Root blocker</label>
            <select id="root-filter" className="select" value={root} onChange={(e) => set({ root: e.target.value })}>
              <option value="">Any</option>
              {(overview.data?.top_root_blockers || []).map((r: any) => <option key={r.code} value={r.code}>{r.title || humanize(r.code)} ({r.cases})</option>)}
            </select>
          </div>
        </div>
        {error ? <div style={{ padding: 14 }}><ErrorState message={error} onRetry={reload} /></div>
          : loading && !data ? <div style={{ padding: 14 }}><Skeleton rows={8} /></div>
          : data.items.length === 0 ? <EmptyState title="No claims match these filters" />
          : (
            <>
              <div className="table-wrap">
                <table className="tbl">
                  <caption className="visually-hidden">Cases. Higher priority numbers should be handled first.</caption>
                  <thead>
                    <tr>
                      {COLS.map((c) => (
                        <th key={c.key} className={c.num ? 'num' : ''} aria-sort={sort === c.key ? (order === 'asc' ? 'ascending' : 'descending') : undefined}>
                          {c.sortable ? <button className="sort" onClick={() => sortBy(c.key)}>{c.label}{sort === c.key ? (order === 'asc' ? ' ▲' : ' ▼') : ''}</button> : c.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((c: any) => (
                      <tr key={c.id} className="clickable" onClick={() => nav(`/claims/${c.id}`)}>
                        <td><Link to={`/claims/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.case_ref}</Link></td>
                        <td><Badge value={c.status} /></td>
                        <td className="small">{c.root_blocker_code ? <>{c.root_blocker_title || humanize(c.root_blocker_code)}<div className="faint">found by {stageLabel(c.root_blocker_stage)}</div></> : <span className="faint">none</span>}</td>
                        <td className="num">{c.priority?.toFixed?.(0) ?? '—'}</td>
                        <td className="small">{nextStep(c)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager page={page} pageSize={data.page_size} total={data.total} onPage={(p) => set({ page: String(p) })} />
            </>
          )}
      </Card>
    </>
  );
}
