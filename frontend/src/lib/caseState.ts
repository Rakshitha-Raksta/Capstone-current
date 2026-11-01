// Plain-language reading of a case's live state. Single source of truth for "is this claim
// complete?" so no screen can call a case complete while blockers or open tasks remain.

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Tone = 'complete' | 'blocked' | 'review' | 'info';

export interface CaseState {
  complete: boolean;
  /** status says complete but blockers or open tasks remain: never shown as complete */
  inconsistent: boolean;
  blockers: any[];
  openTasks: any[];
  tone: Tone;
  headline: string;
  sentence: string;
}

export function caseState(detail: any): CaseState {
  const status: string = detail?.case?.status || 'NEW';
  const roots: any[] = detail?.root_blocker?.roots || [];
  const blockers = roots.filter((r) => r.severity === 'BLOCKING' || r.severity === 'REVIEW');
  const openTasks: any[] = (detail?.tasks || []).filter((t: any) => t.status === 'OPEN');
  const complete = status === 'PREPARATION_COMPLETE' && blockers.length === 0 && openTasks.length === 0;
  const inconsistent = status === 'PREPARATION_COMPLETE' && !complete;
  const nBlock = blockers.filter((b) => b.severity === 'BLOCKING').length;
  const nReview = Math.max(openTasks.length, blockers.length - nBlock);

  if (complete) {
    return { complete, inconsistent, blockers, openTasks, tone: 'complete', headline: 'Preparation complete',
      sentence: 'Every automated check passed and no review is open, so the claim-preparation packet can be produced. This is an internal status, not an insurer decision.' };
  }
  if (inconsistent) {
    return { complete, inconsistent, blockers, openTasks, tone: 'review', headline: 'Not complete',
      sentence: `The stored status says complete, but ${blockers.length} blocker(s) and ${openTasks.length} open task(s) remain, so it is treated as not complete.` };
  }
  switch (status) {
    case 'BLOCKED':
      return { complete, inconsistent, blockers, openTasks, tone: 'blocked', headline: 'Blocked',
        sentence: `${nBlock || blockers.length} issue${(nBlock || blockers.length) === 1 ? '' : 's'} must be fixed (or, for a policy matter, confirmed by a reviewer) before the claim can be prepared.` };
    case 'NEEDS_REVIEW':
      return { complete, inconsistent, blockers, openTasks, tone: 'review', headline: 'Needs review',
        sentence: `${nReview} item${nReview === 1 ? '' : 's'} need${nReview === 1 ? 's' : ''} a person's decision before the claim can be prepared.` };
    case 'ERROR':
      return { complete, inconsistent, blockers, openTasks, tone: 'blocked', headline: 'Processing error',
        sentence: 'A workflow stage failed. The result is incomplete; see the workflow runs for the error.' };
    default:
      return { complete, inconsistent, blockers, openTasks, tone: 'info', headline: 'Not processed yet',
        sentence: 'This case has not been through the workflow yet.' };
  }
}

/** Short next step for a case-list row (uses the pipeline's own next-action text when present). */
export function nextStep(row: any): string {
  switch (row.status) {
    case 'PREPARATION_COMPLETE': return row.open_tasks > 0 ? 'Close the open review tasks' : 'None: packet can be produced';
    case 'NEW': return 'Process the case';
    case 'ERROR': return 'Check the failed workflow stage';
    default: return row.next_action || (row.status === 'NEEDS_REVIEW' ? 'Record a review decision' : 'Open the case');
  }
}
