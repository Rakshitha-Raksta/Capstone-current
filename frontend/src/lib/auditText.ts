// Turns raw audit events (as stored by the backend) into sentences a non-technical reviewer can
// read. Only fields the backend actually records are used; unknown event types fall back to the
// stored summary.
import { DOMAIN_LABELS, humanize, stageLabel } from './format';

/* eslint-disable @typescript-eslint/no-explicit-any */
export type AuditKind = 'correction' | 'decision' | 'run' | 'status' | 'task' | 'failure' | 'system';

export interface AuditStory {
  kind: AuditKind;
  title: string;
  text: string;
  changes?: { field: string; before: string; after: string }[];
  /** routine bookkeeping that is hidden in the "key changes" view */
  routine: boolean;
}

const STATUS = (s: string) => ({
  PREPARATION_COMPLETE: 'Preparation complete', NEEDS_REVIEW: 'Needs review', BLOCKED: 'Blocked',
  PROCESSING: 'Processing', NEW: 'New', ERROR: 'Error',
} as Record<string, string>)[s] || humanize(s);

const OP_TEXT: Record<string, string> = {
  update_bill_line: 'Changed a bill line', delete_bill_line: 'Removed a bill line', add_bill_line: 'Added a bill line',
  set_bill_header_total: 'Set the bill header total', update_diagnosis: 'Changed a diagnosis code',
  delete_diagnosis: 'Removed a diagnosis code', add_diagnosis: 'Added a diagnosis code',
  update_procedure: 'Changed a procedure date', set_discharge_time: 'Recorded the discharge time',
  add_document: 'Recorded a received document', complete_checklist_item: 'Completed a discharge checklist item',
  amend_note_add_diagnosis: 'Amended the discharge note',
};

const fmt = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));

export function changedFields(before: any, after: any) {
  if (!before || !after || typeof before !== 'object' || typeof after !== 'object') return undefined;
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
  const out = keys.filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
    .map((k) => ({ field: k === 'is_deleted' ? 'removed from bill' : humanize(k), before: k === 'is_deleted' ? (before[k] ? 'yes' : 'no') : fmt(before[k]),
      after: k === 'is_deleted' ? (after[k] ? 'yes' : 'no') : fmt(after[k]) }));
  return out.length ? out : undefined;
}

export function describe(e: any): AuditStory {
  const d = e.details || {};
  const s: string = e.summary || '';
  switch (e.event_type) {
    case 'CORRECTION_APPLIED': {
      const [opPart, reason] = s.split(': ');
      const [op, , domain] = (opPart || '').split(' ');
      return { kind: 'correction', routine: false, title: OP_TEXT[op] || 'Data corrected',
        text: `${DOMAIN_LABELS[domain] || humanize(domain)} corrected by ${e.actor}.${reason ? ` Reason given: “${reason}”.` : ''}`,
        changes: changedFields(d.before, d.after) };
    }
    case 'REVIEW_DECISION': {
      const [decision, code] = s.split(' on ');
      return { kind: 'decision', routine: false, title: `Reviewer decision: ${humanize(decision)}`,
        text: `${e.actor} decided “${humanize(decision)}” for ${humanize(code)}.${d.note ? ` Justification: “${d.note}”.` : ''}` };
    }
    case 'PIPELINE_STARTED': {
      const planned: string[] = d.planned_stages || [];
      const trigger = s.split(' run')[0];
      return { kind: 'run', routine: true, title: 'Workflow run started',
        text: `Triggered by ${humanize(trigger).toLowerCase()}${d.changed_domains?.length ? ` (changed: ${d.changed_domains.map((x: string) => DOMAIN_LABELS[x] || x).join(', ')})` : ''}. Stages to re-check: ${planned.length ? planned.map(stageLabel).join(' → ') : 'all'}.` };
    }
    case 'PIPELINE_FINISHED': {
      const m = s.match(/^(\w+): (\d+) stage\(s\) executed, (\d+) reused, outcome (\w+)/);
      const changed: string[] = d.changed_outputs || [];
      if (!m) return { kind: 'run', routine: false, title: 'Workflow run finished', text: s };
      const ok = m[1] === 'SUCCEEDED';
      return { kind: ok ? 'run' : 'failure', routine: false, title: ok ? 'Workflow run finished' : 'Workflow run did not succeed',
        text: `${m[2]} stage(s) re-executed and ${m[3]} reused unchanged. Result: ${STATUS(m[4])}.${changed.length ? ` Outputs that changed: ${changed.map(stageLabel).join(', ')}.` : ' No stage output changed.'}${d.wall_ms != null ? ` Software time ${d.wall_ms} ms.` : ''}` };
    }
    case 'STAGE_FAILED':
      return { kind: 'failure', routine: false, title: 'A workflow stage failed', text: s };
    case 'CASE_STATUS_CHANGED': {
      const [a, b] = s.split(' -> ');
      // every run passes through PROCESSING; the run's own "finished" event already states the outcome
      return a === 'PROCESSING'
        ? { kind: 'status', routine: true, title: 'Case status set', text: `${STATUS(b)} after processing.` }
        : { kind: 'status', routine: false, title: 'Case status changed', text: `${STATUS(a)} → ${STATUS(b)}` };
    }
    case 'REVIEW_TASK_CREATED': {
      const title = s.split(': ').slice(1).join(': ') || s;
      return { kind: 'task', routine: true, title: 'Review task opened',
        text: `${title}${d.owner_stage ? ` (owner: ${stageLabel(d.owner_stage)})` : ''}.` };
    }
    case 'REVIEW_TASK_RESOLVED':
      return { kind: 'task', routine: false, title: d.status === 'RESOLVED_BY_CORRECTION' ? 'Review task closed by the correction' : 'Review task closed',
        text: `${humanize(s.split(' ')[0])} no longer appears after the re-run, so its task was closed automatically.` };
    default:
      return { kind: 'system', routine: false, title: humanize(e.event_type), text: s };
  }
}
