import { useState } from 'react';
import { stageLabel } from '../lib/format';
import { fmtMs, stagesAt, STATE_SHORT } from '../lib/controlRoom';

/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Gantt chart of ONE recorded run. Bar position and width are the measured start/end of each stage
 * function call (ms since the run started). The lighter lead-in bar is the measured gap between the
 * orchestrator marking the stage ready and the stage function starting (input hashing, scheduling).
 */
export default function GanttTimeline({ events, stageIds, metrics, selected, onSelect, replayT, title, scaleEnd, width = 760 }: {
  events: any[]; stageIds: string[]; metrics: any; selected?: string | null; onSelect?: (id: string) => void;
  replayT?: number | null; title?: string; scaleEnd?: number; width?: number;
}) {
  const [zoom, setZoom] = useState(1);
  const snaps = stagesAt(events, stageIds, null);
  const own = Math.max(metrics?.end_to_end_ms || 0, metrics?.wall_ms || 0, ...Object.values(snaps).map((s) => s.end || s.at || 0)) || 1;
  const end = scaleEnd && scaleEnd >= own ? scaleEnd : own;               // shared scale for side-by-side comparison
  const LW = 168, BW = width * zoom, RH = 26, TOP = 26;
  const x = (t: number) => LW + (t / end) * BW;
  const ticks = Array.from({ length: 6 }, (_, i) => (end * i) / 5);
  const H = TOP + stageIds.length * RH + 30;
  return (
    <div className="gantt">
      <div className="row gantt-head">
        {title && <strong className="small">{title}</strong>}
        <span className="small muted">x-axis: milliseconds since this run started (measured)</span>
        <label className="small row" style={{ marginLeft: 'auto', gap: 6 }}>Zoom
          <input type="range" min={1} max={8} step={1} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="Timeline zoom" />
          ×{zoom}</label>
      </div>
      <div className="gantt-scroll">
        <svg className="gantt-svg" width={LW + BW + 70} height={H} role="img"
          aria-label={`Measured timeline: ${stageIds.map((s) => `${stageLabel(s)} ${snaps[s].start !== undefined ? `${fmtMs(snaps[s].start)} to ${fmtMs(snaps[s].end)}` : STATE_SHORT[snaps[s].state]}`).join('; ')}`}>
          {ticks.map((t, i) => (
            <g key={i}><line className="g-grid" x1={x(t)} x2={x(t)} y1={TOP - 6} y2={H - 26} /><text className="g-tick" x={x(t)} y={14} textAnchor="middle">{fmtMs(t)}</text></g>
          ))}
          {stageIds.map((s, i) => {
            const sn = snaps[s];
            const y = TOP + i * RH;
            const on = selected === s;
            const has = sn.start !== undefined && sn.end !== undefined;
            return (
              <g key={s} className={`g-row ${on ? 'on' : ''}`} data-stage={s} onClick={() => onSelect?.(s)} style={{ cursor: onSelect ? 'pointer' : 'default' }}>
                <rect className="g-band" x={0} y={y} width={LW + BW + 70} height={RH} />
                <text className="g-label" x={8} y={y + 17}>{i + 1}. {stageLabel(s)}</text>
                {has && sn.readyAt !== undefined && sn.start! > sn.readyAt && (
                  <rect className="g-wait" x={x(sn.readyAt)} y={y + 9} width={Math.max(1, x(sn.start!) - x(sn.readyAt))} height={8} rx={2}>
                    <title>{`${stageLabel(s)}: ready at ${fmtMs(sn.readyAt)}, started at ${fmtMs(sn.start)} (input hashing and scheduling)`}</title>
                  </rect>)}
                {has ? (
                  <rect className={`g-bar st-${sn.state}`} x={x(sn.start!)} y={y + 5} width={Math.max(2, x(sn.end!) - x(sn.start!))} height={16} rx={3}>
                    <title>{`${stageLabel(s)}: ${STATE_SHORT[sn.state]} · start ${fmtMs(sn.start)} · end ${fmtMs(sn.end)} · duration ${fmtMs(sn.duration)}`}</title>
                  </rect>
                ) : sn.at !== undefined ? (
                  <g><circle className={`g-mark st-${sn.state}`} cx={x(sn.at)} cy={y + 13} r={4} /><text className="g-note" x={x(sn.at) + 8} y={y + 17}>{STATE_SHORT[sn.state]} (not executed)</text></g>
                ) : <text className="g-note" x={LW + 6} y={y + 17}>no execution recorded</text>}
                {has && <text className="g-dur" x={x(sn.end!) + 6} y={y + 17}>{fmtMs(sn.duration)}</text>}
              </g>
            );
          })}
          {metrics?.wall_ms != null && <g><line className="g-end" x1={x(metrics.wall_ms)} x2={x(metrics.wall_ms)} y1={TOP - 4} y2={H - 22} />
            <text className="g-end-t" x={x(metrics.wall_ms)} y={H - 8} textAnchor="middle">wall {fmtMs(metrics.wall_ms)}</text></g>}
          {replayT != null && <line className="g-now" x1={x(Math.min(replayT, end))} x2={x(Math.min(replayT, end))} y1={TOP - 6} y2={H - 26} />}
        </svg>
      </div>
    </div>
  );
}

export function MetricStrip({ m }: { m: any }) {
  if (!m) return null;
  const items: [string, string, string][] = [
    ['Wall-clock', fmtMs(m.wall_ms), 'run start → last stage finished and staged'],
    ['End-to-end', fmtMs(m.end_to_end_ms), 'including outcome update and database commit'],
    ['Sum of stage durations', fmtMs(m.stage_sum_ms), 'adds up every stage that executed'],
    ['Critical path', fmtMs(m.critical_path_ms), 'longest dependency chain, measured durations'],
    ['Max concurrency', String(m.max_concurrency), 'stages executing at the same instant'],
    ['Overhead', fmtMs(m.overhead_ms), 'wall-clock time outside every stage function'],
    ['Executed / reused / not re-run / skipped / failed', `${m.stages_executed} / ${m.stages_reused} / ${m.stages_not_rerun} / ${m.stages_skipped} / ${m.stages_failed}`, ''],
  ];
  return (
    <dl className="metric-strip">
      {items.map(([k, v, h]) => <div key={k}><dt>{k}</dt><dd>{v}</dd>{h && <span>{h}</span>}</div>)}
    </dl>
  );
}
