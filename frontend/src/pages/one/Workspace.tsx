import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { DOMAIN_LABELS, money, stageLabel } from '../../lib/format';
import { fmtMs, lastTime, stagesAt, STATE_LABEL, type StageState } from '../../lib/controlRoom';
import { Badge, Button, ErrorState } from '../../components/ui';
import SpatialScene from '../../components/SpatialScene';
import { MARK_TEXT, type SceneMark } from '../../lib/marks';
import { draftFor } from '../cc/CorrectionDialog';
import { ReplayBar, useJob, useReplay } from '../cc/controlRoomParts';
import {
  COMMAND_HELP, dependencyText, findingDiff, investigateMarks, outputDiff, parseCommand, previewMarks, runOutcome,
  snapsFromCase, STEPS, type Step,
} from './model';

/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * ClearClaim One: a single workspace for INVESTIGATE → UNDERSTAND → CORRECT → REPROCESS → VERIFY.
 * A. central workflow view (3D or list), B. contextual investigation panel, C. action bar + explanation bar.
 * Every state, timing and result comes from the backend: the latest recorded stage executions, the
 * orchestrator's dependency model (preview endpoint), and the events recorded while the correction ran.
 */

type RunState = {
  kind: 'correction' | 'decision'; status: 'running' | 'finished' | 'rejected' | 'error'; events: any[]; error?: string;
  runId?: number; before: any; after?: any; beforeRun?: any; afterRun?: any; body?: any; result?: any;
};
type LogEntry = { t: number; action: string; detail?: Record<string, unknown> };

function getPath(o: any, path: string) { return path.split('.').reduce((a, k) => (a == null ? a : a[k]), o); }
function setPath(o: any, path: string, v: any) {
  const c = JSON.parse(JSON.stringify(o));
  const ks = path.split('.');
  let cur = c;
  ks.slice(0, -1).forEach((k) => { cur = cur[k]; });
  cur[ks[ks.length - 1]] = v;
  return c;
}
const domainLabel = (d: string) => DOMAIN_LABELS[d] || d;
/** Replace stage ids in backend sentences with their readable names. */
const readable = (t: string) => (t || '').replace(
  /\b(ingestion|data_validation|discharge_readiness|billing|eligibility|adjudication|historical|reconciliation|root_blocker|review_routing|claim_generation)\b/g,
  (m) => stageLabel(m).toLowerCase());
const sevClass = (s: string) => `sev ${s}`;

