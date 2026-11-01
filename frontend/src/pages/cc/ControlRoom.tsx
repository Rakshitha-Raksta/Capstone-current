import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { DOMAIN_LABELS, humanize, money, stageLabel } from '../../lib/format';
import { fmtMs, lastTime, stagesAt, STATE_LABEL, type StageSnap } from '../../lib/controlRoom';
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton } from '../../components/ui';
import SpatialScene from '../../components/SpatialScene';
import AgentCanvas from '../../components/AgentCanvas';
import GanttTimeline, { MetricStrip } from '../../components/GanttTimeline';
import { draftFor } from './CorrectionDialog';
import { ComparisonPanel, CorrectionPanel, RESEARCH_NOTE, ReplayBar, TwoRecords, useJob, useReplay } from './controlRoomParts';

/* eslint-disable @typescript-eslint/no-explicit-any */
export type View = 'sequential' | 'parallel' | 'targeted';
export const VIEWS: { id: View; title: string; text: string }[] = [
  { id: 'sequential', title: 'SEQUENTIAL MODE', text: "Each stage runs one at a time, respecting the workflow's dependencies." },
  { id: 'parallel', title: 'PARALLEL MODE', text: 'Independent stages run concurrently when their dependencies permit it.' },
  { id: 'targeted', title: 'TARGETED REPROCESSING MODE', text: 'Correct an issue and rerun only the stages affected by that change.' },
];
const SCENARIO_CODES = ['DUPLICATE_BILL_ITEM', 'BILL_ARITHMETIC_ERROR', 'READINESS_ITEM_INCOMPLETE', 'BILLED_PROCEDURE_NOT_DOCUMENTED', 'RECONCILIATION_RESIDUAL'];

/** Correction that needs no typed input: fully determined by the stage's own evidence. */
export function autoCorrection(d: any) {
  const root = d?.root_blocker?.roots?.[0];
  const draft = root && draftFor(root, d);
  if (!root || !root.correctable || !draft || draft.fields.length) return null;
  const task = d.tasks.find((t: any) => t.status === 'OPEN' && t.code === root.code);
  return { root, draft, body: { op: draft.op, payload: draft.payload, reason: `Control room: ${root.title.toLowerCase()} corrected from the evidence`, review_task_id: task?.id, expected_version: d.case.version } };
}

/** Pick the run a view should show from the launched records. */
export function runFor(view: View, cmp: any, cor: any, rep: number, arm: 'targeted' | 'full') {
  if (view === 'targeted') return cor?.summary?.runs?.find((r: any) => r.rerun_policy === arm) || null;
  const rs = (cmp?.summary?.runs || []).filter((r: any) => r.mode === view && !r.warmup);
  return rs[Math.min(rep, rs.length - 1)] || null;
}

/** HUD numbers at playback time t (null = final), from recorded intervals only. */
export function hudAt(run: any, ids: string[], t: number | null) {
  const snaps = stagesAt(run?.events || [], ids, t);
  const v = Object.values(snaps);
  const c = (...ks: string[]) => v.filter((s) => ks.includes(s.state)).length;
  const iv = v.filter((s) => s.start !== undefined && s.end !== undefined && (t === null || s.start <= t)).map((s) => [s.start!, t === null ? s.end! : Math.min(s.end!, t)]);
  const pts = iv.flatMap(([a, b]) => [[a, 1], [b, -1]]).sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  let cur = 0, best = 0;
  for (const [, d] of pts) { cur += d; best = Math.max(best, cur); }
  return { snaps, completed: c('completed', 'issue', 'blocked'), running: c('running'), waiting: c('waiting', 'ready'), reused: c('reused'),
    notrun: c('notrun'), skipped: c('skipped'), failed: c('failed'), maxConc: best };
}

