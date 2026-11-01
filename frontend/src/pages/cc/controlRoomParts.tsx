import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { humanize, stageLabel } from '../../lib/format';
import { fmtMs, pctChange, stagesAt, STATE_LABEL } from '../../lib/controlRoom';
import { Button, Card, Skeleton } from '../../components/ui';
import TimeStudy from '../../components/TimeStudy';

/* eslint-disable @typescript-eslint/no-explicit-any */
export const RESEARCH_NOTE = 'ClearClaim is a research prototype. Results are not insurer decisions and do not replace clinical or insurance review.';


/** Poll a control-room job until it finishes; returns live events through onEvents. */
export function useJob() {
  const [job, setJob] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const timer = useRef<number | null>(null);
  const tokenRef = useRef<string | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const cancel = useCallback(async () => { if (tokenRef.current) await api.crCancel(tokenRef.current); }, []);
  const start = useCallback(async (starter: () => Promise<any>, onDone: (recordId: number | null, j: any) => void) => {
    setEvents([]); setJob({ status: 'starting' });
    let seen: any[] = [];
    try {
      const { token } = await starter();
      tokenRef.current = token;
      const tick = async (after: number) => {
        try {
          const j = await api.crJob(token, after);
          seen = [...seen, ...j.events];
          setEvents(seen); setJob(j);
          if (j.status === 'queued' || j.status === 'running') { timer.current = window.setTimeout(() => tick(j.next_after), 100); return; }
          onDone(j.record_id, j);
        } catch (e) { setJob({ status: 'error', error: (e as Error).message }); }
      };
      tick(0);
    } catch (e) { setJob({ status: 'error', error: (e as ApiError).message, http_status: (e as ApiError).status }); }
  }, []);
  return { job, events, start, setJob, cancel };
}

/** Replay of recorded events at a chosen slow-down; t is ms since the recorded run started. */
export function useReplay(total: number) {
  const [t, setT] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [slow, setSlow] = useState(500);
  const raf = useRef<number | null>(null);
  const last = useRef<number>(0);
  useEffect(() => {
    if (!playing) return undefined;
    const step = (now: number) => {
      const dt = now - last.current; last.current = now;
      setT((x) => {
        const nx = (x ?? 0) + dt / slow;
        if (nx >= total) { setPlaying(false); return total; }
        return nx;
      });
      raf.current = requestAnimationFrame(step);
    };
    last.current = performance.now();
    raf.current = requestAnimationFrame(step);
    return () => { if (raf.current) cancelAnimationFrame(raf.current); };
  }, [playing, slow, total]);
  return { t, setT, playing, setPlaying, slow, setSlow, play: () => { setT((x) => (x === null || x >= total ? 0 : x)); setPlaying(true); }, stop: () => { setPlaying(false); setT(null); } };
}

export function ReplayBar({ replay, total }: { replay: ReturnType<typeof useReplay>; total: number }) {
  return (
    <div className="replay" role="group" aria-label="Replay recorded execution">
      <Button size="sm" onClick={replay.playing ? () => replay.setPlaying(false) : replay.play}>{replay.playing ? 'Pause' : replay.t === null ? 'Replay execution timeline' : 'Resume'}</Button>
      {replay.t !== null && <Button size="sm" variant="ghost" onClick={replay.stop}>Show final state</Button>}
      <label className="small row" style={{ gap: 6 }}>Playback
        <select className="select" value={replay.slow} onChange={(e) => replay.setSlow(Number(e.target.value))} aria-label="Playback slow-down">
          {[1, 100, 500, 1000, 5000].map((v) => <option key={v} value={v}>{v === 1 ? 'real time' : `${v}× slower`}</option>)}
        </select></label>
      <input type="range" min={0} max={total} step={total / 400 || 0.001} value={replay.t ?? total} aria-label="Replay position (ms since run start)"
        onChange={(e) => { replay.setPlaying(false); replay.setT(Number(e.target.value)); }} style={{ flex: 1, minWidth: 140 }} />
      <span className="small mono">{replay.t === null ? 'final state' : `${fmtMs(replay.t)} / ${fmtMs(total)}`}</span>
      {replay.t !== null && <span className="badge b-advisory">REPLAY of recorded events — not running now</span>}
    </div>
  );
}