export default function Workspace() {
  const [sp, setSp] = useSearchParams();
  const reg = useApi<any>(() => api.crStages());
  const attention = useApi<any>(() => api.cases({ status: 'BLOCKED,NEEDS_REVIEW', sort: 'priority', order: 'desc', page_size: 8 }));
  const [view, setView] = useState<'3d' | 'list'>(sp.get('view') === 'list' ? 'list' : '3d');
  const [hadmInput, setHadmInput] = useState(sp.get('encounter') || '');
  const [enc, setEnc] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [lastRun, setLastRun] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [rootIdx, setRootIdx] = useState(0);
  const [stage, setStage] = useState<string | null>(null);
  const [focus, setFocus] = useState<'blocker' | 'stage'>('blocker');
  const [mode, setMode] = useState<'investigate' | 'correct' | 'run'>('investigate');
  const [preview, setPreview] = useState<any>(null);
  const [payload, setPayload] = useState<any>(null);
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');
  const [formErr, setFormErr] = useState<string | null>(null);
  const [run, setRun] = useState<RunState | null>(null);
  const [cons, setCons] = useState<any>(null);
  const [consErr, setConsErr] = useState<string | null>(null);
  const consJob = useJob();
  const [auditEv, setAuditEv] = useState<any[] | null>(null);
  const [chain, setChain] = useState<any>(null);
  const [cmd, setCmd] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const cmdRef = useRef<HTMLInputElement>(null);
  const t0 = useRef(performance.now());
  const [log, setLog] = useState<LogEntry[]>([]);
  const note_ = useCallback((action: string, d?: Record<string, unknown>) => {
    setLog((l) => [...l, { t: Math.round(performance.now() - t0.current), action, detail: d }]);
  }, []);

  const stages: any[] = useMemo(() => reg.data?.stages || [], [reg.data]);
  const ids: string[] = useMemo(() => stages.map((s) => s.id), [stages]);
  const roots: any[] = detail?.root_blocker?.roots || [];
  const root = roots[rootIdx] || null;
  const draft = useMemo(() => (root && detail && root.correctable ? draftFor(root, detail) : null), [root, detail]);
  const task = root && detail ? detail.tasks.find((t: any) => t.status === 'OPEN' && t.code === root.code) : null;
  const busy = run?.status === 'running' || consJob.job?.status === 'running' || consJob.job?.status === 'queued' || consJob.job?.status === 'starting';

  // ---------------- load an encounter ----------------
  const load = useCallback(async (h: string) => {
    const hadm = h.trim();
    if (!/^\d{1,12}$/.test(hadm)) { setLoadErr('An Encounter ID is digits only, for example 98000056.'); return; }
    setLoading(true); setLoadErr(null);
    try {
      const e = await api.studioEncounter(hadm);
      const d = await api.caseDetail(e.case.id);
      const r = e.last_run ? await api.studioPipelineRun(e.last_run.id) : null;
      setEnc(e); setDetail(d); setLastRun(r); setHadmInput(hadm);
      setRootIdx(0); setStage(d.root_blocker?.roots?.[0]?.stage || null); setFocus('blocker');
      setMode('investigate'); setPreview(null); setRun(null); setCons(null); setConsErr(null); setAuditEv(null); setChain(null);
      setSp((p) => { const n = new URLSearchParams(p); n.set('encounter', hadm); return n; }, { replace: true });
      note_('load_case', { status: d.case.status, roots: (d.root_blocker?.roots || []).length });
    } catch (err) {
      setLoadErr((err as Error).message);
    } finally { setLoading(false); }
  }, [setSp, note_]);

  // first view: the URL's encounter, otherwise the highest-priority blocked case
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    const fromUrl = sp.get('encounter');
    if (fromUrl) { started.current = true; load(fromUrl); return; }
    const first = attention.data?.items?.find((c: any) => c.status === 'BLOCKED') || attention.data?.items?.[0];
    if (first) { started.current = true; load(String(first.hadm_id)); }
  }, [attention.data, sp, load]);

  // ---------------- selection ----------------
  const selectBlocker = (i: number) => {
    if (!roots[i] || busy) return;
    setRootIdx(i); setStage(roots[i].stage); setFocus('blocker'); setMode('investigate'); setPreview(null); setCons(null); setConsErr(null);
    note_('select_blocker', { code: roots[i].code, stage: roots[i].stage });
  };
  const selectStage = (id: string | null) => {
    setStage(id); setFocus(id ? 'stage' : 'blocker');
    if (id) note_('select_stage', { stage: id });
  };

  // ---------------- correct: preview ----------------
  const openPreview = async () => {
    if (!root) return;
    if (!draft) { setMsg('This blocker has no data correction (for example a policy waiting period). Record a review decision instead.'); setMode('correct'); return; }
    setPayload(draft.payload); setReason(''); setFormErr(null); setCons(null); setConsErr(null);
    try {
      const p = await api.workspacePreview(draft.op);
      setPreview(p); setMode('correct'); setStage(root.stage); setFocus('blocker');
      note_('open_preview', { op: draft.op, planned: p.planned.length });
    } catch (e) { setMsg((e as Error).message); }
  };

  const correctionBody = () => {
    if (!draft || !detail) return null;
    const pl = JSON.parse(JSON.stringify(payload ?? draft.payload));
    if (draft.op === 'set_discharge_time' && pl.value && pl.value.length === 16) pl.value += ':00';
    const missing = draft.fields.filter((f) => { const v = getPath(pl, f.key); return v === '' || v === null || v === undefined || Number.isNaN(v); });
    if (missing.length) { setFormErr(`Enter: ${missing.map((f) => f.label).join(', ')}.`); return null; }
    if (reason.trim().length < 3) { setFormErr('Record a reason (at least 3 characters); it is stored in the audit trail.'); return null; }
    setFormErr(null);
    return { op: draft.op, payload: pl, reason: reason.trim(), review_task_id: task?.id, expected_version: detail.case.version };
  };

  // optional: compare full vs targeted re-run on two isolated database copies (live data unchanged)
  const checkConsistency = () => {
    const body = correctionBody();
    if (!body || !enc) return;
    setCons(null); setConsErr(null); note_('consistency_check');
    consJob.start(() => api.crCorrection({ hadm_id: String(enc.case.hadm_id), correction: body }), async (rid, j) => {
      if (rid) setCons(await api.crRecord(rid)); else setConsErr(j.error || 'The check did not complete.');
    });
  };

  // ---------------- reprocess: apply through the real backend ----------------
  const apply = async () => {
    const body = correctionBody();
    if (!body || !enc) return;
    note_('apply', { op: body.op });
    const before = detail, beforeRun = lastRun;
    setMode('run'); setRun({ kind: 'correction', status: 'running', events: [], before, beforeRun, body });
    try {
      const { token } = await api.studioStart({ hadm_id: String(enc.case.hadm_id), mode: 'correction', correction: body });
      let after = 0, events: any[] = [];
      for (;;) {
        const j = await api.studioRun(token, after);
        events = [...events, ...j.events]; after = j.next_after;
        setRun((r) => r && { ...r, events });
        if (j.status === 'queued' || j.status === 'running') { await new Promise((res) => setTimeout(res, 100)); continue; }
        if (j.status !== 'finished') {
          setRun((r) => r && { ...r, status: j.status === 'rejected' ? 'rejected' : 'error', error: j.error });
          return;
        }
        const [e2, d2, r2] = await Promise.all([api.studioEncounter(String(enc.case.hadm_id)), api.caseDetail(enc.case.id), api.studioPipelineRun(j.run_id)]);
        setEnc(e2); setDetail(d2); setLastRun(r2);
        setRun((r) => r && { ...r, status: 'finished', runId: j.run_id, after: d2, afterRun: r2 });
        setAuditEv(null); setChain(null);
        replay.setT(0); replay.setPlaying(true);
        note_('run_finished', { status: d2.case.status });
        return;
      }
    } catch (e) {
      setRun((r) => r && { ...r, status: 'error', error: (e as Error).message });
    }
  };

  // review decision for blockers that no data correction can resolve
  const decide = async (decision: string) => {
    if (!task || !detail || !enc) return;
    if (note.trim().length < 5) { setFormErr('Write a justification of at least 5 characters (stored in the audit trail).'); return; }
    setFormErr(null); note_('decision', { decision });
    const before = detail, beforeRun = lastRun;
    setMode('run'); setRun({ kind: 'decision', status: 'running', events: [], before, beforeRun });
    try {
      const res = await api.decide(task.id, { decision, note: note.trim() });
      const [e2, d2, r2] = await Promise.all([api.studioEncounter(String(enc.case.hadm_id)), api.caseDetail(enc.case.id), api.studioPipelineRun(res.run.id)]);
      setEnc(e2); setDetail(d2); setLastRun(r2);
      setRun((r) => r && { ...r, status: 'finished', runId: res.run.id, after: d2, afterRun: r2, result: res });
    } catch (e) { setRun((r) => r && { ...r, status: 'error', error: (e as Error).message }); }
  };

  const openAudit = async () => {
    if (!detail) return;
    note_('open_audit');
    const [ev, v] = await Promise.all([api.caseEvents(detail.case.id), api.auditVerify()]);
    setAuditEv(ev); setChain(v);
  };

  // ---------------- what the workflow view shows ----------------
  const total = useMemo(() => lastTime(run?.events || []), [run?.events]);
  const replay = useReplay(total || 1);
  const snaps = useMemo(() => {
    if (run && run.events.length) return stagesAt(run.events, ids, run.status === 'running' ? null : replay.t);
    return snapsFromCase(detail, ids);
  }, [run, ids, replay.t, detail]);
  const marks: Record<string, SceneMark> = useMemo(() => {
    if (mode === 'correct' && preview) return previewMarks(preview);
    if (mode === 'investigate' && root) return investigateMarks(root, detail, stages);
    return {};
  }, [mode, preview, root, detail, stages]);
  const outcome = useMemo(() => runOutcome(run?.events || [], ids), [run?.events, ids]);
  const step: Step = !detail ? 'investigate' : mode === 'run' ? (run?.status === 'finished' ? 'verify' : 'reprocess')
    : mode === 'correct' ? 'correct' : focus === 'blocker' && root ? 'understand' : 'investigate';
  const sourceTag = run && run.events.length
    ? run.status === 'running' ? 'Executing on the backend now (recorded events arriving)'
      : replay.t !== null ? `REPLAY of recorded events · ${replay.slow}× slower than real` : 'Final recorded state of the correction run'
    : detail ? 'Latest recorded run of this case' : 'Workflow structure (no case loaded)';

  // ---------------- commands and keyboard ----------------
  const runCommand = (text: string) => {
    const c = parseCommand(text, stages);
    note_('command', { kind: c.kind });
    setMsg(null); setHelp(false);
    switch (c.kind) {
      case 'load': load(c.hadm); break;
      case 'blocker': if (roots.length) selectBlocker((rootIdx + c.dir + roots.length) % roots.length); else setMsg('This case has no root blocker.'); break;
      case 'stage': selectStage(c.id); break;
      case 'preview': openPreview(); break;
      case 'apply': if (mode === 'correct' && preview) apply(); else setMsg('Preview a correction first; nothing is changed until you apply it.'); break;
      case 'replay': if (run?.events.length) replay.play(); else setMsg('There is no recorded correction run to replay yet.'); break;
      case 'audit': openAudit(); break;
      case 'reset': selectStage(null); break;
      case 'view': setView(c.view); break;
      case 'help': setHelp(true); break;
      default: setMsg(`Not a supported command: “${c.text}”. Type help for the list.`);
    }
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) { if (e.key === 'Escape') (e.target as HTMLElement).blur(); return; }
      if (e.key === '/') { e.preventDefault(); cmdRef.current?.focus(); }
      else if (e.key === 'Escape') selectStage(null);
      else if (e.key === 'n' && roots.length) selectBlocker((rootIdx + 1) % roots.length);
      else if ((e.key === ']' || e.key === '[') && ids.length) {
        const i = stage ? ids.indexOf(stage) : -1;
        selectStage(ids[(i + (e.key === ']' ? 1 : ids.length - 1) + (i < 0 && e.key === '[' ? 1 : 0)) % ids.length]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
  useEffect(() => { setSp((p) => { const n = new URLSearchParams(p); if (view === 'list') n.set('view', 'list'); else n.delete('view'); return n; }, { replace: true }); note_('view', { view }); }, [view, setSp, note_]);

  const exportLog = () => {
    const blob = new Blob([JSON.stringify({ tool: 'ClearClaim One', condition: view, entries: log }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = 'clearclaim-one-session.json'; a.click();
    URL.revokeObjectURL(a.href);
  };

  // =====================================================================================
  return (
    <div className="one" data-step={step}>
      {/* ---------- C1: action bar ---------- */}
      <div className="one-bar" role="region" aria-label="Actions">
        <div className="one-brand"><span className="one-mark" aria-hidden="true" />ClearClaim One</div>
        <form className="one-enc" noValidate onSubmit={(e) => { e.preventDefault(); load(hadmInput); }}>
          <label htmlFor="one-hadm">Encounter</label>
          <input id="one-hadm" className="input mono" inputMode="numeric" maxLength={12} value={hadmInput} list="one-sugg"
            aria-invalid={!!loadErr} aria-describedby={loadErr ? 'one-err' : undefined} onChange={(e) => setHadmInput(e.target.value)} />
          <datalist id="one-sugg">{(attention.data?.items || []).map((c: any) => <option key={c.id} value={String(c.hadm_id)}>{`${c.status} · ${c.root_blocker_title || 'review'}`}</option>)}</datalist>
          <Button type="submit" size="sm" busy={loading} disabled={busy}>Load</Button>
        </form>
        <form className="one-cmd" onSubmit={(e) => { e.preventDefault(); if (cmd.trim()) runCommand(cmd); setCmd(''); }}>
          <label htmlFor="one-cmd" className="visually-hidden">Command</label>
          <input id="one-cmd" ref={cmdRef} className="input" placeholder="Command — e.g. next, preview, stage billing, help  ( / )" value={cmd}
            onChange={(e) => setCmd(e.target.value)} disabled={busy} />
        </form>
        <div className="view-switch" role="group" aria-label="Workflow view">
          <button type="button" aria-pressed={view === '3d'} onClick={() => setView('3d')}>3D</button>
          <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>List</button>
        </div>
      </div>
      {(loadErr || msg || help) && (
        <div className="one-msg" role={loadErr ? 'alert' : 'status'}>
          {loadErr && <span id="one-err">{loadErr}</span>}
          {msg && <span>{msg}</span>}
          {help && <span>Commands use fixed keywords (no AI): {COMMAND_HELP.map(([c, d]) => <span key={c} className="one-kbd" title={d}>{c}</span>)}
            · Keys: <span className="one-kbd">/</span> command, <span className="one-kbd">n</span> next blocker, <span className="one-kbd">[ ]</span> stages, <span className="one-kbd">Esc</span> clear</span>}
          <button type="button" className="one-x" aria-label="Dismiss message" onClick={() => { setLoadErr(null); setMsg(null); setHelp(false); }}>✕</button>
        </div>)}

      <div className="one-body">
        {/* ---------- A: workflow environment ---------- */}
        <section className="one-stage" aria-label="Workflow">
          {reg.error && <ErrorState message={reg.error} onRetry={reg.reload} />}
          {reg.data && view === '3d' && (
            <SpatialScene stages={stages} waves={reg.data.waves} snaps={snaps} selected={focus === 'stage' ? stage : null} marks={marks}
              onSelect={(id) => selectStage(focus === 'stage' && stage === id ? null : id)} playing={replay.playing} height="100%"
              label="Three-dimensional workflow: the 11 rule-based stages and their real dependencies. Select a stage to inspect it."
              overlay={<SceneTag text={sourceTag} marks={marks} />} />)}
          {reg.data && view === 'list' && (
            <ListView stages={stages} waves={reg.data.waves} snaps={snaps} marks={marks} selected={focus === 'stage' ? stage : null} onSelect={(id: string) => selectStage(focus === 'stage' && stage === id ? null : id)} sourceTag={sourceTag} />)}
        </section>

        {/* ---------- B: contextual investigation panel ---------- */}
        <aside className="one-panel" aria-label="Investigation">
          {!detail && !loading && !loadErr && (
            <div className="one-card"><h2>Start an investigation</h2>
              <p className="small">Load an encounter, or pick one that needs attention.</p>
              <ul className="one-list">{(attention.data?.items || []).map((c: any) => (
                <li key={c.id}><button type="button" className="one-row" onClick={() => load(String(c.hadm_id))}>
                  <span className="mono">{c.hadm_id}</span><Badge value={c.status} /><span className="small">{c.root_blocker_title || 'Needs review'}</span></button></li>))}</ul>
            </div>)}
          {loading && <div className="one-card" role="status">Loading the case and its latest recorded run…</div>}

          {detail && (<>
            <CaseHeader detail={detail} />
            {focus === 'stage' && stage && (
              <StageCard st={stages.find((s) => s.id === stage)} idx={ids.indexOf(stage)} snap={snaps[stage]} detail={detail} lastRun={lastRun}
                mark={marks[stage]} onClose={() => selectStage(null)} />)}

            {mode === 'investigate' && (<>
              <Blockers roots={roots} rootIdx={rootIdx} onSelect={selectBlocker} detail={detail} />
              {!roots.length && (() => {
                const next = (attention.data?.items || []).find((c: any) => c.id !== detail.case.id && c.status === 'BLOCKED');
                return next ? <Button onClick={() => { attention.reload(); load(String(next.hadm_id)); }}>Open the next blocked case ({next.case_ref})</Button> : null;
              })()}
              {root && <BlockerDetail root={root} detail={detail} stages={stages} onPreview={openPreview} correctable={!!draft} hasTask={!!task} onStage={selectStage} />}
            </>)}

            {mode === 'correct' && root && (
              <div className="one-card" aria-labelledby="corr-h">
                <div className="one-eyebrow">Correct · nothing changes until you apply</div>
                <h2 id="corr-h">{root.title}</h2>
                {draft && preview ? (<>
                  <p className="small">Operation <span className="mono">{draft.op}</span> changes the <strong>{domainLabel(preview.domain)}</strong>. The dependency model plans:</p>
                  <div className="one-split">
                    <div><div className="one-k">Will be re-evaluated ({preview.planned.length})</div>
                      <ol className="one-plan">{preview.planned.map((s: string) => <li key={s}><button type="button" className="linkish" onClick={() => selectStage(s)}>{stageLabel(s)}</button><span className="faint small"> — {readable(preview.why[s])}</span></li>)}</ol></div>
                    <div><div className="one-k">Kept as is ({preview.kept.length})</div>
                      <ul className="one-plan">{preview.kept.map((s: string) => <li key={s}><button type="button" className="linkish" onClick={() => selectStage(s)}>{stageLabel(s)}</button></li>)}</ul></div>
                  </div>
                  <p className="small faint">A planned stage whose inputs turn out identical is reused, not executed. The run will show what actually happened. Source: {preview.source}.</p>
                  {draft.fields.map((fl) => (
                    <label key={fl.key} className="one-field"><span>{fl.label}</span>
                      <input className="input" type={fl.type} value={getPath(payload, fl.key) ?? ''}
                        onChange={(e) => setPayload(setPath(payload, fl.key, fl.type === 'number' ? Number(e.target.value) : e.target.value))} />
                      {fl.help && <span className="faint small">{fl.help}</span>}</label>))}
                  <label className="one-field"><span>Reason (recorded in the audit trail)</span>
                    <textarea className="textarea" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
                  {formErr && <div className="error-state" role="alert">{formErr}</div>}
                  <div className="one-actions">
                    <Button variant="primary" onClick={apply} disabled={busy}>Apply correction and re-run affected stages</Button>
                    <Button onClick={() => { setMode('investigate'); setPreview(null); }} disabled={busy}>Cancel</Button>
                  </div>
                  <details className="one-more"><summary>Optional: check output consistency against a full re-run first</summary>
                    <p className="small">Runs the same correction twice on two <strong>isolated copies</strong> of the database (live data is not changed): once re-running all 11 stages, once with targeted reprocessing, and compares the final outputs.</p>
                    <Button size="sm" onClick={checkConsistency} busy={['starting', 'queued', 'running'].includes(consJob.job?.status)} disabled={busy}>Run consistency check</Button>
                    {consErr && <div className="error-state" role="alert">{consErr}</div>}
                    {cons && <ConsistencyResult rec={cons} />}
                  </details>
                </>) : (
                  <DecisionForm root={root} task={task} note={note} setNote={setNote} formErr={formErr} onDecide={decide} onCancel={() => setMode('investigate')} busy={!!busy} />)}
              </div>)}

            {mode === 'run' && run && (
              <RunPanel run={run} outcome={outcome} replay={replay} total={total} auditEv={auditEv} chain={chain} onAudit={openAudit}
                onStage={selectStage} onDone={() => { setMode('investigate'); setRun(null); setRootIdx(0); setStage(detail?.root_blocker?.roots?.[0]?.stage || null); setFocus('blocker'); }} />)}

            <details className="one-more">
              <summary>Measured results and study tools</summary>
              <MeasuredResults />
              <div className="one-k" style={{ marginTop: 10 }}>Study support</div>
              <p className="small">Condition: <strong>{view === '3d' ? 'spatial workspace' : 'list presentation'}</strong> (switch with the 3D/List control or <span className="mono">?view=list</span>). {log.length} interaction(s) recorded in this browser tab only; no patient identifiers are logged.</p>
              <Button size="sm" onClick={exportLog}>Export session log (JSON)</Button>
              <p className="small faint">No effect on comprehension or speed has been measured yet; these tools exist to support such a study.</p>
            </details>
          </>)}
        </aside>
      </div>

      {/* ---------- C2: explanation bar ---------- */}
      <footer className="one-explain" aria-label="Where you are">
        <ol className="one-steps">{STEPS.map((s, i) => {
          const cur = STEPS.findIndex((x) => x.id === step);
          return <li key={s.id} className={i < cur ? 'done' : i === cur ? 'cur' : ''} aria-current={i === cur ? 'step' : undefined}><span className="n">{i + 1}</span>{s.title}</li>;
        })}</ol>
        <p className="one-say" aria-live="polite">{explain(step, detail, root, preview, run, outcome)}</p>
      </footer>
    </div>
  );
}

function explain(step: Step, detail: any, root: any, preview: any, run: RunState | null, o: ReturnType<typeof runOutcome>) {
  if (!detail) return 'Load an encounter to begin. The workflow shows the real 11 rule-based stages and how they depend on each other.';
  if (step === 'understand' && root) return `${root.title} was raised by ${stageLabel(root.stage)}. It is the root blocker: other issues it explains are consequences and need no separate fix. Owner: ${root.owner_team}.`;
  if (step === 'investigate') return detail.root_blocker?.roots?.length ? 'Select a blocker or a stage to see the evidence.' : 'This case has no open root blocker.';
  if (step === 'correct' && preview) return `Preview only. ${preview.planned.length} stage(s) read the changed ${domainLabel(preview.domain).toLowerCase()} or depend on one that does; ${preview.kept.length} keep their results.`;
  if (step === 'correct') return 'This blocker cannot be fixed by changing data; a reviewer records a decision with a justification.';
  if (step === 'reprocess') return run?.status === 'running' ? 'The backend is applying the change and re-running the affected stages.' : run?.error || 'The run did not complete.';
  if (run?.kind === 'decision') return 'Decision recorded. Only review routing and claim preparation read review decisions, so only they were re-evaluated.';
  return `Done: ${o.executed.length} executed, ${o.reused.length} reused, ${o.notRerun.length} not re-run. The case is now ${run?.after?.case?.status?.replace(/_/g, ' ').toLowerCase()}.`;
}

// ------------------------------------------------------------------------------------------
function SceneTag({ text, marks }: { text: string; marks: Record<string, SceneMark> }) {
  const used = Array.from(new Set(Object.values(marks)));
  return (
    <div className="one-tag s3d-ui" aria-live="polite">
      <div className="one-tag-src">{text}</div>
      {used.length > 0 && <div className="one-legend">{used.map((m) => <span key={m} className={`bb-mark mk-${m}`}>{MARK_TEXT[m]}</span>)}</div>}
    </div>);
}

function ListView({ stages, waves, snaps, marks, selected, onSelect, sourceTag }: any) {
  return (
    <div className="one-listview" role="region" aria-label="Workflow stages in dependency order">
      <div className="one-tag-src">{sourceTag}</div>
      {waves.map((w: string[], i: number) => (
        <div key={i} className={`one-wave ${w.length > 1 ? 'par' : ''}`}>
          <div className="one-wave-k">Wave {i + 1}{w.length > 1 ? ' · independent: may run concurrently' : ''}</div>
          {w.map((id) => {
            const st = stages.find((s: any) => s.id === id); const sn = snaps[id]; const mk = marks[id] as SceneMark | undefined;
            return (
              <button key={id} type="button" className={`one-lrow st-${sn?.state} ${selected === id ? 'sel' : ''}`} aria-pressed={selected === id} onClick={() => onSelect(id)}>
                <span className="one-n">{stages.indexOf(st) + 1}</span>
                <span className="one-lname">{stageLabel(id)}<span className="faint small"> · needs {st.deps.map(stageLabel).join(', ') || 'nothing'}</span></span>
                <span className="one-lstate">{STATE_LABEL[(sn?.state || 'waiting') as StageState]}{sn?.duration !== undefined ? ` · ${fmtMs(sn.duration)}` : ''}</span>
                {mk && <span className={`bb-mark mk-${mk}`}>{MARK_TEXT[mk]}</span>}
              </button>);
          })}
        </div>))}
    </div>);
}

function CaseHeader({ detail }: { detail: any }) {
  const c = detail.case;
  return (
    <div className="one-card one-case">
      <div className="one-eyebrow">Case · {c.data_source === 'SYNTHETIC' ? 'synthetic data' : `${c.data_source} encounter, synthetic billing and insurance`}</div>
      <div className="one-case-h"><Link to={`/claims/${c.id}`} className="mono">{c.case_ref}</Link><Badge value={c.status} /></div>
      <div className="one-facts">
        <div><span>Root blockers</span><b>{(detail.root_blocker?.roots || []).length}</b></div>
        <div><span>Open tasks</span><b>{c.open_tasks}</b></div>
        <div><span>Amount at risk</span><b>{money(c.amount_at_risk)}</b></div>
      </div>
      <div className="faint small">Internal preparation status, not an insurer decision.</div>
    </div>);
}

function Blockers({ roots, rootIdx, onSelect, detail }: any) {
  if (!roots.length) return <div className="one-card"><h2>No root blocker</h2><p className="small">Every automated check passed or was resolved. Status: {detail.case.status.replace(/_/g, ' ').toLowerCase()}.</p></div>;
  return (
    <div className="one-card">
      <div className="one-eyebrow">Investigate · {roots.length} root blocker(s), most important first</div>
      <ul className="one-list">{roots.map((r: any, i: number) => (
        <li key={`${r.code}-${i}`}><button type="button" className={`one-row ${i === rootIdx ? 'on' : ''}`} aria-pressed={i === rootIdx} onClick={() => onSelect(i)}>
          <span className={sevClass(r.severity)}>{r.severity}</span><span className="one-rt">{r.title}{r.rule_id && <span className="mono faint small"> · {r.rule_id}</span>}</span><span className="faint small">{stageLabel(r.stage)}</span></button></li>))}</ul>
    </div>);
}

const EV_HIDE = new Set(['possible_cause_codes', 'reference', 'rule']);
function BlockerDetail({ root, detail, stages, onPreview, correctable, hasTask, onStage }: any) {
  const cons = (detail.root_blocker?.consequences || []).filter((c: any) => c.caused_by === root.code);
  const ev = Object.entries(root.evidence || {}).filter(([k, v]) => !EV_HIDE.has(k) && v !== null && v !== '' && typeof v !== 'object');
  const st = stages.find((s: any) => s.id === root.stage);
  return (
    <div className="one-card" aria-labelledby="blk-h">
      <div className="one-eyebrow">Understand</div>
      <h2 id="blk-h" className="blocker-title">{root.title}</h2>
      <p className="one-plain">{root.message}</p>
      {(root.expected != null || root.actual != null) && (
        <div className="one-evx"><div><span>Expected</span><b>{root.expected ?? '—'}</b></div><div><span>Recorded</span><b>{root.actual ?? '—'}</b></div>
          {root.amount ? <div><span>Amount</span><b>{money(root.amount)}</b></div> : null}</div>)}
      <dl className="one-dl">
        <dt>Raised by</dt><dd><button type="button" className="linkish" onClick={() => onStage(root.stage)}>{stageLabel(root.stage)}</button> (rule-based check)</dd>
        <dt>Owner</dt><dd>{root.owner_team} · fix in {domainLabel(root.owner_domain).toLowerCase()}</dd>
        <dt>Suggested action</dt><dd>{root.next_action}</dd>
        {root.rule_id && <><dt>Rule</dt><dd className="mono small">{root.rule_id}{root.source_document ? ` · ${root.source_document}${root.source_page ? ` p.${root.source_page}` : ''}` : ''}</dd></>}
      </dl>
      {ev.length > 0 && <details className="one-more"><summary>Evidence fields ({ev.length})</summary>
        <dl className="one-dl">{ev.map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, ' ')}</dt><dd className="mono small">{String(v)}</dd></Fragment>)}</dl></details>}
      <div className="one-k">What it affects</div>
      {cons.length ? <ul className="one-cons">{cons.map((c: any, i: number) => <li key={i}><span className={sevClass(c.severity)}>{c.severity}</span> <b>{c.title}</b> in {stageLabel(c.stage)} — consequence: {c.causal_rule}.</li>)}</ul>
        : <p className="small">No other finding is explained by this blocker.</p>}
      {st && <p className="small faint">Downstream of {stageLabel(root.stage)}: {(st.downstream || []).map(stageLabel).join(', ') || 'none'} (highlighted in the workflow).</p>}
      <div className="one-actions">
        <Button variant="primary" onClick={onPreview}>{correctable ? 'Preview correction' : hasTask ? 'Record a review decision' : 'Options'}</Button>
      </div>
      {!correctable && <p className="small faint">No data correction is defined for this issue type.</p>}
    </div>);
}

function DecisionForm({ root, task, note, setNote, formErr, onDecide, onCancel, busy }: any) {
  if (!task) return <><p className="small">There is no open review task for this blocker, so no decision can be recorded here.</p><Button onClick={onCancel}>Back</Button></>;
  return (<>
    <p className="small">{root.correctable ? 'This issue needs input that cannot be derived from the evidence. Use the case page to enter it, or record a decision.' : 'This blocker cannot be resolved by changing data (for example a policy rule). A reviewer records a decision.'}</p>
    <label className="one-field"><span>Justification (recorded in the audit trail)</span><textarea className="textarea" rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} /></label>
    {formErr && <div className="error-state" role="alert">{formErr}</div>}
    <div className="one-actions">
      {task.allowed_decisions.filter((d: string) => d !== 'CORRECT_DATA').map((d: string) => (
        <Button key={d} variant="primary" disabled={busy} onClick={() => onDecide(d)}>{d === 'CONFIRM_BLOCKED' ? 'Confirm blocked' : 'Accept with justification'}</Button>))}
      <Button onClick={onCancel}>Cancel</Button>
    </div>
  </>);
}

