import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, ApiError } from '../services/api';
import { Loading, ErrorBox, OverallBanner, SeverityBadge, NotSpecified } from '../components/Common';

const SEVERITIES = ['PASS', 'WARNING', 'PARTIAL_DEDUCTION', 'FAIL'];

export default function ClaimDetail() {
  const { claimId } = useParams();
  const [claim, setClaim] = useState(null);
  const [validation, setValidation] = useState(null);
  const [error, setError] = useState(null);
  const [revalidating, setRevalidating] = useState(false);

  function load() {
    setError(null);
    Promise.all([api.getClaim(claimId), api.getValidation(claimId).catch(() => null)])
      .then(([c, v]) => { setClaim(c); setValidation(v); })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }

  useEffect(load, [claimId]);

  async function handleRevalidate() {
    setRevalidating(true);
    setError(null);
    try {
      const v = await api.validateClaim(claimId);
      setValidation(v);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRevalidating(false);
    }
  }

  if (error) return <ErrorBox message={error} />;
  if (!claim) return <Loading label="Loading claim…" />;

  const counts = Object.fromEntries(SEVERITIES.map(s => [s, 0]));
  (validation?.results || []).forEach(r => { if (counts[r.severity] !== undefined) counts[r.severity]++; });

  return (
    <div>
      <h1>Claim {claim.claim_ref}</h1>
      <p className="page-subtitle">
        Synthetic/demo claim — not a real insurance submission. Policy version:{' '}
        <strong>{claim.policy_version_db_id}</strong>. Provenance: {claim.claim_provenance}.
      </p>

      {!validation && (
        <div>
          <p>This claim has not been validated yet.</p>
          <button className="primary" onClick={handleRevalidate} disabled={revalidating}>
            {revalidating ? 'Validating…' : 'Run Validation'}
          </button>
        </div>
      )}

      {validation && (
        <>
          <OverallBanner result={validation.overall_result} />

          <div className="card-grid">
            {SEVERITIES.map(s => (
              <div className="stat-card" key={s}>
                <div className="stat-value"><SeverityBadge severity={s} /></div>
                <div className="stat-value" style={{ marginTop: 6 }}>{counts[s]}</div>
              </div>
            ))}
          </div>

          <button className="secondary" onClick={handleRevalidate} disabled={revalidating}>
            {revalidating ? 'Re-validating…' : 'Re-run Validation'}
          </button>

          <h2>Validation Details</h2>
          <table>
            <thead>
              <tr>
                <th>Rule</th><th>Category</th><th>Severity</th><th>Reason</th>
                <th>Expected</th><th>Actual</th><th>Source</th><th>Page</th>
              </tr>
            </thead>
            <tbody>
              {validation.results.map((r, i) => (
                <tr key={i} className="clickable" onClick={() => {
                  window.location.href = `/legacy/claims/${claimId}/rules/${encodeURIComponent(r.rule_id)}`;
                }}>
                  <td>{r.rule_id}</td>
                  <td>{r.category}</td>
                  <td><SeverityBadge severity={r.severity} /></td>
                  <td>{r.reason}</td>
                  <td>{r.expected}</td>
                  <td>{r.actual}</td>
                  <td>{r.source?.document || <NotSpecified />}</td>
                  <td>{r.source?.page || <NotSpecified />}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <FinancialImpact financials={validation.financials} />
        </>
      )}

      <AskPolicy claimId={claimId} />
    </div>
  );
}

function AskPolicy({ claimId }) {
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState([]);

  async function handleAsk(e) {
    e.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.askClaimQuestion(claimId, question);
      setResult(r);
      setHistory(h => [{ question, ...r }, ...h]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAsking(false);
    }
  }

  return (
    <div style={{ marginTop: 32 }}>
      <h2>Ask This Policy a Question</h2>
      <p className="page-subtitle">
        This is a separate feature from claim validation above — it explains
        policy wording, but it can NEVER change a PASS/WARNING/FAIL result.
        Answers are extracted directly from the real policy document text,
        with source citations. If the answer isn't in the document, it says
        so rather than guessing.
      </p>

      <form onSubmit={handleAsk} style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <input
          style={{ flex: 1 }}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. What is the room rent limit? Does this cover cataract treatment?"
        />
        <button className="primary" type="submit" disabled={asking}>
          {asking ? 'Asking…' : 'Ask'}
        </button>
      </form>

      <ErrorBox message={error} />

      {result && (
        <div className="evidence-panel">
          <div className="financial-row">
            <span>Found evidence?</span>
            <span>{result.found ? 'Yes' : 'No — not found in the selected policy source'}</span>
          </div>
          <p style={{ marginTop: 12, whiteSpace: 'pre-wrap' }}>{result.answer}</p>

          {result.found && result.citations?.length > 0 && (
            <>
              <h2 style={{ marginTop: 16, fontSize: 14 }}>Citations</h2>
              <table>
                <thead>
                  <tr><th>Document</th><th>Page</th><th>Relevance Score</th></tr>
                </thead>
                <tbody>
                  {result.citations.map((c, i) => (
                    <tr key={i}>
                      <td>{c.document}</td>
                      <td>{c.page}</td>
                      <td>{c.relevance_score}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}

      {history.length > 1 && (
        <>
          <h2 style={{ marginTop: 24 }}>Previous Questions This Session</h2>
          <table>
            <thead><tr><th>Question</th><th>Found</th><th>Top Source</th></tr></thead>
            <tbody>
              {history.slice(1).map((h, i) => (
                <tr key={i}>
                  <td>{h.question}</td>
                  <td>{h.found ? 'Yes' : 'No'}</td>
                  <td>{h.citations?.[0] ? `${h.citations[0].document} p.${h.citations[0].page}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function FinancialImpact({ financials }) {
  return (
    <div>
      <h2>Financial Impact</h2>
      <p className="page-subtitle">Rule-based estimate — not a guaranteed insurer payout.</p>
      {!financials && <p><NotSpecified /> — no validation run has produced a financial breakdown yet.</p>}
      {financials && (
        <div className="evidence-panel">
          <FinancialRow label="Original Billed Amount" value={financials.gross_bill} />
          <FinancialRow label="Room Rent Adjustment" value={financials.room_rent_adjustment} />
          <FinancialRow label="Sub-limit Adjustment" value={financials.sub_limit_adjustment} />
          <FinancialRow label="Deductible" value={financials.deductible} />
          <FinancialRow label="Co-payment" value={financials.copay_amount} />
          <FinancialRow label="Estimated Eligible Amount" value={financials.estimated_eligible_amount} />
        </div>
      )}
    </div>
  );
}

function FinancialRow({ label, value }) {
  const isSpecified = value !== undefined && value !== null && value !== ''
    && !String(value).startsWith('NOT_COMPUTED');
  return (
    <div className="financial-row">
      <span>{label}</span>
      <span>{isSpecified ? `Rs. ${value}` : <NotSpecified />}</span>
    </div>
  );
}