import { Component, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { caseState } from '../../lib/caseState';
import { lastTime, stagesAt, type StageSnap } from '../../lib/controlRoom';
import type { SceneMark } from '../../lib/marks';
import { draftFor } from '../cc/CorrectionDialog';
import { useJob, useReplay } from '../cc/controlRoomParts';
import { investigateMarks, previewMarks, runOutcome, snapsFromCase } from '../one/model';
import { isView, STATION, STATIONS, type ViewId } from '../../world/stations';
import type { Pick, WorldHandleT } from './types';
import { ListDeck, Timeline } from './parts';
import { webglSupported } from './webgl';
import {
  AuditPanel, CorrectPanel, DepartmentsPanel, HistoryPanel, InvestigatePanel, LabPanel, OverviewPanel, PolicyPanel,
} from './panels';

/* eslint-disable @typescript-eslint/no-explicit-any */
const World3D = lazy(() => import('../../world/World3D'));

/** If the WebGL world throws while starting (driver or context failure), show the list view instead. */
class WorldGuard extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

/**
 * ClearClaim command center: ONE persistent 3D environment (the world) and eight stations. Changing
 * station flies the camera through the same scene; the 2D panel beside it carries text and controls
 * that do not belong in 3D (rule wording, tables, forms). The backend is the only source of state.
 */
export type RunState = {
  kind: 'correction' | 'decision'; status: 'running' | 'finished' | 'rejected' | 'error'; events: any[]; error?: string;
  runId?: number; before: any; after?: any; beforeRun?: any; afterRun?: any; body?: any; result?: any;
};

export default function CommandCenter() {
  const { view: viewParam } = useParams();
  const view: ViewId = isView(viewParam) ? viewParam : 'overview';
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const go = useCallback((v: ViewId) => nav({ pathname: v === 'overview' ? '/' : `/space/${v}`, search: sp.toString() }), [nav, sp]);

  // ---------------- reference data ----------------
  const reg = useApi<any>(() => api.crStages());
  const meta = useApi<any>(() => api.meta());
  const attention = useApi<any>(() => api.cases({ status: 'BLOCKED,NEEDS_REVIEW', sort: 'priority', order: 'desc', page_size: 12 }));
  const overview = useApi<any>(() => api.overview());
  const stages: any[] = useMemo(() => reg.data?.stages || [], [reg.data]);
  const waves: string[][] = useMemo(() => reg.data?.waves || [], [reg.data]);
  const ids: string[] = useMemo(() => stages.map((s) => s.id), [stages]);
  const stageDefs = useMemo(() => stages.map((s) => ({ id: s.id, deps: s.deps, label: s.label, owner: s.owner })), [stages]);

  // ---------------- the encounter ----------------
  const [enc, setEnc] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [lastRun, setLastRun] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [rules, setRules] = useState<any>(null);
  const [hist, setHist] = useState<any>(null);
  const [auditEv, setAuditEv] = useState<any[] | null>(null);
  const [chain, setChain] = useState<any>(null);

  const refreshIntel = useCallback(async (caseId: number) => {
    const [r, h, ev] = await Promise.all([
      api.intelRules(caseId).catch(() => null), api.intelHistorical(caseId, 400).catch(() => null), api.caseEvents(caseId).catch(() => null)]);
    setRules(r); setHist(h); setAuditEv(ev);
  }, []);

  const load = useCallback(async (h: string) => {
    const hadm = h.trim();
    if (!/^\d{1,12}$/.test(hadm)) { setLoadErr('An Encounter ID is digits only, for example 98000056.'); return; }
    setLoading(true); setLoadErr(null);
    try {
      const e = await api.studioEncounter(hadm);
      const [d, r] = await Promise.all([api.caseDetail(e.case.id), e.last_run ? api.studioPipelineRun(e.last_run.id) : Promise.resolve(null)]);
      setEnc(e); setDetail(d); setLastRun(r);
      setRootIdx(0); setStage(null); setRule(null); setPeer(null); setAuditSel(null); setPreview(null); setRun(null); setDepartment(null);
      setSp((p) => { const n = new URLSearchParams(p); n.set('encounter', hadm); return n; }, { replace: true });
      refreshIntel(e.case.id);
    } catch (err) { setLoadErr((err as Error).message); } finally { setLoading(false); }
  }, [setSp, refreshIntel]);

  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    const fromUrl = sp.get('encounter');
    if (fromUrl) { started.current = true; load(fromUrl); return; }
    const first = attention.data?.items?.find((c: any) => c.status === 'BLOCKED') || attention.data?.items?.[0];
    if (first) { started.current = true; load(String(first.hadm_id)); }
  }, [attention.data, sp, load]);

  useEffect(() => { api.auditVerify().then(setChain).catch(() => setChain(null)); }, [detail?.case?.version]);

  // ---------------- selection ----------------
  const [rootIdx, setRootIdx] = useState(0);
  const [stage, setStage] = useState<string | null>(null);
  const [rule, setRule] = useState<string | null>(null);
  const [peer, setPeer] = useState<number | null>(null);
  const [auditSel, setAuditSel] = useState<number | null>(null);
  const [department, setDepartment] = useState<string | null>(null);
  const [bar, setBar] = useState<{ lane: number; stage: string } | null>(null);
  const roots: any[] = detail?.root_blocker?.roots || [];
  const root = roots[rootIdx] || null;
  const state = detail ? caseState(detail) : null;

  // ---------------- correction ----------------
  const draft = useMemo(() => (root && detail && root.correctable ? draftFor(root, detail) : null), [root, detail]);
  const task = root && detail ? detail.tasks.find((t: any) => t.status === 'OPEN' && t.code === root.code) : null;
  const [preview, setPreview] = useState<any>(null);
  const [run, setRun] = useState<RunState | null>(null);
  useEffect(() => {
    setPreview(null);
    if (draft) api.workspacePreview(draft.op).then(setPreview).catch(() => setPreview(null));
  }, [draft]);

  const total = useMemo(() => lastTime(run?.events || []), [run?.events]);
  const replay = useReplay(total || 1);

  const applyCorrection = async (body: any) => {
    if (!enc) return;
    const before = detail, beforeRun = lastRun;
    setRun({ kind: 'correction', status: 'running', events: [], before, beforeRun, body });
    try {
      const { token } = await api.studioStart({ hadm_id: String(enc.case.hadm_id), mode: 'correction', correction: body });
      let after = 0, events: any[] = [];
      for (;;) {
        const j = await api.studioRun(token, after);
        events = [...events, ...j.events]; after = j.next_after;
        setRun((r) => r && { ...r, events });
        if (j.status === 'queued' || j.status === 'running') { await new Promise((res) => setTimeout(res, 100)); continue; }
        if (j.status !== 'finished') { setRun((r) => r && { ...r, status: j.status === 'rejected' ? 'rejected' : 'error', error: j.error }); return; }
        const [e2, d2, r2] = await Promise.all([api.studioEncounter(String(enc.case.hadm_id)), api.caseDetail(enc.case.id), api.studioPipelineRun(j.run_id)]);
        setEnc(e2); setDetail(d2); setLastRun(r2);
        setRun((r) => r && { ...r, status: 'finished', runId: j.run_id, after: d2, afterRun: r2 });
        refreshIntel(enc.case.id); attention.reload(); overview.reload();
        replay.setT(0); replay.setPlaying(true);
        return;
      }
    } catch (e) { setRun((r) => r && { ...r, status: 'error', error: (e as Error).message }); }
  };
  const decide = async (decision: string, note: string) => {
    if (!task || !enc) return;
    const before = detail, beforeRun = lastRun;
    setRun({ kind: 'decision', status: 'running', events: [], before, beforeRun });
    try {
      const res = await api.decide(task.id, { decision, note });
      const [e2, d2, r2] = await Promise.all([api.studioEncounter(String(enc.case.hadm_id)), api.caseDetail(enc.case.id), api.studioPipelineRun(res.run.id)]);
      setEnc(e2); setDetail(d2); setLastRun(r2);
      setRun((r) => r && { ...r, status: 'finished', runId: res.run.id, after: d2, afterRun: r2, result: res });
      refreshIntel(enc.case.id); attention.reload();
    } catch (e) { setRun((r) => r && { ...r, status: 'error', error: (e as Error).message }); }
  };

  // ---------------- execution lab ----------------
  const labJob = useJob();
  const [labRec, setLabRec] = useState<any>(null);
  const [labErr, setLabErr] = useState<string | null>(null);
  const labTotal = useMemo(() => Math.max(0, ...((labRec?.lanes || []) as any[]).flatMap((l) => l.bars.map((b: any) => b.end))), [labRec]);
  const labReplay = useReplay(labTotal || 1);
  const openRecord = useCallback(async (rid: number) => {
    try { setLabRec(toLab(await api.crRecord(rid))); setLabErr(null); } catch (e) { setLabErr((e as Error).message); }
  }, []);
  // show the most recent recorded comparison for this encounter, if any
  useEffect(() => {
    if (!enc) return;
    setLabRec(null);
    api.crHistory().then((h) => {
      const r = h.find((x: any) => String(x.hadm_id) === String(enc.case.hadm_id) && x.kind === 'cr_comparison' && x.status === 'COMPLETED');
      if (r) openRecord(r.id);
    }).catch(() => undefined);
  }, [enc?.case?.hadm_id, openRecord]); // eslint-disable-line react-hooks/exhaustive-deps
  const runCompare = (reps: number) => {
    if (!enc) return;
    setLabErr(null);
    labJob.start(() => api.crCompare({ hadm_id: String(enc.case.hadm_id), repetitions: reps }), (rid, j) => {
      if (rid) openRecord(rid); else setLabErr(j.error || 'The comparison did not complete.');
    });
  };
  const runCorrectionCompare = (body: any) => {
    if (!enc) return;
    setLabErr(null);
    labJob.start(() => api.crCorrection({ hadm_id: String(enc.case.hadm_id), correction: body }), (rid, j) => {
      if (rid) openRecord(rid); else setLabErr(j.error || 'The correction comparison did not complete.');
    });
  };

  // ---------------- what the world shows ----------------
  const snaps: Record<string, StageSnap> = useMemo(() => {
    if (run && run.events.length) return stagesAt(run.events, ids, run.status === 'running' ? null : replay.t);
    return snapsFromCase(detail, ids);
  }, [run, ids, replay.t, detail]);
  const marks: Record<string, SceneMark> = useMemo(() => {
    if (view === 'correct' && preview && !run) return previewMarks(preview);
    if ((view === 'investigate' || view === 'overview' || view === 'correct') && root && !run) return investigateMarks(root, detail, stages);
    return {};
  }, [view, preview, root, detail, stages, run]);
  const ruleDots = useMemo(() => (rules?.rules || []).map((r: any) => ({ id: r.rule_id, status: r.outcome?.status || 'CATALOG', group: r.insurer })), [rules]);
  const peers = useMemo(() => (hist?.peers || []).map((p: any) => ({ total: p.total_billed, los: p.los_days ?? 0, room: p.room_category })), [hist]);
  const band = useMemo(() => {
    if (!hist || hist.status !== 'COMPARED' || !hist.mad_total) return null;
    const w = (hist.method.z_threshold * hist.mad_total) / 0.6745;
    return { median: hist.median_total, lo: hist.median_total - w, hi: hist.median_total + w, medianLos: hist.median_los ?? null };
  }, [hist]);
  const current = useMemo(() => (hist ? { total: hist.current.total_billed, los: hist.current.los_days } : null), [hist]);
  const auditBlocks = useMemo(() => (auditEv || []).map((e: any) => ({ id: e.id, type: e.event_type })), [auditEv]);
  const outcome = useMemo(() => runOutcome(run?.events || [], ids), [run?.events, ids]);

  const onPick = useCallback((p: Pick | null) => {
    if (!p) return;
    if (p.kind === 'stage') { setStage((s) => (s === p.id ? null : p.id)); if (!['investigate', 'departments', 'correct'].includes(view)) go('investigate'); }
    else if (p.kind === 'rule') { setRule(p.id); if (view !== 'policy') go('policy'); }
    else if (p.kind === 'peer') { setPeer(p.index); if (view !== 'history') go('history'); }
    else if (p.kind === 'bar') { setBar({ lane: p.lane, stage: p.stage }); if (view !== 'lab') go('lab'); }
    else if (p.kind === 'audit') { setAuditSel(p.id); if (view !== 'audit') go('audit'); }
    else if (p.kind === 'department') { setDepartment((d) => (d === p.id ? null : p.id)); if (view !== 'departments') go('departments'); }
  }, [view, go]);

  // ---------------- layout, keyboard ----------------
  const [mode3d, setMode3d] = useState<'3d' | 'list'>(sp.get('mode') === 'list' ? 'list' : '3d');
  const gl = useMemo(() => webglSupported(), []);
  const [narrow, setNarrow] = useState(() => window.innerWidth < 900);
  useEffect(() => { const f = () => setNarrow(window.innerWidth < 900); window.addEventListener('resize', f); return () => window.removeEventListener('resize', f); }, []);
  const insets = useMemo(() => (narrow ? { right: 0, left: 0, top: 0, bottom: 0 } : { right: 440, left: 76, top: 64, bottom: 96 }), [narrow]);
  const worldRef = useRef<WorldHandleT>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      const n = Number(e.key);
      if (n >= 1 && n <= 8) { go(STATIONS[n - 1].id); e.preventDefault(); }
      else if (e.key === 'Escape') { if (stage || rule || peer !== null || auditSel !== null) { setStage(null); setRule(null); setPeer(null); setAuditSel(null); } else go('overview'); }
      else if (e.key === 'r' || e.key === 'R') worldRef.current?.reset();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go, stage, rule, peer, auditSel]);
  useEffect(() => { document.title = `ClearClaim · ${STATION[view].title}`; }, [view]);

  const st = STATION[view];
  const ctx = {
    view, go, stages, waves, ids, detail, enc, lastRun, state, roots, rootIdx, setRootIdx, root, stage, setStage, snaps,
    rules, rule, setRule, hist, peer, setPeer, auditEv, chain, auditSel, setAuditSel, department, setDepartment,
    overview: overview.data, attention: attention.data, load, draft, task, preview, run, setRun, applyCorrection, decide, replay, total, outcome,
    labJob, labRec, labErr, runCompare, runCorrectionCompare, labReplay, bar, setBar, meta: meta.data, openRecord,
  };

  return (
    <div className={`space ${narrow ? 'narrow' : ''}`} data-view={view}>
      <a className="skip" href="#space-panel">Skip to the {st.short.toLowerCase()} panel</a>
      <div className="space-stage">
        {(() => {
          const list = (why: string) => (
            <ListDeck stages={stages} waves={waves} snaps={snaps} marks={marks} selected={stage} onSelect={(id: string) => onPick({ kind: 'stage', id })} reason={why} />);
          if (!gl) return list('WebGL is not available in this browser, so the workflow is shown as a list. Every panel still works.');
          if (mode3d === 'list') return list('List view of the same workflow (switch back to 3D in the top bar).');
          return (
            <WorldGuard fallback={list('The 3D view could not start on this device, so the workflow is shown as a list. Every panel still works.')}>
              <Suspense fallback={<div className="world-loading" role="status">Building the 3D workspace…</div>}>
                <World3D ref={worldRef as any} view={view} stages={stageDefs} waves={waves} snaps={snaps} marks={marks}
                  selectedStage={stage} playing={!!run && (run.status === 'running' || replay.playing)} department={department}
                  rules={ruleDots} selectedRule={rule} peers={peers} current={current} band={band} selectedPeer={peer}
                  lanes={labRec?.lanes || []} laneOrder={ids} selectedBar={bar} labCursor={view === 'lab' ? labReplay.t : null}
                  audit={auditBlocks} auditOk={chain ? !!chain.ok : null} selectedAudit={auditSel}
                  focusStage={stage || root?.stage || null} insets={insets} onPick={onPick} />
              </Suspense>
            </WorldGuard>);
        })()}
      </div>

      <header className="space-top">
        <Link to="/" className="brand" aria-label="ClearClaim home">
          <span className="brand-mark" aria-hidden="true">◈</span>
          <span><strong>ClearClaim</strong><span className="brand-sub">Discharge-to-claim coordination</span></span>
        </Link>
        <EncounterPicker items={attention.data?.items || []} current={enc?.case?.hadm_id} loading={loading} error={loadErr} onLoad={load} />
        {state && <span className={`chip tone-${state.tone}`} title={state.sentence}>{state.headline}</span>}
        {detail && <span className="chip src" title="Where this encounter's data comes from">{detail.encounter.data_source === 'MIMIC_IV_DEMO' ? 'MIMIC-IV demo encounter · synthetic billing/policy' : 'Synthetic data'}</span>}
        <div className="top-actions">
          {gl && <button type="button" className="tbtn" aria-pressed={mode3d === 'list'} onClick={() => { const m = mode3d === '3d' ? 'list' : '3d'; setMode3d(m); setSp((p) => { const n = new URLSearchParams(p); if (m === 'list') n.set('mode', 'list'); else n.delete('mode'); return n; }, { replace: true }); }}>
            {mode3d === '3d' ? 'List view' : '3D view'}</button>}
          <details className="tools-menu">
            <summary className="tbtn">Classic tools</summary>
            <nav aria-label="Classic tools">
              <Link to="/one">ClearClaim One workspace</Link><Link to="/control-room">Agent Control Room</Link>
              <Link to="/claims">Cases</Link><Link to="/review">Review queue</Link><Link to="/policies">Policy library</Link>
              <Link to="/experiments">Experiments</Link><Link to="/data">Data laboratory</Link><Link to="/settings">Settings</Link>
              <Link to="/present">Presentation mode</Link><Link to="/about">About</Link>
            </nav>
          </details>
        </div>
      </header>

      <nav className="space-rail" aria-label="Stations">
        {STATIONS.map((s) => (
          <button key={s.id} type="button" className={s.id === view ? 'on' : ''} aria-current={s.id === view ? 'page' : undefined}
            onClick={() => go(s.id)} title={`${s.title} (key ${s.n})`}>
            <span className="rn">{s.n}</span><span className="rl">{s.short}</span>
          </button>
        ))}
      </nav>

      {mode3d === '3d' && gl && (
        <div className="cam-tools" role="toolbar" aria-label="Camera">
          <button type="button" aria-label="Zoom in" onClick={() => worldRef.current?.zoom(0.75)}>＋</button>
          <button type="button" aria-label="Zoom out" onClick={() => worldRef.current?.zoom(1.33)}>－</button>
          <button type="button" onClick={() => worldRef.current?.reset()} title="Return the camera to this station (R)">Re-centre</button>
          <span className="cam-hint">Drag to orbit · right-drag to pan · scroll to zoom · 1–8 stations · Esc back</span>
        </div>
      )}

      <aside className="space-panel" id="space-panel" tabIndex={-1} aria-labelledby="panel-title">
        <div className="panel-head">
          <span className="panel-n">{st.n}</span>
          <div><h1 id="panel-title">{st.title}</h1><p>{st.question}</p></div>
        </div>
        <div className="panel-body">
          {reg.error && <div className="p-error" role="alert">{reg.error} <button type="button" className="link" onClick={reg.reload}>Retry</button></div>}
          {view === 'overview' && <OverviewPanel c={ctx} />}
          {view === 'investigate' && <InvestigatePanel c={ctx} />}
          {view === 'departments' && <DepartmentsPanel c={ctx} />}
          {view === 'policy' && <PolicyPanel c={ctx} />}
          {view === 'history' && <HistoryPanel c={ctx} />}
          {view === 'lab' && <LabPanel c={ctx} />}
          {view === 'correct' && <CorrectPanel c={ctx} />}
          {view === 'audit' && <AuditPanel c={ctx} />}
        </div>
      </aside>

      <Timeline ids={ids} snaps={snaps} detail={detail} run={run} replay={replay} total={total} onSelect={(id) => onPick({ kind: 'stage', id })} selected={stage} />
    </div>
  );
}