function ConsistencyResult({ rec }: { rec: any }) {
  const s = rec.summary || {};
  const [f, t] = s.runs || [];
  if (!f || !t) return <p className="small">No result.</p>;
  return (
    <div className="one-cons-res" role="status">
      <div><span>Full re-run</span><b>{f.metrics.stages_executed} executed</b></div>
      <div><span>Targeted</span><b>{t.metrics.stages_executed} executed · {t.metrics.stages_reused} reused · {t.metrics.stages_not_rerun} not re-run</b></div>
      <div><span>Final outputs</span><b className={s.outputs_match ? 'ok' : 'bad'}>{s.outputs_match ? 'identical' : `differ in ${s.mismatched_stages?.join(', ')}`}</b></div>
      <div className="faint small">Control Room record #{rec.id} · isolated copies · live data unchanged.</div>
    </div>);
}

function RunPanel({ run, outcome, replay, total, auditEv, chain, onAudit, onStage, onDone }: any) {
  const b = run.before?.case, a = run.after?.case;
  const fd = run.after ? findingDiff(run.before, run.after) : null;
  const od = run.afterRun ? outputDiff(run.beforeRun, run.afterRun) : [];
  const corr = run.after?.corrections?.[run.after.corrections.length - 1];
  const S = ({ list }: { list: string[] }) => list.length ? <>{list.map((s, i) => <span key={s}>{i ? ', ' : ''}<button type="button" className="linkish" onClick={() => onStage(s)}>{stageLabel(s)}</button></span>)}</> : <span className="faint">none</span>;
  return (
    <div className="one-card" aria-labelledby="run-h">
      <div className="one-eyebrow">{run.status === 'running' ? 'Reprocess · running on the backend' : 'Verify'}</div>
      <h2 id="run-h">{run.kind === 'decision' ? 'Review decision recorded' : run.status === 'finished' ? 'Correction applied' : run.status === 'running' ? 'Applying correction…' : 'Correction not applied'}</h2>
      {(run.status === 'rejected' || run.status === 'error') && <div className="error-state" role="alert">{run.error} {run.status === 'rejected' ? 'Nothing was changed.' : 'The backend rolled the change back.'}</div>}
      {run.kind === 'correction' && run.events.length > 0 && (<>
        <dl className="one-dl">
          <dt>Executed</dt><dd><S list={outcome.executed} /></dd>
          <dt>Reused (identical inputs)</dt><dd><S list={outcome.reused} /></dd>
          <dt>Not re-run</dt><dd><S list={outcome.notRerun} /></dd>
          <dt>Output changed</dt><dd><S list={outcome.changed} /></dd>
          {outcome.failed.length > 0 && <><dt>Failed</dt><dd><S list={outcome.failed} /></dd></>}
          {outcome.end && <><dt>Measured time</dt><dd>{fmtMs(outcome.end.wall_ms)} wall-clock · {fmtMs(outcome.end.commit_ms)} incl. commit</dd></>}
        </dl>
        {run.status === 'finished' && <ReplayBar replay={replay} total={total} />}
      </>)}
      {run.kind === 'decision' && run.result && <p className="small">Run #{run.result.run.id}: {run.result.run.stages_executed} executed, {run.result.run.stages_reused} reused. Decisions are applied through the review API, which records no event stream, so there is no replay.</p>}
      {a && b && (<>
        <div className="one-k">Before → after</div>
        <table className="one-ba"><tbody>
          <tr><th scope="row">Status</th><td><Badge value={b.status} /></td><td><Badge value={a.status} /></td></tr>
          <tr><th scope="row">Root blocker</th><td>{b.root_blocker_title || '—'}</td><td>{a.root_blocker_title || 'none'}</td></tr>
          <tr><th scope="row">Open tasks</th><td>{b.open_tasks}</td><td>{a.open_tasks}</td></tr>
          <tr><th scope="row">Amount at risk</th><td>{money(b.amount_at_risk)}</td><td>{money(a.amount_at_risk)}</td></tr>
          <tr><th scope="row">Estimated eligible</th><td>{money(b.estimated_eligible)}</td><td>{money(a.estimated_eligible)}</td></tr>
        </tbody></table>
        {fd && <p className="small">Findings resolved: <b>{fd.resolved.length}</b>{fd.resolved.length ? ` (${fd.resolved.map((f: any) => f.title).join('; ')})` : ''} · new: <b>{fd.added.length}</b>{fd.added.length ? ` (${fd.added.map((f: any) => f.title).join('; ')})` : ''}</p>}
        {od.length > 0 && <details className="one-more"><summary>Stage output changes ({od.length} stage(s))</summary>
          <table className="one-ba small"><thead><tr><th>Stage · field</th><th>Before</th><th>After</th></tr></thead><tbody>
            {od.flatMap((r: any) => r.fields.slice(0, 8).map((f: any) => <tr key={`${r.stage}${f.key}`}><td>{stageLabel(r.stage)} · <span className="mono">{f.key}</span></td><td className="mono">{String(f.before ?? '—')}</td><td className="mono">{String(f.after ?? '—')}</td></tr>))}
          </tbody></table></details>}
        <div className="one-k">Audit evidence</div>
        {corr && run.kind === 'correction' && <p className="small">Correction #{corr.id} · <span className="mono">{corr.op}</span> on {domainLabel(corr.domain).toLowerCase()} · by {corr.actor} · run #{corr.run_id}</p>}
        {!auditEv ? <Button size="sm" onClick={onAudit}>Show audit records</Button> : (<>
          {chain && <p className={`small ${chain.ok ? 'ok' : 'bad'}`} role="status">Audit hash chain {chain.ok ? `intact (${chain.checked} events checked)` : `BROKEN at event #${chain.first_broken_event_id}`}.</p>}
          <ol className="one-audit">{auditEv.slice(0, 8).map((e: any) => <li key={e.id}><span className="mono small">#{e.id}</span> <b>{e.event_type.replace(/_/g, ' ').toLowerCase()}</b> — {e.summary}</li>)}</ol>
          <Link className="small" to="/audit">Open the full audit trail</Link>
        </>)}
        <div className="one-actions"><Button onClick={onDone}>Continue investigating this case</Button></div>
      </>)}
    </div>);
}

function MeasuredResults() {
  const hist = useApi<any[]>(() => api.crHistory());
  const cmp = (hist.data || []).find((r) => r.kind === 'cr_comparison' && r.status === 'COMPLETED');
  const cor = (hist.data || []).find((r) => r.kind === 'cr_correction' && r.status === 'COMPLETED');
  return (
    <div className="small">
      <p>Recorded on this database (Agent Control Room history):</p>
      <ul>
        <li>Sequential vs parallel: {cmp ? <>record #{cmp.id}, encounter {cmp.hadm_id}, median wall-clock change {cmp.wall_reduction_pct ?? '—'}% (positive = parallel faster), outputs {cmp.outputs_match ? 'identical' : 'not identical'}. Parallel execution has <strong>not</strong> shown a reliable speed advantage.</> : 'no comparison recorded yet.'}</li>
        <li>Full vs targeted re-run: {cor ? <>record #{cor.id}, outputs {cor.outputs_match ? 'identical' : 'not identical'}.</> : 'no correction comparison recorded yet.'}</li>
      </ul>
      <p>Offline experiments with ground truth (synthetic and MIMIC-linked cohorts) are on the <Link to="/experiments">Experiments</Link> page and in <span className="mono">docs/research/EXPERIMENT_RESULTS.md</span>.</p>
    </div>);
}

function StageCard({ st, idx, snap, detail, lastRun, mark, onClose }: any) {
  if (!st) return null;
  const dep = dependencyText(st, stageLabel, domainLabel);
  const fs = (detail?.findings || []).filter((f: any) => f.stage === st.id);
  const out = lastRun?.stages?.find((s: any) => s.stage === st.id);
  const scal = Object.entries(out?.output || {}).filter(([, v]) => v === null || ['string', 'number', 'boolean'].includes(typeof v)).slice(0, 12);
  return (
    <div className="one-card one-stagecard" aria-labelledby="st-h">
      <button type="button" className="one-x" onClick={onClose} aria-label="Close stage details">✕</button>
      <div className="one-eyebrow">Stage {idx + 1} of 11 · deterministic rule-based Python, not an AI agent{mark ? ` · ${MARK_TEXT[mark as SceneMark]}` : ''}</div>
      <h2 id="st-h">{stageLabel(st.id)}</h2>
      <p className="one-plain">{st.purpose}</p>
      <dl className="one-dl">
        <dt>State</dt><dd>{STATE_LABEL[(snap?.state || 'waiting') as StageState]}{snap?.duration !== undefined ? ` · ${fmtMs(snap.duration)} measured` : ''}</dd>
        <dt>Waits for</dt><dd>{dep.deps.join(', ') || 'nothing (first stage)'}</dd>
        <dt>Why</dt><dd>{dep.ups.length ? `It uses ${dep.ups.join('; ')}.` : 'It uses no upstream results; it only waits for ordering.'}</dd>
        <dt>Reads</dt><dd>{dep.reads.join(', ') || 'results of earlier stages only'}</dd>
        <dt>Findings now</dt><dd>{fs.length ? fs.map((f: any) => `${f.title} (${f.severity.toLowerCase()})`).join('; ') : 'none'}</dd>
      </dl>
      {scal.length > 0 && <details className="one-more"><summary>Recorded output ({out?.output_source})</summary>
        <dl className="one-dl">{scal.map(([k, v]) => <Fragment key={k}><dt>{k.replace(/_/g, ' ')}</dt><dd className="mono small">{String(v)}</dd></Fragment>)}</dl></details>}
      <details className="one-more"><summary>Implementation and checks</summary>
        <p className="mono small">{st.implementation}</p>
        <p className="small">{st.checks?.length ? st.checks.map((c: any) => c.title).join(' · ') : st.note}</p></details>
    </div>);
}
