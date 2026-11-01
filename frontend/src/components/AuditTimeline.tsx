import { Link } from 'react-router-dom';
import { describe } from '../lib/auditText';
import { dateTime } from '../lib/format';
import { EmptyState } from './ui';

/* eslint-disable @typescript-eslint/no-explicit-any */
const ICON: Record<string, string> = {
  correction: '✎', decision: '⚖', run: '⟳', status: '●', task: '☐', failure: '!', system: '·',
};

/** Plain-language audit history. `keyOnly` hides routine bookkeeping events. */
export default function AuditTimeline({ events, keyOnly, showCase }: { events: any[]; keyOnly: boolean; showCase?: boolean }) {
  const rows = events.map((e) => ({ e, s: describe(e) })).filter(({ s }) => !keyOnly || !s.routine);
  if (rows.length === 0) return <EmptyState title={keyOnly ? 'No key changes recorded yet' : 'No events recorded yet'} />;
  return (
    <ol className="timeline" aria-label="Audit history">
      {rows.map(({ e, s }) => (
        <li key={e.id} className={`tl-item tl-${s.kind}`} data-event-type={e.event_type}>
          <span className="tl-icon" aria-hidden="true">{ICON[s.kind]}</span>
          <div className="tl-body">
            <div className="row" style={{ gap: 6 }}>
              <strong>{s.title}</strong>
              {showCase && e.case_id && <Link className="small" to={`/claims/${e.case_id}`}>case #{e.case_id}</Link>}
              <span className="small faint" style={{ marginLeft: 'auto' }}>{dateTime(e.ts)} · {e.actor}</span>
            </div>
            <div className="small">{s.text}</div>
            {s.changes && (
              <table className="tbl tl-changes">
                <caption className="visually-hidden">Values before and after</caption>
                <thead><tr><th>What</th><th>Before</th><th>After</th></tr></thead>
                <tbody>{s.changes.map((c) => <tr key={c.field}><td>{c.field}</td><td>{c.before}</td><td><strong>{c.after}</strong></td></tr>)}</tbody>
              </table>
            )}
            <span className="visually-hidden">Recorded event type {e.event_type}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}
