import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { Card, ErrorState, PageHead, Skeleton } from '../../components/ui';

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function PolicyLibrary() {
  const { data, error, reload } = useApi<any[]>(() => api.policies());
  return (
    <>
      <PageHead title="Policy library" sub="The cited policy and regulatory rules behind every eligibility and adjudication result." />
      <div className="stack">
        <div className="grid g-2">
          <div className="callout"><strong>Authoritative:</strong> deterministic rule results. Each one carries its rule id, source document and page.
            They come from the existing 59-rule engine and are the only thing that changes a case's status.</div>
          <div className="callout"><strong>Explanatory only:</strong> the policy explainer (Investigation → Policy explainer) retrieves passages from the policy
            text to help a reviewer read the clause. It cannot change any result.</div>
        </div>
        <Card title="Policy versions" flush meta="real extracted policy documents">
          {error ? <div style={{ padding: 14 }}><ErrorState message={error} onRetry={reload} /></div> : !data ? <div style={{ padding: 14 }}><Skeleton /></div> : (
            <div className="table-wrap"><table className="tbl">
              <thead><tr><th>Insurer</th><th>Product</th><th>Policy version</th><th>UIN</th><th>Status</th><th /></tr></thead>
              <tbody>{data.flatMap((p: any) => p.versions.map((v: any) => (
                <tr key={v.policy_version_id}>
                  <td className="small">{p.insurer}</td><td className="small">{p.product}</td>
                  <td className="mono small">{v.policy_version_id}</td>
                  <td className="mono small">{v.uin}{v.uin_conflict_flag && <span className="badge b-review" style={{ marginLeft: 6 }}>UIN conflict — rules quarantined</span>}</td>
                  <td className="small">{v.status}</td>
                  <td><Link to={`/policies/${p.policy_id}?version=${v.policy_version_id}`} className="small">Rules & citations →</Link></td>
                </tr>)))}</tbody>
            </table></div>)}
        </Card>
        <div className="grid g-3">
          <Card title="Source documents"><p className="small muted">Read the extracted text of each insurer or IRDAI document, page by page.</p><Link to="/policy-source-viewer">Open source viewer →</Link></Card>
          <Card title="Rule coverage"><p className="small muted">32 insurer rules are active (21 evaluated per claim; 11 are administrative), 17 HDFC 2026 rules are quarantined, and 10 IRDAI rules are regulatory references.</p><span className="small faint">Verified by backend/tests/test_rule_coverage.py</span></Card>
          <Card title="Legacy single-claim validator"><p className="small muted">The original pre-submission validator UI, preserved unchanged.</p><Link to="/legacy">Open legacy validator →</Link></Card>
        </div>
      </div>
    </>
  );
}
