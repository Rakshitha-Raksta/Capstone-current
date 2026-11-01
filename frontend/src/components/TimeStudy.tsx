import { useState } from 'react';
import { api } from '../lib/api';
import { useApi } from '../lib/hooks';
import { n } from '../lib/format';
import { fmtMs } from '../lib/controlRoom';
import { Button } from './ui';

/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Keeps three kinds of time strictly apart: measured software time, SIMULATED human effort
 * (from stated assumptions, with sensitivity), and observed hospital time (not collected).
 */
export default function TimeStudy({ measured }: { measured: number | null }) {
  const list = useApi<any[]>(() => api.experiments());
  const rec = (list.data || []).find((e) => e.status === 'COMPLETED' && e.has_summary);
  const exp = useApi<any>(() => (rec ? api.experiment(rec.id) : Promise.resolve(null)), [rec?.id]);
  const [busy, setBusy] = useState(false);
  const cfg = exp.data?.summary?.config;
  const agg = exp.data?.summary?.aggregate;
  const assumed: Record<string, number> | undefined = cfg?.assumed_manual_minutes;
  const manual = agg?.A_manual_model?.minutes_per_case?.mean as number | undefined;
  const auto = agg?.C_parallel_targeted?.modelled_human_minutes_per_case?.mean as number | undefined;
  const load = async () => {
    setBusy(true);
    try { await api.importSummary('results/experiments/main/summary.json'); list.reload(); } finally { setBusy(false); }
  };
  return (
    <section className="time-study" aria-labelledby="ts-title">
      <h3 id="ts-title">Claim-preparation time study: three kinds of time, never mixed</h3>
      <div className="ts-grid">
        <div className="ts measured"><span className="ts-k">1 · Measured software time</span>
          <strong>{measured === null ? 'run a comparison' : fmtMs(measured)}</strong>
          <p>Backend execution of the 11 stages for one encounter (median, parallel, end-to-end). Measured with a monotonic clock.</p></div>
        <div className="ts simulated"><span className="ts-k">2 · Simulated human effort (model)</span>
          {!rec ? (<><strong>no experiment record loaded</strong>
            <p>Load the recorded synthetic experiment to see the modelled minutes. <Button size="sm" busy={busy} onClick={load}>Load recorded experiment</Button></p></>)
            : !assumed || manual === undefined ? <strong>unavailable in this record</strong> : (
              <>
                <strong>{n(manual, 0)} min manual vs {n(auto, 1)} min with ClearClaim</strong>
                <p>Per case, from <em>assumed</em> stage durations and an assumed {cfg.assumed_minutes_per_review_action} minutes per review action. Sensitivity (all manual assumptions scaled):
                  ×0.5 → {n(manual * 0.5, 0)} min, ×1 → {n(manual, 0)} min, ×2 → {n(manual * 2, 0)} min.</p>
                <details><summary>Assumed manual minutes per stage</summary>
                  <ul>{Object.entries(assumed).map(([k, v]) => <li key={k}>{k.replace(/_/g, ' ')}: {v} min</li>)}</ul>
                  <p>Chosen as round-number placeholders for the modelled comparison (docs/research/EVALUATION_PROTOCOL.md); they were not observed. Active work only; waiting time is not modelled.</p>
                </details>
              </>)}
        </div>
        <div className="ts observed"><span className="ts-k">3 · Observed hospital time</span>
          <strong>not collected</strong>
          <p>No time-and-motion study was done. Faster software does not show that patient discharge or claim preparation takes less time in a hospital.</p></div>
      </div>
    </section>
  );
}
