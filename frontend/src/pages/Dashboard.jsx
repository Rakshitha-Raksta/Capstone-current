import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../services/api';
import { Loading, ErrorBox } from '../components/Common';

export default function Dashboard() {
  const [policies, setPolicies] = useState(null);
  const [claims, setClaims] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([api.listPolicies(), api.listClaims()])
      .then(([p, c]) => { setPolicies(p); setClaims(c); })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  if (error) return <ErrorBox message={`Failed to load dashboard: ${error}`} />;
  if (!policies) return <Loading label="Loading dashboard…" />;

  const insurers = new Set(policies.map(p => p.insurer));
  const totalVersions = policies.reduce((sum, p) => sum + p.versions.length, 0);

  return (
    <div>
      <h1>Dashboard</h1>
      <p className="page-subtitle">Overview of the current policy dataset and recent claims.</p>

      <div className="card-grid">
        <div className="stat-card">
          <div className="stat-value">{policies.length}</div>
          <div className="stat-label">Total Policies</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{insurers.size}</div>
          <div className="stat-label">Insurers</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{totalVersions}</div>
          <div className="stat-label">Policy Versions</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{claims ? claims.length : '—'}</div>
          <div className="stat-label">Recent Claims</div>
        </div>
      </div>

      <h2>Recent Claims</h2>
      {claims && claims.length === 0 && <p className="page-subtitle">No claims created yet. Start with "New Claim".</p>}
      {claims && claims.length > 0 && (
        <table>
          <thead>
            <tr><th>Claim ID</th><th>Insurer / Product</th><th>Policy Version</th><th>Admission</th><th>Status</th></tr>
          </thead>
          <tbody>
            {claims.slice(0, 8).map(c => (
              <tr key={c.claim_id} className="clickable">
                <td><Link to={`/legacy/claims/${c.claim_id}`}>{c.claim_ref}</Link></td>
                <td>{c.insurer} — {c.product}</td>
                <td>{c.policy_version_id}</td>
                <td>{c.admission_date}</td>
                <td>{c.last_validation_result || 'Not yet validated'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
