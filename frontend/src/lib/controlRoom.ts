// Helpers for the Agent Control Room. Every state is derived from events the orchestrator recorded;
// replay positions use the recorded, measured timestamps (ms since the run started).

/* eslint-disable @typescript-eslint/no-explicit-any */
export type StageState = 'waiting' | 'ready' | 'running' | 'completed' | 'issue' | 'blocked' | 'failed' | 'reused' | 'notrun' | 'skipped';

export interface StageSnap {
  state: StageState;
  label: string;
  reason?: string;
  start?: number; end?: number; duration?: number; readyAt?: number; at?: number;
  findings?: { code: string; severity: string; title: string }[];
  attempts?: number; error?: string; outputHash?: string; reusedFrom?: number; executionId?: number;
}

export const STATE_LABEL: Record<StageState, string> = {
  waiting: 'Waiting for dependencies', ready: 'Ready', running: 'Running', completed: 'Completed', issue: 'Completed · issue found',
  blocked: 'Completed · blocking finding', failed: 'Failed', reused: 'Reused', notrun: 'Not re-run', skipped: 'Skipped',
};

export const STATE_SHORT: Record<StageState, string> = {
  waiting: 'Waiting', ready: 'Ready', running: 'Running', completed: 'Completed', issue: 'Issue found', blocked: 'Blocked',
  failed: 'Failed', reused: 'Reused', notrun: 'Not re-run', skipped: 'Skipped',
};

/** Stage snapshots at replay time `t` (ms since run start); `t = null` means "everything recorded so far". */
export function stagesAt(events: any[], stageIds: string[], t: number | null): Record<string, StageSnap> {
  const out: Record<string, StageSnap> = Object.fromEntries(stageIds.map((s) => [s, { state: 'waiting', label: STATE_LABEL.waiting }]));
  const vis = (x: number | undefined) => t === null || (x !== undefined && x <= t);
  for (const e of events) {
    const s = out[e.stage];
    if (!s) continue;
    if (e.type === 'stage_ready' && vis(e.t_ms)) { s.state = s.state === 'waiting' ? 'ready' : s.state; s.readyAt = e.t_ms; }
    else if (e.type === 'stage_started' && vis(e.t_ms)) s.reason = e.reason;
    else if (e.type === 'stage_reused' && vis(e.t_ms)) Object.assign(s, { state: 'reused', at: e.t_ms, reason: `${e.considered_because ? `${e.considered_because}; ` : ''}${e.reason}`, reusedFrom: e.reused_from_execution });
    else if (e.type === 'stage_not_rerun' && vis(e.t_ms)) Object.assign(s, { state: 'notrun', at: e.t_ms, reason: e.reason });
    else if (e.type === 'stage_skipped' && vis(e.t_ms)) Object.assign(s, { state: 'skipped', at: e.t_ms, reason: e.reason });
    else if (e.type === 'stage_finished') {
      Object.assign(s, { start: e.start_ms, end: e.end_ms, duration: e.duration_ms, attempts: e.attempts, error: e.error,
        findings: e.findings, outputHash: e.output_hash, executionId: e.execution_id });
      if (t === null || (e.end_ms !== undefined && e.end_ms <= t)) {
        s.state = e.status === 'FAILED' ? 'failed' : e.stage_status === 'BLOCKED' ? 'blocked' : e.stage_status === 'WARN' ? 'issue' : 'completed';
      } else if (e.start_ms !== undefined && e.start_ms <= t) s.state = 'running';
    }
  }
  for (const s of Object.values(out)) s.label = STATE_LABEL[s.state];
  return out;
}

export const runEnd = (events: any[]) => events.find((e) => e.type === 'run_finished');
export const lastTime = (events: any[]) => Math.max(0, ...events.map((e) => Math.max(e.t_ms || 0, e.end_ms || 0, e.commit_ms || 0)));

/** Measured milliseconds with enough precision that sub-millisecond stages are not shown as 0. */
export function fmtMs(v: number | null | undefined) {
  if (v === null || v === undefined || Number.isNaN(v)) return 'unavailable';
  if (v === 0) return '0 ms';
  if (v < 0.1) return `${v.toFixed(4)} ms`;
  if (v < 10) return `${v.toFixed(3)} ms`;
  if (v < 1000) return `${v.toFixed(1)} ms`;
  return `${(v / 1000).toFixed(2)} s`;
}

export const pctChange = (seq?: number | null, par?: number | null) =>
  seq && seq > 0 && par !== null && par !== undefined ? ((seq - par) / seq) * 100 : null;
