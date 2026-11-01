import { useEffect, useMemo, useState } from 'react';
import { api } from '../../lib/api';
import { DOMAIN_LABELS, stageLabel } from '../../lib/format';
import { Button, Dialog, Field, useToast } from '../../components/ui';

/* eslint-disable @typescript-eslint/no-explicit-any */
type Draft = { op: string; payload: any; fields: { key: string; label: string; type: string; help?: string }[] };

/** Build an editable correction draft from a finding's own evidence (all values come from the API). */
export function draftFor(f: any, detail: any): Draft | null {
  const ev = f?.evidence || {};
  const lineRes = (detail.billing?.line_results || []).find((l: any) => l.line_no === ev.line_no);
  switch (f?.code) {
    case 'BILL_ARITHMETIC_ERROR':
      return { op: 'update_bill_line', payload: { line_no: ev.line_no, fields: { amount: Number(f.expected) } },
        fields: [{ key: 'fields.amount', label: `Line ${ev.line_no} amount (INR)`, type: 'number', help: `Recorded ${f.actual}; quantity × unit price = ${f.expected}` }] };
    case 'UNIT_PRICE_ABOVE_TARIFF': {
      const qty = lineRes?.quantity ?? 1;
      const price = Number(f.expected);
      return { op: 'update_bill_line', payload: { line_no: ev.line_no, fields: { unit_price: price, amount: qty * price } },
        fields: [{ key: 'fields.unit_price', label: `Line ${ev.line_no} unit price (INR)`, type: 'number', help: `Rate card ${f.expected}; recorded ${f.actual}` },
          { key: 'fields.amount', label: 'Line amount (INR)', type: 'number', help: `quantity ${qty}` }] };
    }
    case 'DUPLICATE_BILL_ITEM':
      return { op: 'delete_bill_line', payload: { line_no: ev.line_no }, fields: [] };
    case 'BILLED_PROCEDURE_NOT_DOCUMENTED':
      return { op: 'delete_bill_line', payload: { line_no: ev.line_no }, fields: [] };
    case 'LOS_BILLING_MISMATCH': {
      const los = detail.data_validation?.los_days;
      const bed = detail.billing?.bed_days;
      const qty = lineRes?.quantity ?? 0;
      const newQty = los != null && bed != null ? Math.max(0, qty - (bed - los)) : qty;
      return { op: 'update_bill_line', payload: { line_no: ev.line_no, fields: { quantity: newQty, amount: newQty * (lineRes?.unit_price ?? 0) } },
        fields: [{ key: 'fields.quantity', label: `Line ${ev.line_no} (${ev.item_code}) days`, type: 'number', help: `Length of stay ${los} day(s); ${bed} bed-day(s) billed in total` },
          { key: 'fields.amount', label: 'Line amount (INR)', type: 'number' }] };
    }
    case 'MISSING_CHARGE':
      return { op: 'add_bill_line', payload: { line: { item_code: `PROC-${ev.icd_code}`, quantity: 1, unit_price: f.amount ?? 0,
        amount: f.amount ?? 0, linked_icd_code: ev.icd_code, category: 'PROCEDURE', description: `Procedure ${ev.icd_code}`,
        service_date: (detail.encounter?.admittime || '').slice(0, 10) } },
        fields: [{ key: 'line.unit_price', label: 'Unit price (INR)', type: 'number', help: 'Rate-card price for the documented procedure' },
          { key: 'line.amount', label: 'Line amount (INR)', type: 'number' }] };
    case 'RECONCILIATION_RESIDUAL':
      return { op: 'set_bill_header_total', payload: { value: 'SUM_OF_LINES' }, fields: [] };
    case 'MISSING_CLAIM_DOCUMENT':
    case 'MISSING_KYC_DOCUMENT':
      return { op: 'add_document', payload: { document: ev.document }, fields: [{ key: 'document', label: 'Document received', type: 'text' }] };
    case 'READINESS_ITEM_INCOMPLETE':
      return { op: 'complete_checklist_item', payload: { item: ev.item }, fields: [] };
    case 'INVALID_ICD_CODE':
      return { op: 'update_diagnosis', payload: { seq_num: ev.seq_num, icd_code: '' },
        fields: [{ key: 'icd_code', label: `Valid ICD-10-CM code for diagnosis #${ev.seq_num}`, type: 'text', help: `Currently ${ev.icd_code} (not billable). Enter the fully specified code.` }] };
    case 'GENDER_DX_CONFLICT':
      return { op: 'delete_diagnosis', payload: { seq_num: ev.seq_num }, fields: [] };
    case 'NOTE_DX_NOT_CODED':
      return { op: 'add_diagnosis', payload: { icd_code: '' },
        fields: [{ key: 'icd_code', label: `ICD-10-CM code for “${ev.phrase}”`, type: 'text', help: 'Coder confirms the documented diagnosis' }] };
    case 'CODED_DX_NOT_IN_NOTE':
      return { op: 'amend_note_add_diagnosis', payload: { text: f.expected || '' },
        fields: [{ key: 'text', label: 'Diagnosis text to add to the note', type: 'text' }] };
    case 'MISSING_DISCHARGE_TIME':
      return { op: 'set_discharge_time', payload: { value: '' },
        fields: [{ key: 'value', label: 'Discharge date and time', type: 'datetime-local' }] };
    case 'PROCEDURE_DATE_OUT_OF_RANGE':
      return { op: 'update_procedure', payload: { seq_num: ev.seq_num, chartdate: '' },
        fields: [{ key: 'chartdate', label: `Procedure #${ev.seq_num} date`, type: 'date', help: `Admission ${f.expected}` }] };
    default:
      return null;
  }
}