function EncounterPicker({ items, current, loading, error, onLoad }: { items: any[]; current?: number; loading: boolean; error: string | null; onLoad: (h: string) => void }) {
  const [v, setV] = useState('');
  useEffect(() => { if (current) setV(String(current)); }, [current]);
  return (
    <form className="picker" onSubmit={(e) => { e.preventDefault(); onLoad(v); }} role="search" aria-label="Load an encounter">
      <label htmlFor="enc-id" className="sr">Encounter ID</label>
      <input id="enc-id" list="enc-suggest" value={v} onChange={(e) => setV(e.target.value)} placeholder="Encounter ID" inputMode="numeric" autoComplete="off"
        aria-invalid={!!error} aria-describedby={error ? 'enc-err' : undefined} />
      <datalist id="enc-suggest">{items.map((c) => <option key={c.id} value={c.hadm_id}>{`${c.status} · ${c.root_blocker_title || c.root_blocker_code || ''}`}</option>)}</datalist>
      <button type="submit" className="tbtn primary" disabled={loading}>{loading ? 'Loading…' : 'Load'}</button>
      {error && <span id="enc-err" className="picker-err" role="alert">{error}</span>}
    </form>
  );
}

/** Lab lanes from a recorded control-room comparison (real, measured event offsets). */
function toLab(rec: any) {
  const runs: any[] = rec.summary?.runs || [];
  const barsOf = (r: any) => (r.events || []).flatMap((e: any) => {
    if (e.type === 'stage_finished') return [{ stage: e.stage, start: e.start_ms, end: e.end_ms, status: e.status }];
    if (e.type === 'stage_reused') return [{ stage: e.stage, start: e.t_ms, end: e.t_ms, status: 'REUSED' }];
    return [];
  });
  let pick: any[] = [];
  if (rec.kind === 'cr_comparison') {
    const measured = runs.filter((r) => !r.warmup);
    // the run whose wall time is the median of its mode is shown; every run stays available in the table
    const med = (m: string) => {
      const rs = measured.filter((r) => r.mode === m).sort((a, b) => (a.metrics.wall_ms ?? 0) - (b.metrics.wall_ms ?? 0));
      return rs[Math.floor((rs.length - 1) / 2)];
    };
    pick = [med('sequential'), med('parallel')].filter(Boolean);
  } else pick = runs;
  return {
    ...rec, shown: pick,
    lanes: pick.map((r) => ({ label: `${r.label}${rec.kind === 'cr_comparison' ? ' (median run)' : ''}`, bars: barsOf(r), metrics: r.metrics, mode: r.mode, policy: r.rerun_policy })),
  };
}

