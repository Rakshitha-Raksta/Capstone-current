import { stageLabel } from '../../lib/format';
import { fmtMs, STATE_SHORT, type StageSnap } from '../../lib/controlRoom';
import { MARK_TEXT, type SceneMark } from '../../lib/marks';

/* eslint-disable @typescript-eslint/no-explicit-any */

/** Accessible list rendering of the same workflow: the WebGL fallback and the "List view". */
export function ListDeck({ stages, waves, snaps, marks, selected, onSelect, reason }: {
  stages: any[]; waves: string[][]; snaps: Record<string, StageSnap>; marks: Record<string, SceneMark>;
  selected: string | null; onSelect: (id: string) => void; reason: string;
}) {
  const label = Object.fromEntries(stages.map((s) => [s.id, s]));
  let n = 0;
  return (
    <div className="list-deck">
      <p className="ld-note" role="note">{reason}</p>
      <ol className="ld-waves">
        {waves.map((w, i) => (
          <li key={i} className="ld-wave">
            <span className="ld-wave-n">Wave {i + 1}{w.length > 1 ? ' · independent stages (may run concurrently)' : ''}</span>
            <ul>
              {w.map((id) => {
                n += 1;
                const sn = snaps[id];
                const mk = marks[id];
                return (
                  <li key={id}>
                    <button type="button" className={`ld-stage st-${sn?.state || 'waiting'} ${selected === id ? 'sel' : ''}`} aria-pressed={selected === id} onClick={() => onSelect(id)}>
                      <span className="ws-n">{n}</span>
                      <span className="ld-name">{stageLabel(id)}</span>
                      <span className="ld-state">{STATE_SHORT[sn?.state || 'waiting']}{sn?.duration !== undefined ? ` · ${fmtMs(sn.duration)}` : ''}</span>
                      {mk && <span className={`ws-mark mk-${mk}`}>{MARK_TEXT[mk]}</span>}
                      <span className="ld-deps">{label[id]?.deps?.length ? `after ${label[id].deps.map(stageLabel).join(', ')}` : 'first stage'}</span>
                    </button>
                  </li>);
              })}
            </ul>
          </li>
        ))}
      </ol>
    </div>
  );
}

/**
 * Shared execution timeline: one segment per stage, proportional to its recorded duration in the
 * latest run (or in the correction run while it is replayed). Software execution time only.
 */
export function Timeline({ ids, snaps, detail, run, replay, total, onSelect, selected }: {
  ids: string[]; snaps: Record<string, StageSnap>; detail: any; run: any; replay: any; total: number;
  onSelect: (id: string) => void; selected: string | null;
}) {
  const live = run && run.events.length;
  const segs = ids.map((id) => {
    const sn = snaps[id];
    const d = live ? (sn?.end !== undefined && sn?.start !== undefined ? sn.end - sn.start : 0) : detail?.stages?.[id]?.duration_ms ?? 0;
    return { id, d: Math.max(0, d || 0), state: sn?.state || 'waiting' };
  });
  const sum = segs.reduce((a, s) => a + s.d, 0) || 1;
  const source = live
    ? run.status === 'running' ? 'Correction run · executing on the backend now'
      : replay.t !== null ? `REPLAY of the recorded correction run · ${replay.slow}× slower than real` : 'Correction run · final recorded state'
    : detail ? 'Latest recorded run of this encounter' : 'No encounter loaded';
  return (
    <section className="space-timeline" aria-label="Execution timeline">
      <div className="tl-head">
        <strong>Execution timeline</strong>
        <span className="tl-src">{source}</span>
        <span className="tl-unit">Measured software time (sum {fmtMs(segs.reduce((a, s) => a + s.d, 0))}); not hospital turnaround</span>
        {live && run.status !== 'running' && (
          <span className="tl-replay">
            <button type="button" className="tbtn" onClick={replay.playing ? () => replay.setPlaying(false) : replay.play}>{replay.playing ? 'Pause' : 'Replay'}</button>
            <select aria-label="Replay slow-down" value={replay.slow} onChange={(e) => replay.setSlow(Number(e.target.value))}>
              {[100, 500, 1000, 5000].map((v) => <option key={v} value={v}>{v}× slower</option>)}
            </select>
            <input type="range" min={0} max={total || 1} step={(total || 1) / 300} value={replay.t ?? total} aria-label="Replay position"
              onChange={(e) => { replay.setPlaying(false); replay.setT(Number(e.target.value)); }} />
          </span>
        )}
      </div>
      <ol className="tl-track">
        {segs.map((s, i) => (
          <li key={s.id} style={{ flexGrow: Math.max(0.035, s.d / sum) }}>
            <button type="button" className={`tl-seg st-${s.state} ${selected === s.id ? 'sel' : ''}`} onClick={() => onSelect(s.id)}
              aria-label={`${i + 1}. ${stageLabel(s.id)}: ${STATE_SHORT[s.state as keyof typeof STATE_SHORT]}${s.d ? `, ${fmtMs(s.d)}` : ''}`}
              title={`${stageLabel(s.id)} · ${s.d ? fmtMs(s.d) : 'no duration recorded'}`}>
              <span className="tl-n">{i + 1}</span><span className="tl-name">{stageLabel(s.id)}</span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return <dl className="kv">{rows.filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

export const Sev = ({ s }: { s: string }) => <span className={`sev-chip sev-${String(s).toLowerCase()}`}>{String(s).replace(/_/g, ' ')}</span>;

export function show(v: any): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}
