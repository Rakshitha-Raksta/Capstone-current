// ClearClaim One: pure derivations from API data. Nothing here invents a state, a timing or a result;
// every value comes from the case detail, the stage registry, recorded events or the preview endpoint.
/* eslint-disable @typescript-eslint/no-explicit-any */
import { STATE_LABEL, type StageSnap, type StageState } from '../../lib/controlRoom';
import type { SceneMark } from '../../lib/marks';

export type Step = 'investigate' | 'understand' | 'correct' | 'reprocess' | 'verify';
export const STEPS: { id: Step; title: string; text: string }[] = [
  { id: 'investigate', title: 'Investigate', text: 'Find the case and its most important blocker.' },
  { id: 'understand', title: 'Understand', text: 'See why it is a blocker, the evidence, the owner and what it affects.' },
  { id: 'correct', title: 'Correct', text: 'Preview the correction and exactly which stages the dependency model will re-evaluate.' },
  { id: 'reprocess', title: 'Reprocess', text: 'The real backend applies the change and re-runs only what is affected.' },
  { id: 'verify', title: 'Verify', text: 'Compare before and after, check what was reused, and open the audit record.' },
];

/** Stage states of the latest recorded run, from the case's current stage executions. */
export function snapsFromCase(detail: any, ids: string[]): Record<string, StageSnap> {
  const out: Record<string, StageSnap> = {};
  for (const id of ids) {
    const x = detail?.stages?.[id];
    const state: StageState = !x ? 'waiting' : x.status === 'FAILED' ? 'failed'
      : x.stage_status === 'BLOCKED' ? 'blocked' : x.stage_status === 'WARN' ? 'issue' : 'completed';
    const findings = (detail?.findings || []).filter((f: any) => f.stage === id)
      .map((f: any) => ({ code: f.code, severity: f.severity, title: f.title }));
    out[id] = { state, label: STATE_LABEL[state], duration: x?.duration_ms ?? undefined, findings };
  }
  return out;
}

export function descendants(id: string, stages: any[]): string[] {
  const out = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const s = stack.pop()!;
    for (const t of stages) if (t.deps.includes(s) && !out.has(t.id)) { out.add(t.id); stack.push(t.id); }
  }
  return stages.map((s) => s.id).filter((s) => out.has(s));
}

/** Where a root blocker was raised, which findings it explains, and which stages lie downstream of it. */
export function impactOf(root: any, detail: any, stages: any[]) {
  const consequences = (detail?.root_blocker?.consequences || []).filter((c: any) => c.caused_by === root.code
    && (!c.caused_by_stage || c.caused_by_stage === root.stage));
  const down = descendants(root.stage, stages);
  return { consequences, down };
}

export function investigateMarks(root: any, detail: any, stages: any[]): Record<string, SceneMark> {
  if (!root) return {};
  const { consequences, down } = impactOf(root, detail, stages);
  const m: Record<string, SceneMark> = {};
  down.forEach((s) => { m[s] = 'downstream'; });
  consequences.forEach((c: any) => { m[c.stage] = 'consequence'; });
  m[root.stage] = 'root';
  return m;
}

export function previewMarks(preview: any): Record<string, SceneMark> {
  const m: Record<string, SceneMark> = {};
  (preview?.planned || []).forEach((s: string) => { m[s] = 'rerun'; });
  (preview?.kept || []).forEach((s: string) => { m[s] = 'kept'; });
  return m;
}

/** What actually happened in a recorded run, from its events only. */
export function runOutcome(events: any[], ids: string[]) {
  const by = (t: string) => ids.filter((s) => events.some((e) => e.type === t && e.stage === s));
  const finished = events.filter((e) => e.type === 'stage_finished');
  return {
    executed: ids.filter((s) => finished.some((e) => e.stage === s && e.status === 'SUCCEEDED')),
    failed: ids.filter((s) => finished.some((e) => e.stage === s && e.status === 'FAILED')),
    changed: ids.filter((s) => finished.some((e) => e.stage === s && e.output_changed)),
    unchanged: ids.filter((s) => finished.some((e) => e.stage === s && e.status === 'SUCCEEDED' && !e.output_changed)),
    reused: by('stage_reused'),
    notRerun: by('stage_not_rerun'),
    skipped: by('stage_skipped'),
    end: events.find((e) => e.type === 'run_finished') || null,
  };
}

const scalar = (v: any) => v === null || ['string', 'number', 'boolean'].includes(typeof v);
const show = (v: any) => (Array.isArray(v) ? `${v.length} item(s)` : v && typeof v === 'object' ? `${Object.keys(v).length} field(s)` : v);