function getPath(o: any, path: string) { return path.split('.').reduce((a, k) => (a == null ? a : a[k]), o); }
function setPath(o: any, path: string, v: any) {
  const c = JSON.parse(JSON.stringify(o));
  const ks = path.split('.');
  let cur = c;
  ks.slice(0, -1).forEach((k) => { cur = cur[k]; });
  cur[ks[ks.length - 1]] = v;
  return c;
}

export default function CorrectionDialog({ open, onClose, finding, detail, taskId, onApplied }: {
  open: boolean; onClose: () => void; finding: any; detail: any; taskId?: number; onApplied: (res: any) => void;
}) {
  const toast = useToast();
  const base = useMemo(() => draftFor(finding, detail), [finding, detail]);
  const [payload, setPayload] = useState<any>(base?.payload);
  const [advanced, setAdvanced] = useState(!base);
  const [op, setOp] = useState(base?.op || detail.correction_ops?.[0] || '');
  const [raw, setRaw] = useState(JSON.stringify(base?.payload || {}, null, 2));
  const [reason, setReason] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  useEffect(() => {
    setPayload(base?.payload); setOp(base?.op || detail.correction_ops?.[0] || ''); setAdvanced(!base);
    setRaw(JSON.stringify(base?.payload || {}, null, 2)); setReason(''); setErr(null);
  }, [base, open, detail.correction_ops]);

  const root = (detail.root_blocker?.roots || []).find((r: any) => r.code === finding?.code);
  const submit = async () => {
    setErr(null);
    let body: any;
    try {
      body = advanced ? { op, payload: JSON.parse(raw) } : { op: base!.op, payload };
    } catch { setErr('Payload is not valid JSON.'); return; }
    if (body.op === 'set_discharge_time' && body.payload.value && body.payload.value.length === 16) body.payload.value += ':00';
    if (reason.trim().length < 3) { setErr('Please record a reason (at least 3 characters).'); return; }
    setBusy(true);
    try {
      const res = await api.correct(detail.case.id, { ...body, reason: reason.trim(), review_task_id: taskId, expected_version: detail.case.version });
      toast(`Correction applied: ${res.run.stages_executed} stage(s) re-executed, ${res.run.stages_reused} reused.`);
      onApplied(res);
      onClose();
    } catch (e) {
      setErr((e as Error).message);
      setConflict((e as { status?: number }).status === 409);
    } finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onClose={onClose} title={`Correct: ${finding?.title || 'case data'}`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" busy={busy} onClick={submit}>Apply and re-run affected stages</Button></>}>
      {root && (
        <div className="callout small">
          This changes <strong>{DOMAIN_LABELS[root.owner_domain] || root.owner_domain}</strong>. Stages that read it will be re-checked:{' '}
          {root.rerun_stages.map((s: string) => stageLabel(s)).join(' → ')}. Stages whose inputs are unchanged are reused.
        </div>
      )}
      {!advanced && base && (
        <>
          <div className="small muted">Operation: <span className="mono">{base.op}</span>{base.fields.length === 0 && ' — no values needed.'}</div>
          {base.fields.map((fl) => (
            <Field key={fl.key} label={fl.label} help={fl.help} htmlFor={`corr-${fl.key}`}>
              <input id={`corr-${fl.key}`} className="input" type={fl.type} value={getPath(payload, fl.key) ?? ''}
                onChange={(e) => setPayload(setPath(payload, fl.key, fl.type === 'number' ? Number(e.target.value) : e.target.value))} required />
            </Field>
          ))}
        </>
      )}
      {advanced && (
        <>
          <Field label="Operation" htmlFor="corr-op">
            <select id="corr-op" className="select" value={op} onChange={(e) => setOp(e.target.value)}>
              {(detail.correction_ops || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
            </select>
          </Field>
          <Field label="Payload (JSON)" htmlFor="corr-raw" help="Validated by the server before anything changes.">
            <textarea id="corr-raw" className="textarea mono" rows={6} value={raw} onChange={(e) => setRaw(e.target.value)} />
          </Field>
        </>
      )}
      {base && <Button variant="ghost" size="sm" onClick={() => setAdvanced((v) => !v)}>{advanced ? 'Use guided form' : 'Advanced: edit operation'}</Button>}
      <Field label="Reason for correction (recorded in the audit trail)" htmlFor="corr-reason">
        <textarea id="corr-reason" className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} required />
      </Field>
      {err && <div className="error-state" role="alert">{err}
        {conflict && <div style={{ marginTop: 6 }}><Button size="sm" onClick={() => window.location.reload()}>Reload case</Button></div>}</div>}
    </Dialog>
  );
}
