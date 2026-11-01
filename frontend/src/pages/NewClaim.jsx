import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../services/api';
import { Loading, ErrorBox } from '../components/Common';

const REQUIRED = ['policy_version_id', 'policy_start_date', 'admission_date'];

export default function NewClaim() {
  const navigate = useNavigate();
  const [policies, setPolicies] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [submitError, setSubmitError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({
    claim_ref: '', policy_version_id: '', patient_ref: '', date_of_birth: '',
    gender: '', policy_start_date: '', admission_date: '', discharge_date: '',
    hospital_id: '', room_type: '', room_rent_per_day: '', diagnosis_description: '',
    diagnosis_code: '', procedure_description: '', procedure_code: '', billed_amount: '',
    preauth_status: 'NONE', preauth_request_date: '', documents_submitted: '',
  });

  useEffect(() => {
    api.listPolicies()
      .then(setPolicies)
      .catch((e) => setLoadError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  const selectedPolicy = policies?.find(p =>
    p.versions.some(v => v.policy_version_id === form.policy_version_id));

  function update(field, value) {
    setForm(f => ({ ...f, [field]: value }));
  }

  function missingFields() {
    return REQUIRED.filter(f => !form[f]);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitError(null);
    const missing = missingFields();
    if (missing.length > 0) {
      setSubmitError(`Missing required field(s): ${missing.join(', ')}`);
      return;
    }
    setSubmitting(true);
    try {
      const payload = { ...form };
      // Strip empty-string optional fields so the backend doesn't receive
      // invalid empty dates/numbers where a real value wasn't entered.
      Object.keys(payload).forEach(k => {
        if (payload[k] === '') delete payload[k];
      });
      if (payload.room_rent_per_day) payload.room_rent_per_day = Number(payload.room_rent_per_day);
      if (payload.billed_amount) payload.billed_amount = Number(payload.billed_amount);
      if (!payload.claim_ref) payload.claim_ref = `CLAIM-${Date.now()}`;

      const created = await api.createClaim(payload);
      await api.validateClaim(created.claim_id);
      navigate(`/legacy/claims/${created.claim_id}`);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (loadError) return <ErrorBox message={`Failed to load policies: ${loadError}`} />;
  if (!policies) return <Loading label="Loading policy list…" />;

  return (
    <div>
      <h1>New Claim</h1>
      <p className="page-subtitle">
        This claim is a test/demo record you are creating now — it is stored
        in the database like any real claim record structurally, but is not
        a real insurer submission.
      </p>
      <ErrorBox message={submitError} />

      <form className="claim-form" onSubmit={handleSubmit}>
        <div>
          <label>Insurer / Product</label>
          <select
            value={form.policy_version_id}
            onChange={(e) => update('policy_version_id', e.target.value)}
          >
            <option value="">Select a policy version…</option>
            {policies.map(p => (
              <optgroup key={p.policy_id} label={`${p.insurer} — ${p.product}`}>
                {p.versions.map(v => (
                  <option key={v.policy_version_id} value={v.policy_version_id}>
                    {v.policy_version_id} (UIN {v.uin}){v.uin_conflict_flag ? ' — UIN CONFLICT' : ''}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <div>
          <label>Claim Reference (optional)</label>
          <input value={form.claim_ref} onChange={(e) => update('claim_ref', e.target.value)} placeholder="Auto-generated if left blank" />
        </div>

        <div>
          <label>Patient ID (synthetic)</label>
          <input value={form.patient_ref} onChange={(e) => update('patient_ref', e.target.value)} />
        </div>
        <div>
          <label>Date of Birth</label>
          <input type="date" value={form.date_of_birth} onChange={(e) => update('date_of_birth', e.target.value)} />
        </div>

        <div>
          <label>Gender</label>
          <select value={form.gender} onChange={(e) => update('gender', e.target.value)}>
            <option value="">Select…</option>
            <option value="MALE">Male</option>
            <option value="FEMALE">Female</option>
            <option value="OTHER">Other</option>
          </select>
        </div>
        <div>
          <label>Room Type</label>
          <select value={form.room_type} onChange={(e) => update('room_type', e.target.value)}>
            <option value="">Select…</option>
            <option value="SHARED">Shared</option>
            <option value="SINGLE_PRIVATE">Single Private</option>
            <option value="ANY">Any Room</option>
          </select>
        </div>

        <div>
          <label>Policy Start Date *</label>
          <input type="date" required value={form.policy_start_date} onChange={(e) => update('policy_start_date', e.target.value)} />
        </div>
        <div>
          <label>Admission Date *</label>
          <input type="date" required value={form.admission_date} onChange={(e) => update('admission_date', e.target.value)} />
        </div>

        <div>
          <label>Discharge Date</label>
          <input type="date" value={form.discharge_date} onChange={(e) => update('discharge_date', e.target.value)} />
        </div>
        <div>
          <label>Hospital ID</label>
          <input value={form.hospital_id} onChange={(e) => update('hospital_id', e.target.value)} />
        </div>

        <div>
          <label>Room Rent Per Day (Rs.)</label>
          <input type="number" value={form.room_rent_per_day} onChange={(e) => update('room_rent_per_day', e.target.value)} />
        </div>
        <div>
          <label>Billed Amount (Rs.)</label>
          <input type="number" value={form.billed_amount} onChange={(e) => update('billed_amount', e.target.value)} />
        </div>

        <div>
          <label>Diagnosis</label>
          <input value={form.diagnosis_description} onChange={(e) => update('diagnosis_description', e.target.value)} />
        </div>
        <div>
          <label>ICD Code</label>
          <input value={form.diagnosis_code} onChange={(e) => update('diagnosis_code', e.target.value)} />
        </div>

        <div>
          <label>Procedure</label>
          <input value={form.procedure_description} onChange={(e) => update('procedure_description', e.target.value)} />
        </div>
        <div>
          <label>Procedure Code</label>
          <input value={form.procedure_code} onChange={(e) => update('procedure_code', e.target.value)} />
        </div>

        <div>
          <label>Preauthorization Status</label>
          <select value={form.preauth_status} onChange={(e) => update('preauth_status', e.target.value)}>
            <option value="NONE">None</option>
            <option value="REQUESTED">Requested</option>
            <option value="APPROVED">Approved</option>
            <option value="DENIED">Denied</option>
          </select>
        </div>
        <div>
          <label>Preauthorization Date</label>
          <input type="date" value={form.preauth_request_date} onChange={(e) => update('preauth_request_date', e.target.value)} />
        </div>

        <div className="field-full">
          <label>Documents Submitted (comma-separated, e.g. claim_form,photo_id,KYC)</label>
          <textarea rows={2} value={form.documents_submitted} onChange={(e) => update('documents_submitted', e.target.value)} />
        </div>

        {selectedPolicy?.versions.find(v => v.policy_version_id === form.policy_version_id)?.uin_conflict_flag && (
          <div className="field-full">
            <ErrorBox message="Warning: the selected policy version has an unresolved UIN conflict. Rules for it will not auto-evaluate and the claim will be marked Human Review Needed. See docs/HDFC_ERGO_UIN_REVIEW.md." />
          </div>
        )}

        <div className="field-full">
          <button className="primary" type="submit" disabled={submitting}>
            {submitting ? 'Submitting…' : 'Create & Validate Claim'}
          </button>
        </div>
      </form>
    </div>
  );
}
