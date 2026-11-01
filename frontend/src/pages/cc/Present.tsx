import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { pct, stageLabel } from '../../lib/format';
import { fmtMs, lastTime, stagesAt } from '../../lib/controlRoom';
import { Button, Skeleton } from '../../components/ui';
import SpatialScene from '../../components/SpatialScene';
import GanttTimeline, { MetricStrip } from '../../components/GanttTimeline';
import TimeStudy from '../../components/TimeStudy';
import { ComparisonPanel, CorrectionPanel, RESEARCH_NOTE, ReplayBar, useJob, useReplay } from './controlRoomParts';
import { autoCorrection, hudAt, runFor } from './ControlRoom';

/* eslint-disable @typescript-eslint/no-explicit-any */
const SCENES = ['The problem', 'The proposed solution', 'Meet the 11 workflow stages', 'How the stages depend on each other',
  'Sequential execution', 'Parallel execution', 'Timelines side by side', 'Correction and targeted reprocessing',
  'What the experiments found', 'Limitations and future work'];

export default function Present() {
  const nav = useNavigate();
  const reg = useApi<any>(() => api.crStages());
  const hist = useApi<any[]>(() => api.crHistory());
  const [i, setI] = useState(0);
  const [hadm, setHadm] = useState('98000056');
  const [cmp, setCmp] = useState<any>(null);
  const [cor, setCor] = useState<any>(null);
  // reuse the most recent recorded experiments; nothing is executed by opening a scene
  useEffect(() => {
    const h = hist.data || [];
    const c = h.find((x: any) => x.kind === 'cr_comparison' && x.status === 'COMPLETED');
    const k = h.find((x: any) => x.kind === 'cr_correction' && x.status === 'COMPLETED');
    if (c && cmp?.id !== c.id) api.crRecord(c.id).then(setCmp).catch(() => undefined);
    if (k && cor?.id !== k.id) api.crRecord(k.id).then(setCor).catch(() => undefined);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hist.data]);
  const go = useCallback((d: number) => setI((x) => Math.max(0, Math.min(SCENES.length - 1, x + d))), []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest?.('input, select, textarea')) return;
      if (e.key === 'ArrowRight' || e.key === 'PageDown') go(1);
      if (e.key === 'ArrowLeft' || e.key === 'PageUp') go(-1);
      if (e.key === 'Escape') nav('/control-room');
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [go, nav]);
  const ids: string[] = reg.data?.stages.map((s: any) => s.id) || [];

  return (
    <div className="present" role="dialog" aria-modal="true" aria-label="ClearClaim presentation mode">
      <header className="pr-top">
        <span className="pr-brand">ClearClaim · Present Workflow</span>
        <span className="pr-badge">Synthetic demo data</span>
        <nav className="pr-dots" aria-label="Scenes">
          {SCENES.map((s, k) => <button key={s} type="button" aria-current={k === i ? 'step' : undefined} aria-label={`Scene ${k + 1}: ${s}`} onClick={() => setI(k)} />)}
        </nav>
        <Button size="sm" onClick={() => nav('/control-room')}>Exit presentation</Button>
      </header>
      <main className="pr-stage" key={i}>
        <p className="pr-eyebrow">Scene {i + 1} of {SCENES.length}</p>
        <h1 className="pr-title">{SCENES[i]}</h1>
        {!reg.data ? <Skeleton rows={4} /> : (
          <>
            {i === 0 && <Cards items={[
              ['Many separate checks', 'Before a claim can be prepared, staff check the clinical record, discharge documents, the itemised bill, the policy rules and the coverage amount.'],
              ['One error, many symptoms', 'A single wrong bill line can make billing, coverage and reconciliation all look wrong. Without the root cause, people chase symptoms.'],
              ['Repeated work', 'After each correction the usual answer is to check everything again, even what the correction cannot have changed.']]} />}
            {i === 1 && <Cards items={[
              ['Find the root blocker', 'Documented causal rules separate the issue to fix from its downstream consequences.'],
              ['Show the evidence', 'Expected vs recorded values, the policy rule and page, the owner and the suggested correction.'],
              ['Re-run only what changed', 'After a correction, only stages that read the changed data are re-checked; identical inputs are reused.'],
              ['Keep an audit trail', 'Every correction, decision and re-run is recorded with before/after values.']]} note={reg.data.disclosure} />}
            {i === 2 && <div className="pr-stages">{reg.data.stages.map((s: any, k: number) => (
              <div key={s.id} className="pr-stage-card" style={{ animationDelay: `${k * 60}ms` }}><span className="pr-n">{k + 1}</span><strong>{stageLabel(s.id)}</strong><p>{s.purpose}</p></div>))}</div>}
            {i === 3 && <SceneGraph reg={reg.data} />}
            {(i === 4 || i === 5) && <ScenePlay reg={reg.data} view={i === 4 ? 'sequential' : 'parallel'} cmp={cmp} hadm={hadm} setHadm={setHadm}
              onRecorded={(r: any) => { setCmp(r); hist.reload(); }} />}
            {i === 6 && <SceneTimelines cmp={cmp} ids={ids} />}
            {i === 7 && <SceneCorrection reg={reg.data} cor={cor} hadm={hadm} setHadm={setHadm} onRecorded={(r: any) => { setCor(r); hist.reload(); }} />}
            {i === 8 && <SceneFindings cmp={cmp} cor={cor} />}
            {i === 9 && <Cards items={[
              ['Data', 'Synthetic billing and insurance data; the benchmark also uses real MIMIC-IV demo encounters. No real insurer outcomes.'],
              ['Agents', 'The 11 stages are deterministic rule-based functions, not autonomous AI agents.'],
              ['Speed', 'Stages take milliseconds and only two are independent, so parallelism has little room to help; overhead dominates.'],
              ['Time', 'Simulated human minutes are assumptions. No hospital time-and-motion study was done; faster software does not prove faster discharge.'],
              ['Future work', 'Observed hospital timings, a reviewer user study, credentialed MIMIC-IV notes, and stages heavy enough for parallelism to matter.']]} note={RESEARCH_NOTE} />}
          </>)}
      </main>
      <footer className="pr-foot">
        <Button onClick={() => go(-1)} disabled={i === 0}>← Previous</Button>
        <span className="small">{RESEARCH_NOTE}</span>
        <Button variant="primary" onClick={() => go(1)} disabled={i === SCENES.length - 1}>Next →</Button>
      </footer>
    </div>
  );
}

