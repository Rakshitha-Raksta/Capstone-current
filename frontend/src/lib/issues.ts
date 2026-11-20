// Shared reading of persistent issue memory (backend: app/clearclaim/issues.py). Labels and tones are
// defined once so every view says the same thing about an issue's lifecycle.

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Lifecycle = 'NEW' | 'UNDER_REVIEW' | 'PREVIOUSLY_UNRESOLVED' | 'RESOLVED' | 'REOPENED' | 'DISMISSED' | 'NO_LONGER_DETECTED' | 'PREDICTION_ONLY';

export const LIFECYCLE_LABEL: Record<Lifecycle, string> = {
  NEW: 'New issue',
  UNDER_REVIEW: 'Already under review',
  PREVIOUSLY_UNRESOLVED: 'Previously unresolved',
  RESOLVED: 'Resolved',
  REOPENED: 'Reopened',
  DISMISSED: 'Dismissed (accepted)',
  NO_LONGER_DETECTED: 'No longer detected',
  PREDICTION_ONLY: 'Prediction only',
};

/** tone for chips (space UI) and badges (classic UI) */
export const LIFECYCLE_TONE: Record<Lifecycle, 'blocked' | 'review' | 'complete' | 'info'> = {
  NEW: 'review', UNDER_REVIEW: 'review', PREVIOUSLY_UNRESOLVED: 'blocked', REOPENED: 'blocked',
  RESOLVED: 'complete', DISMISSED: 'complete', NO_LONGER_DETECTED: 'info', PREDICTION_ONLY: 'info',
};
export const LIFECYCLE_BADGE: Record<string, string> = { blocked: 'b-blocked', review: 'b-review', complete: 'b-complete', info: 'b-info' };

export const EVENT_TEXT: Record<string, string> = {
  DETECTED: 'First detected and routed', ROUTED: 'Routed to review', ADOPTED_EXISTING_TASK: 'Linked to an existing task',
  BACKFILLED: 'Reconstructed from earlier records', TASK_REKEYED: 'Cited rules changed (same task kept)',
  EVIDENCE_UPDATED: 'Evidence refreshed on the pending task', DECISION_RECORDED: 'Reviewer decision recorded',
  DECISION_APPLIED: 'Decision applied', CORRECTION_ATTEMPTED: 'Correction applied against it', NOTE_ADDED: 'Reviewer note',
  RESOLVED: 'Resolved by a correction', NO_LONGER_DETECTED: 'No longer detected (not claimed resolved)',
  REOPENED: 'Reopened', REDETECTED_DISMISSAL_KEPT: 'Detected again; dismissal kept', NOT_DETECTED: 'Not detected (dismissal kept)',
  NOT_ROUTED_STILL_PRESENT: 'Still present, explained as a consequence', PRESENT_AS_CONSEQUENCE: 'Present again as a consequence',
};

/** issues of a case that match a root-blocker finding (same code and identity evidence) */
export function issueForFinding(issues: any[] | undefined, f: any): any | null {
  if (!issues || !f) return null;
  const ev = f.evidence || {};
  const keys = ['line_no', 'seq_num', 'item', 'document', 'phrase', 'field'];
  return issues.find((i) => i.code === f.code && keys.every((k) => (i.identity?.[k] ?? null) === (ev[k] === undefined || ev[k] === null || ev[k] === '' ? null : String(ev[k])))) || null;
}

export function lifecycleOf(i: any): Lifecycle {
  return (i?.lifecycle as Lifecycle) || 'NEW';
}

export function issueSentence(i: any): string {
  if (!i) return '';
  if (i.dedup_note) return i.dedup_note;
  const dept = i.department || 'the owning desk';
  switch (i.status) {
    case 'PENDING_REVIEW':
      return i.reopen_count > 0 ? `Reopened (occurrence ${i.reopen_count + 1}) and routed to ${dept}.` : `Routed to ${dept} for review.`;
    case 'OPEN':
      return i.review_outcome === 'CONFIRMED_BLOCKED' ? `Reviewed by ${dept} and confirmed as blocking; still present.` : 'Still present; explained as a consequence of another root, so no separate task.';
    case 'RESOLVED': {
      const a = i.resolution_action || {};
      return `Resolved: no longer detected after correction${a.correction_id ? ` #${a.correction_id}` : ''}${a.op ? ` (${a.op.replace(/_/g, ' ')})` : ''}. Not an active blocker.`;
    }
    case 'DISMISSED':
      return `Accepted with a justification by ${i.reviewer || 'a reviewer'}${i.is_present ? '; the evidence is unchanged' : '; no longer detected'}. It reopens automatically if the evidence changes materially.`;
    case 'NO_LONGER_DETECTED':
      return 'No longer detected, but no data correction was recorded, so it is not claimed as resolved.';
    default:
      return '';
  }
}