export default function ControlRoom() {
  const [sp, setSp] = useSearchParams();
  const reg = useApi<any>(() => api.crStages());
  const hist = useApi<any[]>(() => api.crHistory());
  const [hadm, setHadm] = useState(sp.get('encounter') || '98000056');
  const [sugg, setSugg] = useState<any[]>([]);
  const [enc, setEnc] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [lookupErr, setLookupErr] = useState<string | null>(null);
  const [finding, setFinding] = useState(false);
  const [reps, setReps] = useState(5);
  const [phase, setPhase] = useState<'idle' | 'compare' | 'correct' | 'done' | 'error' | 'cancelled'>('idle');
  const [phaseMsg, setPhaseMsg] = useState<string | null>(null);
  const [cmp, setCmp] = useState<any>(null);
  const [cor, setCor] = useState<any>(null);
  const [view, setView] = useState<View>('sequential');
  const [arm, setArm] = useState<'targeted' | 'full'>('targeted');
  const [rep, setRep] = useState(0);
  const [display, setDisplay] = useState<'3d' | '2d' | 'list'>(() => (typeof window !== 'undefined' && window.innerWidth < 760 ? 'list' : '3d'));
  const [selected, setSelected] = useState<string | null>(null);
  const [sideBySide, setSideBySide] = useState(false);
  const [cmpIds, setCmpIds] = useState<number[]>([]);
  const { job, events: liveEvents, start, cancel } = useJob();

  // ----- encounter -----
  const lookup = useCallback(async (v: string) => {
    setLookupErr(null); setEnc(null); setDetail(null);
    const id = v.trim();
    if (!/^\d{1,12}$/.test(id)) { setLookupErr('Enter a numeric Encounter ID, for example 98000056.'); return; }
    try {
      const e = await api.studioEncounter(id);
      setEnc(e); setSp({ encounter: id }, { replace: true });
      setDetail(await api.caseDetail(e.case.id));
    } catch (err) { setLookupErr((err as Error).message); }
  }, [setSp]);
  useEffect(() => { lookup(hadm); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  useEffect(() => {
    if (!/^\d{3,12}$/.test(hadm.trim())) { setSugg([]); return undefined; }
    const h = window.setTimeout(() => { api.cases({ q: hadm.trim(), page_size: 8 }).then((r) => setSugg(r.items || [])).catch(() => setSugg([])); }, 250);
    return () => window.clearTimeout(h);
  }, [hadm]);
  const findScenario = async () => {
    setFinding(true); setLookupErr(null);
    try {
      for (const code of SCENARIO_CODES) {
        const list = await api.cases({ status: 'BLOCKED', root: code, sort: 'case_ref', order: 'asc', page_size: 20 });
        for (const row of list.items) {
          if (row.open_tasks !== 1) continue;
          const d = await api.caseDetail(row.id);
          if ((d.root_blocker?.roots || []).length === 1 && autoCorrection(d)) { setHadm(String(row.hadm_id)); await lookup(String(row.hadm_id)); return; }
        }
      }
      setLookupErr('No blocked synthetic encounter with a single evidence-determined fix is left. Reset the demo data with scripts/setup_demo.py --reset.');
    } catch (e) { setLookupErr((e as Error).message); } finally { setFinding(false); }
  };
  const auto = autoCorrection(detail);

  // ----- launch: two real experiments, one after the other -----
  const launch = () => {
    if (!enc) return;
    const h = String(enc.case.hadm_id);
    const corr = auto;
    setCmp(null); setCor(null); setSelected(null); setPhase('compare'); setPhaseMsg(null); setRep(0);
    start(() => api.crCompare({ hadm_id: h, repetitions: reps }), async (rid, j) => {
      if (j.status === 'cancelled') { setPhase('cancelled'); setPhaseMsg(j.error); if (rid) setCmp(await api.crRecord(rid)); hist.reload(); return; }
      if (!rid) { setPhase('error'); setPhaseMsg(j.error || 'The comparison did not complete.'); return; }
      setCmp(await api.crRecord(rid)); hist.reload();
      if (!corr) { setPhase('done'); setPhaseMsg('Targeted reprocessing was not run: this encounter has no correction fully determined by its evidence. Use “Find a correctable scenario”.'); setView('sequential'); return; }
      setPhase('correct');
      start(() => api.crCorrection({ hadm_id: h, correction: corr.body }), async (rid2, j2) => {
        if (rid2) setCor(await api.crRecord(rid2));
        setPhase(j2.status === 'finished' ? 'done' : j2.status === 'cancelled' ? 'cancelled' : 'error');
        if (j2.status !== 'finished') setPhaseMsg(j2.error);
        hist.reload(); setView('sequential');
      });
    });
  };
  const busy = phase === 'compare' || phase === 'correct';

  // ----- what is on screen -----
  const ids: string[] = useMemo(() => reg.data?.stages.map((s: any) => s.id) || [], [reg.data]);
  const run = runFor(view, cmp, cor, rep, arm);
  const total = useMemo(() => lastTime(run?.events || []), [run]);
  const replay = useReplay(total || 1);
  const autoKey = `${cmp?.id}-${cor?.id}-${view}-${arm}-${rep}`;
  const lastKey = useRef('');
  useEffect(() => {
    // after a launch, and when switching mode, play the recorded run once (time-scaled and labelled)
    if (!run || busy || autoKey === lastKey.current) return;
    lastKey.current = autoKey;
    replay.setT(0); replay.setPlaying(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoKey, busy, run]);
  const liveLabelEvents = useMemo(() => (busy ? liveEvents.filter((e) => !job?.current || e.label === job.current) : []), [busy, liveEvents, job?.current]);
  const hud = useMemo(() => hudAt(busy ? { events: liveLabelEvents } : run, ids, busy ? null : replay.t), [busy, liveLabelEvents, run, ids, replay.t]);
  const snaps = hud.snaps;
  const seqRun = runFor('sequential', cmp, cor, rep, arm), parRun = runFor('parallel', cmp, cor, rep, arm);
  const err = !!run?.metrics?.status && run.metrics.status !== 'SUCCEEDED';

  const openRecord = async (id: number) => {
    const r = await api.crRecord(id);
    if (r.kind === 'cr_correction') { setCor(r); setView('targeted'); }
    else { setCmp(r); setView(r.kind === 'cr_run' ? (r.summary.runs[0]?.mode || 'parallel') : 'sequential'); }
    setRep(0); setSelected(null); setPhase('done'); setPhaseMsg(`Showing record #${id} from the history.`);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <>
      <div className="cr-head">
        <div><h1>Agent Control Room</h1><div className="sub small muted">The real 11-stage workflow in 3D. Load an encounter, launch, and watch the recorded execution.</div></div>
        <Link className="btn btn-primary" to="/present">Present Workflow</Link>
      </div>

      {/* ---------- compact command bar: encounter + launch ---------- */}
      <section className="cr-bar card" aria-label="Encounter and launch">
        <form className="cr-bar-enc" noValidate onSubmit={(e) => { e.preventDefault(); lookup(hadm); }}>
          <label htmlFor="cr-hadm" className="cr-bar-k">Encounter ID</label>
          <input id="cr-hadm" className="input cr-hadm" inputMode="numeric" maxLength={12} value={hadm} list="cr-sugg" aria-invalid={!!lookupErr}
            aria-describedby={lookupErr ? 'cr-hadm-err' : undefined} onChange={(e) => setHadm(e.target.value)} />
          <datalist id="cr-sugg">{sugg.map((c) => <option key={c.id} value={String(c.hadm_id)}>{`${humanize(c.status)} · ${c.root_blocker_title || 'no issue'}`}</option>)}</datalist>
          <Button type="submit" size="sm">Load encounter</Button>
          <Button type="button" size="sm" variant="ghost" busy={finding} onClick={findScenario}>Find a correctable scenario</Button>
        </form>
        {enc && detail && (
          <div className="cr-enc-sum" aria-live="polite">
            <Link to={`/claims/${enc.case.id}`} className="mono">{enc.case.case_ref}</Link>
            <Badge value={enc.case.status} />
            <span className="badge b-advisory">{enc.case.data_source === 'SYNTHETIC' ? 'Synthetic' : enc.case.data_source}</span>
            <span className="small">{(detail.findings || []).length} finding(s) · {(detail.root_blocker?.roots || []).length} root blocker(s) · {(detail.tasks || []).filter((t: any) => t.status === 'OPEN').length} open task(s){enc.case.root_blocker_title ? ` · ${enc.case.root_blocker_title}` : ''}</span>
          </div>)}
        <div className="cr-bar-go">
          <label className="small row" style={{ gap: 6 }}>Repetitions
            <input className="input" type="number" min={1} max={10} value={reps} style={{ width: 58 }} onChange={(e) => setReps(Math.max(1, Math.min(10, Number(e.target.value) || 1)))} /></label>
          <Button variant="primary" className="cr-launch-btn" busy={busy} disabled={!enc || !reg.data || !reg.data.isolated_comparisons_available} onClick={launch}>Launch Workflow Comparison</Button>
          {busy && <Button variant="danger" onClick={cancel}>Stop</Button>}
        </div>
        {lookupErr && <span className="err small cr-bar-err" id="cr-hadm-err" role="alert">{lookupErr}</span>}
        {reg.data && !reg.data.isolated_comparisons_available && <div className="error-state" role="alert">This server does not use the SQLite development database, so isolated comparisons are disabled.</div>}
        {busy && <div className="cr-progress" role="status" aria-live="polite">
          <span className={`dot ${phase === 'compare' ? 'on' : 'done'}`} /> 1 · Sequential vs parallel {phase === 'compare' ? `(running on the backend: ${job?.current || 'starting'})` : '✓'}
          <span className={`dot ${phase === 'correct' ? 'on' : ''}`} /> 2 · Targeted reprocessing {phase === 'correct' ? `(running: ${job?.current || 'starting'})` : ''}
          <span className="faint"> · {liveEvents.length} real event(s) received</span></div>}
        {phaseMsg && !busy && <div className={phase === 'error' ? 'error-state' : 'callout small'} role={phase === 'error' ? 'alert' : 'status'}>{phaseMsg}</div>}
      </section>

      {/* ---------- mode tabs (compact) ---------- */}
      <div className="cr-tabs compact" role="tablist" aria-label="Execution mode">
        {VIEWS.map((v) => (
          <button key={v.id} role="tab" type="button" aria-selected={view === v.id} title={v.text} className={`cr-tab ${view === v.id ? 'on' : ''}`} onClick={() => { setView(v.id); setSelected(null); }}>
            <span className="cr-tab-t">{v.title}</span>
            <span className="cr-tab-m">{v.id === 'targeted' ? (cor ? `${cor.summary.executions_avoided} stage executions avoided` : 'not run yet')
              : cmp?.summary?.stats?.[v.id]?.wall_ms ? `median ${fmtMs(cmp.summary.stats[v.id].wall_ms.median)}` : cmp ? 'recorded' : 'not run yet'}</span>
          </button>))}
      </div>

      {/* ---------- scene (immediately visible) ---------- */}
      {reg.error && <ErrorState message={reg.error} onRetry={reg.reload} />}
      {!reg.data ? <Skeleton rows={8} /> : (
        <section className="cr-stage" aria-label="Workflow execution view">
          {display === '3d' && (
            <SpatialScene stages={reg.data.stages} waves={reg.data.waves} snaps={snaps} selected={selected} onSelect={(id) => setSelected(selected === id ? null : id)}
              playing={replay.playing} height="clamp(420px, calc(100vh - 330px), 680px)"
              overlay={<>
                <Hud view={view} enc={enc} run={busy ? null : run} hud={hud} t={busy ? null : replay.t} busy={busy} job={job} slow={replay.slow} playing={replay.playing} err={err} />
                {selected && <Inspector3D reg={reg.data} id={selected} snap={snaps[selected]} run={run} onClose={() => setSelected(null)} />}
              </>} />)}
          {display === '2d' && <div className="card" style={{ padding: 12 }}>
            <AgentCanvas stages={reg.data.stages} waves={reg.data.waves} snaps={snaps} selected={selected} onSelect={(id) => setSelected(selected === id ? null : id)} replayT={busy ? null : replay.t} />
            {selected && <div className="cr-insp-flat"><Inspector3D reg={reg.data} id={selected} snap={snaps[selected]} run={run} onClose={() => setSelected(null)} /></div>}</div>}
          {display === 'list' && <StageList reg={reg.data} snaps={snaps} selected={selected} onSelect={setSelected} run={run} />}
          <div className="cr-stage-bar">
            <div className="view-switch" role="group" aria-label="Graph view">
              {(['3d', '2d', 'list'] as const).map((d) => <button key={d} type="button" aria-pressed={display === d} onClick={() => setDisplay(d)}>{d === '3d' ? '3D' : d === '2d' ? '2D graph' : 'List'}</button>)}
            </div>
            {view === 'targeted' && cor && <div className="view-switch" role="group" aria-label="Correction arm">
              <button type="button" aria-pressed={arm === 'targeted'} onClick={() => setArm('targeted')}>Targeted re-run</button>
              <button type="button" aria-pressed={arm === 'full'} onClick={() => setArm('full')}>Full re-run</button></div>}
            {view !== 'targeted' && cmp?.summary?.runs && (
              <label className="small row" style={{ gap: 6 }}>Repetition
                <select className="select" value={rep} onChange={(e) => setRep(Number(e.target.value))}>
                  {cmp.summary.runs.filter((r: any) => r.mode === view && !r.warmup).map((r: any, i: number) => <option key={i} value={i}>{r.label} · {fmtMs(r.metrics.wall_ms)}</option>)}
                </select></label>)}
            <span className="small muted cr-mode-desc">{VIEWS.find((v) => v.id === view)!.text}</span>
          </div>
          {run && !busy && <ReplayBar replay={replay} total={total} />}
          {run && !busy && <p className="small faint" style={{ margin: '6px 2px 0' }}>
            Playback of run #{run.pipeline_run_id} ({run.label}) as recorded by the backend. The playback is slowed down so it can be followed; every position, order and duration is the measured value. Authoritative totals are in the timeline below.</p>}
          {!run && !busy && <p className="small muted" style={{ margin: '6px 2px 0' }}>No {view === 'targeted' ? 'targeted reprocessing ' : ''}run yet. Load an encounter and press <strong>Launch Workflow Comparison</strong>; until then the scene shows the real workflow at rest.</p>}
        </section>)}

      {/* ---------- context: what launch does, disclosure, correction preview ---------- */}
      <div className="cr-disclosure" role="note">
        <strong>Rule-based stages, real execution.</strong> {reg.data?.disclosure || 'ClearClaim orchestrates 11 rule-based workflow stages.'}
        <span className="cr-research">{RESEARCH_NOTE}</span>
        <span className="small"><strong>Launch Workflow Comparison</strong> runs, on isolated copies of the database (live data is not changed): (1) sequential and parallel execution of all 11 stages on the same input snapshot, {reps}× each after a warm-up pair; (2) a separate targeted reprocessing experiment with the correction below, re-run with the full and the targeted policy. The second measures correction behaviour, not initial speed.</span>
      </div>
      {detail && (
        <section className="cr-corr-prev card" aria-label="Correction scenario">
          {auto ? (
            <div className="row" style={{ alignItems: 'flex-start', gap: 18 }}>
              <div style={{ flex: '1 1 300px' }}><div className="small muted">Issue to correct (targeted reprocessing experiment)</div>
                <strong>{auto.root.title}</strong><div className="small">{auto.root.message}</div>
                {(auto.root.expected != null || auto.root.actual != null) && <div className="small">Expected <strong>{auto.root.expected}</strong> · recorded <strong>{auto.root.actual}</strong>{auto.root.amount ? ` · ${money(auto.root.amount)}` : ''}</div>}</div>
              <div style={{ flex: '1 1 260px' }}><div className="small muted">Correction applied</div><span className="mono small">{auto.draft.op}</span> <span className="small">on the {(DOMAIN_LABELS[auto.root.owner_domain] || auto.root.owner_domain).toLowerCase()}</span></div>
              <div style={{ flex: '2 1 360px' }}><div className="small muted">Stages invalidated by this change ({auto.root.rerun_stages.length} of 11)</div>
                <div className="flow">{auto.root.rerun_stages.map((s: string) => <span key={s} className="stage-chip hot">{stageLabel(s)}</span>)}</div></div>
            </div>
          ) : <div className="small muted">This encounter has no correction that is fully determined by its evidence, so the targeted reprocessing experiment cannot run for it. Sequential and parallel still can. Use <strong>Find a correctable scenario</strong> to pick one.</div>}
        </section>)}

      {/* ---------- timeline and metrics ---------- */}
      {run && !busy && (
        <Card title="Measured execution timeline" meta={`${VIEWS.find((v) => v.id === view)!.title} · ${run.mode} · ${run.rerun_policy} policy · run #${run.pipeline_run_id}`}
          actions={seqRun && parRun && view !== 'targeted' ? <Button size="sm" aria-pressed={sideBySide} onClick={() => setSideBySide((x) => !x)}>{sideBySide ? 'Single timeline' : 'Compare Timelines'}</Button> : undefined}>
          {err && <div className="error-state" role="alert"><strong>This run did not succeed ({run.metrics.status}).</strong> Failed stages: {run.metrics.stages_failed}. It is not reported as a successful preparation.</div>}
          <MetricStrip m={run.metrics} />
          <div className="small faint" style={{ margin: '6px 0' }}>Output fingerprint <span className="mono">{run.fingerprint?.hash?.slice(0, 16)}…</span> · input snapshot <span className="mono">{run.input_snapshot_hash?.slice(0, 12)}…</span></div>
          {sideBySide && seqRun && parRun && view !== 'targeted' ? (() => {
            const shared = Math.max(lastTime(seqRun.events), lastTime(parRun.events));
            return (
              <div className="tl-pair">
                <GanttTimeline title={`Sequential · ${seqRun.label} · wall ${fmtMs(seqRun.metrics.wall_ms)}`} events={seqRun.events} stageIds={ids} metrics={seqRun.metrics} scaleEnd={shared} width={520} selected={selected} onSelect={setSelected} />
                <GanttTimeline title={`Parallel · ${parRun.label} · wall ${fmtMs(parRun.metrics.wall_ms)}`} events={parRun.events} stageIds={ids} metrics={parRun.metrics} scaleEnd={shared} width={520} selected={selected} onSelect={setSelected} />
                <p className="small" style={{ gridColumn: '1 / -1', margin: 0 }}>Same horizontal scale ({fmtMs(shared)}). In this repetition parallel {parRun.metrics.wall_ms < seqRun.metrics.wall_ms ? 'finished sooner' : 'did not finish sooner'} ({fmtMs(seqRun.metrics.wall_ms)} sequential vs {fmtMs(parRun.metrics.wall_ms)} parallel); maximum concurrency {parRun.metrics.max_concurrency}. Judge by the medians below, not one repetition.</p>
              </div>);
          })() : <GanttTimeline events={run.events} stageIds={ids} metrics={run.metrics} selected={selected} onSelect={setSelected} replayT={replay.t} />}
        </Card>)}

      {cmp?.kind === 'cr_comparison' && !cmp.summary.cancelled && view !== 'targeted' && <ComparisonPanel rec={cmp} />}
      {cor && view === 'targeted' && <CorrectionPanel rec={cor} stageIds={ids} />}

      <History hist={hist} onOpen={openRecord} cmpIds={cmpIds} setCmpIds={setCmpIds} />
    </>
  );
}

function Hud({ view, enc, run, hud, t, busy, job, slow, playing, err }: any) {
  const m = run?.metrics;
  return (
    <div className="hud s3d-ui" aria-live="polite">
      <div className="hud-id"><span className="hud-mode">{VIEWS.find((v) => v.id === view)!.title}</span>
        <span className="hud-k">Encounter {enc?.case?.hadm_id ?? '—'}{run ? ` · run #${run.pipeline_run_id}` : ''}</span></div>
      {busy ? (
        <div className="hud-main"><span className="hud-k">Status</span><span className="hud-big">Executing on backend</span>
          <span className="hud-note">{job?.current || 'starting'} · events arrive as the orchestrator reports them</span></div>
      ) : run ? (<>
        <div className="hud-main"><span className="hud-k">{t === null ? 'Wall-clock (measured)' : playing ? `Playback · ${slow === 1 ? 'real time' : `${slow}× slower than real`}` : 'Playback paused at'}</span>
          <span className="hud-big">{fmtMs(t === null ? m?.wall_ms : t)}</span></div>
        <div className="hud-grid">
          <div><span className="hud-k">Done</span><b>{hud.completed}/11</b></div>
          <div><span className="hud-k">Running</span><b>{hud.running}</b></div>
          <div><span className="hud-k">Waiting</span><b>{hud.waiting}</b></div>
          <div><span className="hud-k">Max conc.</span><b>{hud.maxConc}</b></div>
          <div><span className="hud-k">Reused</span><b>{hud.reused + hud.notrun}</b></div>
          <div><span className="hud-k">Errors</span><b className={hud.failed ? 'hud-err' : ''}>{hud.failed + hud.skipped}</b></div>
        </div>
        <div className="hud-note">{err ? <span className="hud-err">Run FAILED. </span> : null}Result <strong>{humanize(m?.outcome_status)}</strong><br />measured wall {fmtMs(m?.wall_ms)} · end-to-end {fmtMs(m?.end_to_end_ms)}</div></>
      ) : <div className="hud-note idle">Idle. The graph shows the real 11-stage workflow; nothing is running.</div>}
    </div>
  );
}

function Inspector3D({ reg, id, snap, run, onClose }: { reg: any; id: string; snap: StageSnap; run: any; onClose: () => void }) {
  const st = reg.stages.find((s: any) => s.id === id);
  const fin = (run?.events || []).find((e: any) => e.type === 'stage_finished' && e.stage === id);
  const why = snap?.reason || (fin ? 'full re-run policy: every stage executes' : undefined);
  return (
    <aside className="insp-float s3d-ui" aria-label={`Inspector: ${stageLabel(id)}`}>
      <button type="button" className="x" onClick={onClose} aria-label="Close inspector">✕</button>
      <div className="hud-k">Stage {reg.stages.indexOf(st) + 1} of 11 · rule-based Python component</div>
      <h3>{stageLabel(id)}</h3>
      <p className="small" style={{ margin: '6px 0' }}>{st.purpose}</p>
      <dl>
        <dt>Status</dt><dd>{STATE_LABEL[snap?.state || 'waiting']}</dd>
        <dt>Why</dt><dd>{why || 'no event for this stage yet'}</dd>
        <dt>Start → finish</dt><dd>{snap?.start !== undefined ? `${fmtMs(snap.start)} → ${fmtMs(snap.end)}` : 'not executed in this run'}</dd>
        <dt>Duration</dt><dd>{snap?.duration !== undefined ? fmtMs(snap.duration) : '—'}{snap?.attempts ? ` · attempt ${snap.attempts}` : ''}</dd>
        {snap?.readyAt !== undefined && <><dt>Ready at</dt><dd>{fmtMs(snap.readyAt)} after run start</dd></>}
        {snap?.error && <><dt>Error</dt><dd className="hud-err">{snap.error}</dd></>}
        <dt>Source</dt><dd className="mono small">{st.implementation}</dd>
        <dt>Reads</dt><dd>{st.input_domains.map((d: string) => DOMAIN_LABELS[d] || d).join(', ') || 'results of earlier stages only'}</dd>
        <dt>Needs</dt><dd>{st.deps.map(stageLabel).join(', ') || 'nothing (first stage)'}</dd>
        <dt>Feeds</dt><dd>{st.downstream.map(stageLabel).join(', ') || 'nothing (final stage)'}</dd>
        <dt>Output hash</dt><dd className="mono small">{snap?.outputHash ? `${snap.outputHash.slice(0, 16)}…` : '—'}</dd>
      </dl>
      <div className="small"><strong>Checks this stage performs</strong></div>
      <div>{st.checks.length ? st.checks.map((c: any) => <span key={c.code} className="chipx">{c.title}</span>) : <span className="small muted">{st.note}</span>}</div>
      <div className="small" style={{ marginTop: 10 }}><strong>Findings in this run</strong></div>
      {!fin ? <div className="small muted">{snap?.state === 'reused' ? 'Reused: its previous output (and findings) were kept because its inputs were identical.' : snap?.state === 'notrun' ? 'Not re-run: nothing it reads changed.' : 'No execution recorded yet.'}</div>
        : fin.findings.length === 0 ? <div className="small muted">None: every check passed.</div>
          : <ul className="small" style={{ paddingLeft: 16, margin: '4px 0' }}>{fin.findings.map((f: any, i: number) => (
            <li key={i} style={{ marginBottom: 6 }}><span className={`sev ${f.severity}`}>{f.severity}</span><strong>{f.title}</strong>
              {f.message && <div>{f.message}</div>}
              {(f.expected != null || f.actual != null) && <div className="muted">Expected {String(f.expected ?? '—')} · recorded {String(f.actual ?? '—')}</div>}
              {f.rule_id && <div className="muted mono">{f.rule_id}{f.source_document ? ` · ${f.source_document}${f.source_page ? ` p.${f.source_page}` : ''}` : ''}</div>}</li>))}</ul>}
      {fin?.output_summary && <details className="small"><summary>Output summary (identifiers hidden)</summary><pre>{JSON.stringify(fin.output_summary, null, 1)}</pre></details>}
      <p className="small faint" style={{ marginTop: 10 }}>What this means: the stage is ordinary Python code applying fixed rules. It does not reason or learn; the same inputs always give the same output.</p>
    </aside>
  );
}

function StageList({ reg, snaps, selected, onSelect, run }: any) {
  return (
    <div className="card table-wrap">
      <table className="tbl">
        <caption className="visually-hidden">Workflow stages in execution order with their recorded state</caption>
        <thead><tr><th>#</th><th>Stage</th><th>State</th><th className="num">Start</th><th className="num">Duration</th><th>Findings</th><th>Needs</th></tr></thead>
        <tbody>{reg.stages.map((s: any, i: number) => {
          const sn = snaps[s.id];
          return (
            <tr key={s.id} className={selected === s.id ? 'flag' : ''} data-stage={s.id} data-state={sn.state}>
              <td>{i + 1}</td><td><button type="button" className="linkish" onClick={() => onSelect(selected === s.id ? null : s.id)}>{stageLabel(s.id)}</button></td>
              <td className="small">{STATE_LABEL[sn.state as keyof typeof STATE_LABEL]}</td>
              <td className="num small">{sn.start !== undefined ? fmtMs(sn.start) : '—'}</td><td className="num small">{sn.duration !== undefined ? fmtMs(sn.duration) : '—'}</td>
              <td className="small">{(sn.findings || []).map((f: any) => f.title).join('; ') || '—'}</td>
              <td className="small">{s.deps.map(stageLabel).join(', ') || '—'}</td>
            </tr>);
        })}</tbody>
      </table>
      {selected && <div className="cr-insp-flat"><Inspector3D reg={reg} id={selected} snap={snaps[selected]} run={run} onClose={() => onSelect(null)} /></div>}
    </div>
  );
}

function History({ hist, onOpen, cmpIds, setCmpIds }: any) {
  return (
    <Card title="Run history" meta="every launch is kept; failed and cancelled runs too" flush>
      {hist.error ? <div style={{ padding: 14 }}><ErrorState message={hist.error} onRetry={hist.reload} /></div>
        : !hist.data ? <div style={{ padding: 14 }}><Skeleton rows={3} /></div>
        : hist.data.length === 0 ? <EmptyState title="No runs yet">Launch a comparison above; every run is recorded here.</EmptyState> : (
          <>
            <div className="table-wrap"><table className="tbl">
              <caption className="visually-hidden">Control room run history</caption>
              <thead><tr><th>Compare</th><th>#</th><th>Experiment</th><th>Encounter</th><th>Started</th><th className="num">Result</th><th>Status</th><th>Outputs match</th><th /></tr></thead>
              <tbody>{hist.data.map((h: any) => (
                <tr key={h.id}>
                  <td><input type="checkbox" aria-label={`Select record ${h.id} for comparison`} checked={cmpIds.includes(h.id)}
                    onChange={(e) => setCmpIds((x: number[]) => e.target.checked ? [...x, h.id].slice(-2) : x.filter((y) => y !== h.id))} /></td>
                  <td>{h.id}</td>
                  <td className="small">{h.kind === 'cr_run' ? `single ${h.mode} run` : h.kind === 'cr_comparison' ? `sequential vs parallel ×${h.repetitions}` : 'targeted reprocessing'}</td>
                  <td className="mono small">{h.hadm_id}</td><td className="small faint">{h.started_at?.replace('T', ' ').slice(0, 19)}</td>
                  <td className="num small">{h.kind === 'cr_comparison' ? (h.wall_reduction_pct === null || h.wall_reduction_pct === undefined ? '—' : `parallel ${h.wall_reduction_pct >= 0 ? `${h.wall_reduction_pct.toFixed(1)}% faster` : `${Math.abs(h.wall_reduction_pct).toFixed(1)}% slower`}`) : fmtMs(h.wall_ms)}</td>
                  <td><Badge value={h.status} /></td>
                  <td className="small">{h.outputs_match === undefined || h.outputs_match === null ? '—' : h.outputs_match ? 'yes' : <strong style={{ color: 'var(--blocked)' }}>NO</strong>}</td>
                  <td><Button size="sm" onClick={() => onOpen(h.id)}>Open</Button></td>
                </tr>))}</tbody>
            </table></div>
            {cmpIds.length === 2 && <TwoRecords ids={cmpIds} />}
          </>)}
    </Card>
  );
}