function Cards({ items, note }: { items: [string, string][]; note?: string }) {
  return (
    <div className="pr-cols">
      {items.map(([h, t]) => <div key={h} className="pr-card"><h2>{h}</h2><p>{t}</p></div>)}
      {note && <p className="pr-note">{note}</p>}
    </div>
  );
}

function SceneGraph({ reg }: { reg: any }) {
  const [sel, setSel] = useState<string | null>(null);
  const snaps = useMemo(() => stagesAt([], reg.stages.map((s: any) => s.id), null), [reg]);
  const st = sel ? reg.stages.find((s: any) => s.id === sel) : null;
  return (
    <div className="stack">
      <SpatialScene stages={reg.stages} waves={reg.waves} snaps={snaps} selected={sel} onSelect={(id) => setSel(sel === id ? null : id)} height={520} />
      <p className="pr-note">{st ? <><strong>{stageLabel(st.id)}</strong> needs {st.deps.map(stageLabel).join(', ') || 'nothing'} and feeds {st.downstream.map(stageLabel).join(', ') || 'nothing'}. </> : 'Select a stage to highlight what it needs and what it feeds. '}
        These are the real dependencies from the code. Only billing verification and insurance eligibility are independent of each other.</p>
    </div>
  );
}

function ScenePlay({ reg, view, cmp, hadm, setHadm, onRecorded }: any) {
  const { job, start } = useJob();
  const ids = reg.stages.map((s: any) => s.id);
  const run = runFor(view, cmp, null, 0, 'targeted');
  const replay = useReplay(lastTime(run?.events || []) || 1);
  const busy = job && ['starting', 'queued', 'running'].includes(job.status);
  const hud = useMemo(() => hudAt(run, ids, replay.t), [run, ids, replay.t]);
  return (
    <div className="stack">
      <div className="row">
        <label className="small row" style={{ gap: 6 }}>Encounter ID <input className="input" value={hadm} onChange={(e) => setHadm(e.target.value)} style={{ width: 130 }} inputMode="numeric" /></label>
        <Button busy={!!busy} onClick={() => start(() => api.crCompare({ hadm_id: hadm, repetitions: 3 }), async (rid) => { if (rid) onRecorded(await api.crRecord(rid)); })}>Run a new measured comparison now</Button>
        {run && <Button variant="primary" onClick={() => { replay.setT(0); replay.setPlaying(true); }}>Replay recorded {view} run</Button>}
        <span className="small muted">{view === 'sequential' ? "Each stage runs one at a time, respecting the workflow's dependencies." : 'Independent stages run concurrently when their dependencies permit it.'}</span>
      </div>
      {job && ['error', 'rejected'].includes(job.status) && <div className="error-state" role="alert">{job.error}</div>}
      {!run && !busy && <p className="pr-note">No recorded comparison yet. Press “Run a new measured comparison now”; nothing runs until you do.</p>}
      <SpatialScene stages={reg.stages} waves={reg.waves} snaps={hud.snaps} selected={null} onSelect={() => undefined} playing={replay.playing} height={480}
        overlay={run ? <div className="hud s3d-ui"><div className="hud-id"><span className="hud-mode">{view.toUpperCase()} MODE</span><span className="hud-k">run #{run.pipeline_run_id} · encounter {cmp?.config?.hadm_id}</span></div>
          <div className="hud-main"><span className="hud-k">{replay.t === null ? 'Wall-clock (measured)' : `Playback · ${replay.slow}× slower than real`}</span><span className="hud-big">{fmtMs(replay.t === null ? run.metrics.wall_ms : replay.t)}</span></div>
          <div className="hud-grid"><div><span className="hud-k">Done</span><b>{hud.completed}/11</b></div><div><span className="hud-k">Running</span><b>{hud.running}</b></div><div><span className="hud-k">Max conc.</span><b>{hud.maxConc}</b></div></div></div> : undefined} />
      {run && <ReplayBar replay={replay} total={lastTime(run.events)} />}
      {run && <MetricStrip m={run.metrics} />}
    </div>
  );
}