/** Field-level differences between two persisted pipeline-run records (outputs as stored by the backend). */
export function outputDiff(before: any, after: any) {
  if (!before || !after) return [];
  const b: Record<string, any> = Object.fromEntries((before.stages || []).map((s: any) => [s.stage, s]));
  const rows: { stage: string; fields: { key: string; before: any; after: any }[] }[] = [];
  for (const s of after.stages || []) {
    const o1 = b[s.stage]?.output || {}, o2 = s.output || {};
    const keys = Array.from(new Set([...Object.keys(o1), ...Object.keys(o2)])).sort();
    const fields = keys.filter((k) => JSON.stringify(o1[k]) !== JSON.stringify(o2[k]) && !/hash|packet/i.test(k))
      .map((k) => ({ key: k, before: scalar(o1[k]) ? o1[k] : show(o1[k]), after: scalar(o2[k]) ? o2[k] : show(o2[k]) }));
    if (fields.length) rows.push({ stage: s.stage, fields });
  }
  return rows;
}

export function findingDiff(before: any, after: any) {
  const key = (f: any) => `${f.stage}|${f.code}|${JSON.stringify(f.evidence || {})}`;
  const b = new Map((before?.findings || []).map((f: any) => [key(f), f]));
  const a = new Map((after?.findings || []).map((f: any) => [key(f), f]));
  return { resolved: [...b.entries()].filter(([k]) => !a.has(k)).map(([, f]) => f),
    added: [...a.entries()].filter(([k]) => !b.has(k)).map(([, f]) => f) };
}

/** Fixed-keyword command parser (no language model). */
export type Command =
  | { kind: 'load'; hadm: string } | { kind: 'stage'; id: string } | { kind: 'blocker'; dir: 1 | -1 }
  | { kind: 'preview' } | { kind: 'apply' } | { kind: 'replay' } | { kind: 'audit' } | { kind: 'reset' }
  | { kind: 'view'; view: '3d' | 'list' } | { kind: 'help' } | { kind: 'unknown'; text: string };

export const COMMAND_HELP = [
  ['load 98000056', 'load an encounter'], ['blocker / next', 'select the next blocker'], ['stage billing', 'inspect a stage'],
  ['preview', 'preview the correction'], ['apply', 'apply the previewed correction'], ['replay', 'replay the recorded run'],
  ['audit', 'show audit evidence'], ['reset', 'reset the camera'], ['list view / 3d view', 'switch view'],
];

export function parseCommand(text: string, stages: { id: string; label?: string }[]): Command {
  const t = text.trim().toLowerCase();
  const num = t.match(/\b(\d{4,12})\b/);
  if (num && /^(load|open|case|encounter|\d)/.test(t)) return { kind: 'load', hadm: num[1] };
  if (/^(help|\?)$/.test(t)) return { kind: 'help' };
  if (/^(next|blocker|next blocker)$/.test(t)) return { kind: 'blocker', dir: 1 };
  if (/^(prev|previous|previous blocker)$/.test(t)) return { kind: 'blocker', dir: -1 };
  if (/^(preview|fix|correct|preview fix|preview correction)$/.test(t)) return { kind: 'preview' };
  if (/^(apply|apply correction|apply fix)$/.test(t)) return { kind: 'apply' };
  if (/^(replay|play)$/.test(t)) return { kind: 'replay' };
  if (/^(audit|audit trail|evidence)$/.test(t)) return { kind: 'audit' };
  if (/^(reset|reset view|home)$/.test(t)) return { kind: 'reset' };
  if (/list/.test(t) && /view|mode/.test(t)) return { kind: 'view', view: 'list' };
  if (/3d|spatial/.test(t)) return { kind: 'view', view: '3d' };
  const m = t.match(/^(?:stage|show|inspect)\s+(.+)$/);
  if (m) {
    const q = m[1].replace(/[^a-z ]/g, '');
    const hit = stages.find((s) => s.id.replace(/_/g, ' ') === q || s.id === q)
      || stages.find((s) => (s.label || '').toLowerCase().includes(q) || s.id.replace(/_/g, ' ').includes(q));
    if (hit) return { kind: 'stage', id: hit.id };
  }
  return { kind: 'unknown', text };
}

/** Plain-language description of what a stage reads and why it waits for its upstream stages. */
export function dependencyText(st: any, label: (s: string) => string, domainLabel: (d: string) => string) {
  const reads = (st.input_domains || []).map(domainLabel);
  const ups = Object.entries(st.upstream_inputs || {}).map(([k, v]: [string, any]) => {
    const src = k.split('#')[0];
    const what = v === '*' ? 'its full result' : v === 'findings' ? 'its findings' : (v as string[]).join(', ');
    return `${label(src)} (${what})`;
  });
  return { reads, ups, deps: (st.deps || []).map(label) };
}