export function ComparisonPanel({ rec }: { rec: any }) {
  const s = rec.summary;
  const st = s.stats;
  const red = s.wall_reduction_pct;
  const rows: [string, string][] = [['Wall-clock', 'wall_ms'], ['End-to-end (incl. commit)', 'end_to_end_ms'], ['Sum of stage durations', 'stage_sum_ms'],
    ['Critical path', 'critical_path_ms'], ['Overhead outside stages', 'overhead_ms'], ['Max concurrency', 'max_concurrency']];
  const v = (m: string, k: string, f: string) => { const x = st?.[m]?.[k]?.[f]; return x === undefined || x === null ? '—' : k === 'max_concurrency' ? String(x) : fmtMs(x); };
  return (
    <Card title="Sequential vs parallel: measured comparison" meta={`${s.repetitions} repetition(s) per mode · isolated copy · ${rec.config.hadm_id}`}
      actions={<><a className="btn btn-sm" href={api.crExportUrl(rec.id, 'csv')}>Export CSV</a><a className="btn btn-sm" href={api.crExportUrl(rec.id, 'json')}>Export JSON</a></>}>
      {!s.outputs_match && <div className="error-state" role="alert"><strong>Outputs DIFFER between runs ({s.distinct_output_fingerprints} distinct fingerprints).</strong> The timing comparison is not of equivalent work and must not be reported as such.</div>}
      <div className="cmp-hero">
        <div><span>Sequential median wall-clock</span><strong>{v('sequential', 'wall_ms', 'median')}</strong></div>
        <div><span>Parallel median wall-clock</span><strong>{v('parallel', 'wall_ms', 'median')}</strong></div>
        <div><span>Difference</span><strong className={red !== null && red < 0 ? 'neg' : ''}>{red === null ? 'unavailable' : red >= 0 ? `${red.toFixed(1)}% faster` : `${Math.abs(red).toFixed(1)}% slower`}</strong>
          <em>{st?.sequential?.wall_ms && st?.parallel?.wall_ms ? `${fmtMs(Math.abs(st.sequential.wall_ms.median - st.parallel.wall_ms.median))} absolute` : ''}</em></div>
        <div><span>Final outputs</span><strong>{s.outputs_match ? 'identical' : 'DIFFERENT'}</strong><em>{s.same_input_snapshot ? 'same input snapshot' : 'input snapshots differ'}</em></div>
      </div>
      <div className="table-wrap"><table className="tbl">
        <caption className="visually-hidden">Median, minimum and maximum per mode</caption>
        <thead><tr><th>Metric</th><th className="num">Sequential median</th><th className="num">min – max</th><th className="num">Parallel median</th><th className="num">min – max</th></tr></thead>
        <tbody>{rows.map(([label, k]) => (
          <tr key={k}><td>{label}</td><td className="num">{v('sequential', k, 'median')}</td><td className="num small">{v('sequential', k, 'min')} – {v('sequential', k, 'max')}</td>
            <td className="num">{v('parallel', k, 'median')}</td><td className="num small">{v('parallel', k, 'min')} – {v('parallel', k, 'max')}</td></tr>))}
          <tr><td>Successful / failed runs</td><td className="num" colSpan={2}>{s.successful.sequential} ok</td><td className="num" colSpan={2}>{s.successful.parallel} ok · {s.failed} failed overall</td></tr>
          <tr><td>Stages that actually overlapped (parallel)</td><td colSpan={4} className="small">{s.parallel_overlapping_pairs.length ? s.parallel_overlapping_pairs.map((p: string[]) => p.map(stageLabel).join(' ∥ ')).join('; ') : 'none observed: the two independent stages finished too quickly to overlap'}</td></tr>
        </tbody>
      </table></div>
      <details className="small interp" open><summary><strong>How to interpret these results</strong></summary>
        <ul>
          <li>Only one pair of stages (billing and eligibility) is independent, so at most two stages can ever run at once. The other nine must wait for their dependencies in both modes.</li>
          <li>Each stage takes about 0.05–1 ms. Starting a thread and switching between threads costs a similar amount, so parallel execution can be slower as well as faster here; the measured difference is reported either way.</li>
          <li>“Sum of stage durations” can exceed wall-clock time only when stages truly overlapped. The critical path is the fastest the run could be with unlimited parallelism and no overhead.</li>
          <li>Runs were measured on this machine, with one warm-up pair excluded and the order alternated. Results on another machine will differ.</li>
          <li>This measures software execution time only. It says nothing about how long hospital staff take to prepare a claim (see the time study below).</li>
        </ul></details>
      <TimeStudy measured={st?.parallel?.end_to_end_ms?.median ?? null} />
    </Card>
  );
}