function SceneTimelines({ cmp, ids }: { cmp: any; ids: string[] }) {
  const s = runFor('sequential', cmp, null, 0, 'targeted'), p = runFor('parallel', cmp, null, 0, 'targeted');
  if (!s || !p) return <p className="pr-note">No recorded comparison yet. Run one in scene 5 or in the Agent Control Room.</p>;
  const shared = Math.max(lastTime(s.events), lastTime(p.events));
  const st = cmp.summary.stats;
  return (
    <div className="stack">
      <div className="tl-pair">
        <GanttTimeline title={`Sequential · wall ${fmtMs(s.metrics.wall_ms)}`} events={s.events} stageIds={ids} metrics={s.metrics} scaleEnd={shared} width={520} />
        <GanttTimeline title={`Parallel · wall ${fmtMs(p.metrics.wall_ms)}`} events={p.events} stageIds={ids} metrics={p.metrics} scaleEnd={shared} width={520} />
      </div>
      <p className="pr-note">Same time scale. Across {cmp.summary.repetitions} repetitions the median wall-clock was {fmtMs(st.sequential.wall_ms?.median)} sequential and {fmtMs(st.parallel.wall_ms?.median)} parallel
        ({cmp.summary.wall_reduction_pct === null ? 'n/a' : cmp.summary.wall_reduction_pct >= 0 ? `${cmp.summary.wall_reduction_pct.toFixed(1)}% faster` : `${Math.abs(cmp.summary.wall_reduction_pct).toFixed(1)}% slower`} in parallel). Final outputs {cmp.summary.outputs_match ? 'were identical' : 'DIFFERED'}.</p>
    </div>
  );
}

