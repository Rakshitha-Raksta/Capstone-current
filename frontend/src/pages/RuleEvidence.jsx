import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api, ApiError } from '../services/api';
import { Loading, ErrorBox, SeverityBadge, NotSpecified } from '../components/Common';

export default function RuleEvidence() {
  const { claimId, ruleId } = useParams();
  const [validation, setValidation] = useState(null);
  const [rule, setRule] = useState(null);
  const [ruleError, setRuleError] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getValidation(claimId)
      .then(setValidation)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
    api.getRule(ruleId)
      .then(setRule)
      .catch((e) => setRuleError(e instanceof ApiError ? e.message : String(e)));
  }, [claimId, ruleId]);

  if (error) return <ErrorBox message={error} />;
  if (!validation) return <Loading label="Loading validation result…" />;

  const result = validation.results.find(r => r.rule_id === ruleId);
  if (!result) return <ErrorBox message={`Rule ${ruleId} not found in this claim's validation results.`} />;

  return (
    <div>
      <p><Link to={`/legacy/claims/${claimId}`}>&larr; Back to claim</Link></p>
      <h1>Rule Detail — {result.rule_id}</h1>

      <div className="evidence-panel">
        <div className="financial-row"><span>Category</span><span>{result.category}</span></div>
        <div className="financial-row"><span>Severity</span><span><SeverityBadge severity={result.severity} /></span></div>
        <div className="financial-row"><span>Reason</span><span>{result.reason}</span></div>
        <div className="financial-row"><span>Expected</span><span>{result.expected}</span></div>
        <div className="financial-row"><span>Actual</span><span>{result.actual}</span></div>
      </div>

      <h2>Source Evidence</h2>
      <div className="evidence-panel">
        <div className="financial-row">
          <span>Document</span>
          <span>{result.source?.document || <NotSpecified />}</span>
        </div>
        <div className="financial-row">
          <span>Page</span>
          <span>{result.source?.page || <NotSpecified />}</span>
        </div>
        <div className="financial-row">
          <span>Provenance</span>
          <span>{result.provenance || <NotSpecified />}</span>
        </div>

        <h2 style={{ marginTop: 20 }}>Extracted Source Passage</h2>
        {ruleError && (
          <ErrorBox message={`Could not load the full rule record (${ruleError}). Showing what the validation result itself provided above.`} />
        )}
        {!ruleError && !rule && <Loading label="Loading source passage…" />}
        {rule && rule.source_text && (
          <div className="evidence-passage">{rule.source_text}</div>
        )}
        {rule && !rule.source_text && (
          <p><NotSpecified /> — no extracted passage is recorded for this rule.</p>
        )}
      </div>
    </div>
  );
}
