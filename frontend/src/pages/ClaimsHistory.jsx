import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../services/api';
import { Loading, ErrorBox, NotSpecified } from '../components/Common';

export default function ClaimsHistory() {
  const [claims, setClaims] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listClaims().then(setClaims).catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  if (error) return <ErrorBox message={error} />;
  if (!claims) return <Loading label="Loading claims…" />;

  return (
    <div>
      <h1>Claims</h1>
      <p className="page-subtitle">
        All claims created in this system are synthetic/demo test records
        derived from or exercised against real policy rules — never real
        insurance submissions.
      </p>
      {claims.length === 0 && <p>No claims created yet.</p>}
      {claims.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Claim ID</th><th>Policy</th><th>Policy Version</th>
              <th>Admission Date</th><th>Amount</th><th>Last Validation</th>
            </tr>
          </thead>
          <tbody>
            {claims.map(c => (
              <tr key={c.claim_id} className="clickable">
                <td><Link to={`/legacy/claims/${c.claim_id}`}>{c.claim_ref}</Link></td>
                <td>{c.insurer} — {c.product}</td>
                <td>{c.policy_version_id}</td>
                <td>{c.admission_date || <NotSpecified />}</td>
                <td>{c.billed_amount ? `Rs. ${c.billed_amount}` : <NotSpecified />}</td>
                <td>{c.last_validation_result || 'Not yet validated'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