function SceneCorrection({ reg, cor, hadm, setHadm, onRecorded }: any) {
  const { job, start } = useJob();
  const [err, setErr] = useState<string | null>(null);
  const [arm, setArm] = useState<'targeted' | 'full'>('targeted');
  const ids = reg.stages.map((s: any) => s.id);
  const run = runFor('targeted', null, cor, 0, arm);
  const replay = useReplay(lastTime(run?.events || []) || 1);
  const hud = useMemo(() => hudAt(run, ids, replay.t), [run, ids, replay.t]);
  const busy = job && ['starting', 'queued', 'running'].includes(job.status);
  const runIt = async () => {
    setErr(null);
    try {
      const e = await api.studioEncounter(hadm);
      const a = autoCorrection(await api.caseDetail(e.case.id));
      if (!a) { setErr('This encounter has no correction determined by its evidence. Try 98000056 on fresh demo data.'); return; }
      start(() => api.crCorrection({ hadm_id: hadm, correction: a.body }), async (rid) => { if (rid) onRecorded(await api.crRecord(rid)); });
    } catch (x) { setErr((x as Error).message); }
  };
  return (
    <div className="stack">
      <div className="row">
        <label className="small row" style={{ gap: 6 }}>Encounter ID <input className="input" value={hadm} onChange={(e) => setHadm(e.target.value)} style={{ width: 130 }} inputMode="numeric" /></label>
        <Button busy={!!busy} onClick={runIt}>Run correction comparison now</Button>
        {run && <div className="view-switch" role="group" aria-label="Arm"><button type="button" aria-pressed={arm === 'targeted'} onClick={() => setArm('targeted')}>Targeted re-run</button><button type="button" aria-pressed={arm === 'full'} onClick={() => setArm('full')}>Full re-run</button></div>}
        {run && <Button variant="primary" onClick={() => { replay.setT(0); replay.setPlaying(true); }}>Replay recorded run</Button>}
      </div>
      {(err || (job && ['error', 'rejected'].includes(job.status))) && <div className="error-state" role="alert">{err || job.error}</div>}
      {!cor && !busy && <p className="pr-note">No recorded correction comparison yet. Nothing runs until you press the button.</p>}
      {run && <SpatialScene stages={reg.stages} waves={reg.waves} snaps={hud.snaps} selected={null} onSelect={() => undefined} playing={replay.playing} height={440} />}
      {run && <ReplayBar replay={replay} total={lastTime(run.events)} />}
      {cor && <CorrectionPanel rec={cor} stageIds={ids} />}
    </div>
  );
}

function SceneFindings({ cmp, cor }: { cmp: any; cor: any }) {
  const exps = useApi<any[]>(() => api.experiments());
  const e = (exps.data || []).find((x) => x.status === 'COMPLETED' && x.has_summary);
  const ed = useApi<any>(() => (e ? api.experiment(e.id) : Promise.resolve(null)), [e?.id]);
  const agg = ed.data?.summary?.aggregate;
  const loopB = agg?.B_sequential_full?.loop_stage_executions?.mean, loopC = agg?.C_parallel_targeted?.loop_stage_executions?.mean;
  return (
    <div className="stack">
      {cmp ? <ComparisonPanel rec={cmp} /> : <p className="pr-note">No sequential vs parallel comparison recorded yet.</p>}
      <div className="pr-cols">
        <div className="pr-card"><h2>Correction</h2><p>{cor ? `Targeted re-running avoided ${cor.summary.executions_avoided} of ${cor.summary.runs[0].metrics.stages_executed} stage executions; final outputs ${cor.summary.outputs_match ? 'identical' : 'DIFFERENT'}.` : 'Not recorded yet.'}</p></div>
        <div className="pr-card"><h2>Benchmark</h2><p>{loopB && loopC ? `Across the recorded synthetic benchmark, targeted re-running cut correction-loop stage executions from ${loopB.toFixed(0)} to ${loopC.toFixed(0)} (${pct(1 - loopC / loopB)} fewer).` : 'Load the recorded benchmark on the Experiments page.'}</p></div>
      </div>
      {!cmp && <TimeStudy measured={null} />}
    </div>
  );
}