export function CorrectionPanel({ rec, stageIds }: { rec: any; stageIds: string[] }) {
  const s = rec.summary;
  const [full, tgt] = s.runs;
  const fs = stagesAt(full.events, stageIds, null), ts = stagesAt(tgt.events, stageIds, null);
  return (
    <Card title="Correction: full re-run vs targeted re-run" meta={`isolated copies · ${humanize(s.correction.op)} · ${rec.config.hadm_id}`}
      actions={<><a className="btn btn-sm" href={api.crExportUrl(rec.id, 'csv')}>Export CSV</a></>}>
      {!s.outputs_match && <div className="error-state" role="alert"><strong>Final outputs differ</strong> in: {s.mismatched_stages.map(stageLabel).join(', ')}.</div>}
      <div className="cmp-hero">
        <div><span>Status before correction</span><strong>{humanize(full.before.status)}</strong></div>
        <div><span>After (full / targeted)</span><strong>{humanize(full.metrics.outcome_status)} / {humanize(tgt.metrics.outcome_status)}</strong></div>
        <div><span>Stage executions avoided</span><strong>{s.executions_avoided} of {full.metrics.stages_executed}</strong><em>{tgt.metrics.stages_executed} executed, {tgt.metrics.stages_reused} reused, {tgt.metrics.stages_not_rerun} not re-run</em></div>
        <div><span>Final outputs</span><strong>{s.outputs_match ? 'identical' : 'DIFFERENT'}</strong><em>same correction, two isolated copies</em></div>
      </div>
      <div className="table-wrap"><table className="tbl">
        <caption className="visually-hidden">Per-stage comparison of full and targeted re-run</caption>
        <thead><tr><th>Stage</th><th>Full re-run</th><th>Targeted re-run</th><th>Why (targeted)</th></tr></thead>
        <tbody>{stageIds.map((id) => (
          <tr key={id}><td>{stageLabel(id)}</td><td className="small">{STATE_LABEL[fs[id].state]} {fs[id].duration !== undefined ? `· ${fmtMs(fs[id].duration)}` : ''}</td>
            <td className="small"><strong>{STATE_LABEL[ts[id].state]}</strong> {ts[id].duration !== undefined ? `· ${fmtMs(ts[id].duration)}` : ''}</td><td className="small muted">{ts[id].reason || '—'}</td></tr>))}</tbody>
      </table></div>
      <p className="small faint" style={{ marginBottom: 0 }}>Fewer stage executions is a software measure. It is not the same percentage of hospital staff time saved.</p>
    </Card>
  );
}

export function TwoRecords({ ids }: { ids: number[] }) {
  const a = useApi<any>(() => api.crRecord(ids[0]), [ids[0]]);
  const b = useApi<any>(() => api.crRecord(ids[1]), [ids[1]]);
  if (!a.data || !b.data) return <div style={{ padding: 14 }}><Skeleton rows={2} /></div>;
  const first = (r: any) => (r.summary.runs.find((x: any) => !x.warmup) || r.summary.runs[0]);
  const ma = first(a.data).metrics, mb = first(b.data).metrics;
  const keys: [string, string][] = [['Wall-clock', 'wall_ms'], ['End-to-end', 'end_to_end_ms'], ['Sum of stage durations', 'stage_sum_ms'], ['Critical path', 'critical_path_ms'], ['Max concurrency', 'max_concurrency'], ['Stages executed', 'stages_executed']];
  return (
    <div className="two-rec">
      <h3>Record #{ids[0]} vs record #{ids[1]} <span className="small faint">(first measured run of each)</span></h3>
      <table className="tbl"><thead><tr><th>Metric</th><th className="num">#{ids[0]} · {first(a.data).label}</th><th className="num">#{ids[1]} · {first(b.data).label}</th><th className="num">Change</th></tr></thead>
        <tbody>{keys.map(([l, k]) => {
          const p = ['max_concurrency', 'stages_executed'].includes(k) ? null : pctChange(ma[k], mb[k]);
          return <tr key={k}><td>{l}</td><td className="num">{k.endsWith('ms') ? fmtMs(ma[k]) : ma[k]}</td><td className="num">{k.endsWith('ms') ? fmtMs(mb[k]) : mb[k]}</td>
            <td className="num small">{p === null ? '—' : `${p >= 0 ? '−' : '+'}${Math.abs(p).toFixed(1)}%`}</td></tr>;
        })}
          <tr><td>Final output fingerprint</td><td colSpan={3} className="small">{first(a.data).fingerprint.hash === first(b.data).fingerprint.hash ? 'identical' : 'different (different encounter, data or correction)'}</td></tr>
        </tbody></table>
    </div>
  );
}
