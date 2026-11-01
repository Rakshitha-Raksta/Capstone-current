const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });
const num = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

export const money = (v: number | null | undefined) => (v === null || v === undefined ? '—' : inr.format(v));
export const n = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? '—' : new Intl.NumberFormat('en-IN', { maximumFractionDigits: digits }).format(v);
export const int = (v: number | null | undefined) => (v === null || v === undefined ? '—' : num.format(Math.round(v)));
export const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`;
export const ms = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v < 10 ? v.toFixed(2) : v.toFixed(0)} ms`);

export function date(v: string | null | undefined) {
  if (!v) return '—';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}
export function dateTime(v: string | null | undefined) {
  if (!v) return '—';
  const d = new Date(v.endsWith('Z') || v.includes('+') ? v : `${v}Z`);
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export const humanize = (s: string | null | undefined) =>
  !s ? '—' : s.toLowerCase().replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

export const STAGE_LABELS: Record<string, string> = {
  ingestion: 'Ingestion', data_validation: 'Data validation', discharge_readiness: 'Discharge readiness',
  billing: 'Billing verification', eligibility: 'Insurance eligibility', adjudication: 'Insurance adjudication',
  historical: 'Historical comparison', reconciliation: 'Reconciliation', root_blocker: 'Root-blocker analysis',
  review_routing: 'Review routing', claim_generation: 'Claim preparation',
};
export const stageLabel = (s: string | null | undefined) => (s ? STAGE_LABELS[s] || humanize(s) : '—');

export const DOMAIN_LABELS: Record<string, string> = {
  encounter: 'Encounter', diagnoses: 'Diagnoses', procedures: 'Procedures', discharge_note: 'Discharge note',
  documents: 'Documents', checklist: 'Discharge checklist', bill: 'Hospital bill', claim_admin: 'Claim administration',
  policy: 'Insurance policy', reviews: 'Review decisions',
};
