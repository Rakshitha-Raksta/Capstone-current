import { useEffect, useState } from 'react';
import { api, tokenStore } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { humanize } from '../../lib/format';
import { Badge, Button, Card, ErrorState, Field, PageHead, Skeleton, useToast } from '../../components/ui';

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function Settings() {
  const toast = useToast();
  const s = useApi<any>(() => api.settings());
  const health = useApi<any>(() => api.health());
  const [vals, setVals] = useState<any>(null);
  const [token, setToken] = useState(tokenStore.get());
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (s.data) setVals({ ...s.data.values }); }, [s.data]);

  if (s.error) return <><PageHead title="Settings" /><ErrorState message={s.error} onRetry={s.reload} /></>;
  if (!s.data || !vals) return <><PageHead title="Settings" /><Skeleton rows={6} /></>;
  const env = s.data.environment;
  const save = async () => {
    setBusy(true);
    try {
      const payload = { ...vals,
        readiness_required_documents: String(vals.readiness_required_documents).split(',').map((x: string) => x.trim()).filter(Boolean),
        readiness_required_checklist: String(vals.readiness_required_checklist).split(',').map((x: string) => x.trim()).filter(Boolean) };
      await api.saveSettings(payload);
      toast('Settings saved. New runs use these values; existing results are unchanged until a case is re-run.');
      s.reload();
    } catch (e) { toast((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  const sel = (k: string, opts: string[]) => (
    <select id={`set-${k}`} className="select" value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}>
      {opts.map((o) => <option key={o} value={o}>{humanize(o)}</option>)}
    </select>);
  const num = (k: string, step = 1) => (
    <input id={`set-${k}`} className="input" type="number" step={step} value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: Number(e.target.value) })} />);

  return (
    <>
      <PageHead title="Settings" sub="Execution, routing and threshold configuration. Every change is recorded in the audit trail." />
      <div className="grid g-main-rail">
        <div className="stack">
          <Card title="Execution">
            <div className="grid g-3">
              <Field label="Execution mode" htmlFor="set-execution_mode" help="Parallel runs billing and eligibility concurrently">{sel('execution_mode', ['parallel', 'sequential'])}</Field>
              <Field label="Rebuild policy" htmlFor="set-rerun_policy" help="Targeted reruns only affected stages">{sel('rerun_policy', ['targeted', 'full'])}</Field>
              <Field label="Review routing" htmlFor="set-routing_policy" help="Root: route root blockers; all: every finding">{sel('routing_policy', ['root', 'all_findings'])}</Field>
              <Field label="Max stage retries" htmlFor="set-max_stage_retries">{num('max_stage_retries')}</Field>
            </div>
          </Card>
          <Card title="Thresholds">
            <div className="grid g-3">
              <Field label="Historical |z| threshold" htmlFor="set-historical_z_threshold" help="Modified z-score (advisory)">{num('historical_z_threshold', 0.1)}</Field>
              <Field label="Minimum historical peers" htmlFor="set-historical_min_peers">{num('historical_min_peers')}</Field>
              <Field label="Price tolerance (fraction)" htmlFor="set-billing_price_tolerance_pct" help="0 = must not exceed rate card">{num('billing_price_tolerance_pct', 0.01)}</Field>
              <Field label="Amount rounding tolerance (INR)" htmlFor="set-billing_amount_tolerance_inr">{num('billing_amount_tolerance_inr', 0.5)}</Field>
            </div>
          </Card>
          <Card title="Administrative discharge checklist" meta="not a clinical-fitness assessment">
            <div className="stack" style={{ gap: 10 }}>
              <Field label="Required documents (comma-separated)" htmlFor="set-docs">
                <input id="set-docs" className="input" value={Array.isArray(vals.readiness_required_documents) ? vals.readiness_required_documents.join(', ') : vals.readiness_required_documents}
                  onChange={(e) => setVals({ ...vals, readiness_required_documents: e.target.value })} /></Field>
              <Field label="Required checklist items (comma-separated)" htmlFor="set-check">
                <input id="set-check" className="input" value={Array.isArray(vals.readiness_required_checklist) ? vals.readiness_required_checklist.join(', ') : vals.readiness_required_checklist}
                  onChange={(e) => setVals({ ...vals, readiness_required_checklist: e.target.value })} /></Field>
            </div>
          </Card>
          <div className="row"><Button variant="primary" busy={busy} onClick={save}>Save settings</Button>
            <Button onClick={() => setVals({ ...s.data.defaults })}>Reset form to defaults</Button></div>
        </div>
        <div className="stack">
          <Card title="Application health">
            <dl className="kv">
              <dt>API</dt><dd>{health.data ? <Badge value={health.data.status === 'ok' ? 'SUCCEEDED' : 'FAILED'} /> : '…'}</dd>
              <dt>Database</dt><dd>{health.data?.database ?? '…'} ({env.database_engine})</dd>
              <dt>Cases</dt><dd>{health.data?.cases ?? '…'}</dd>
              <dt>Version</dt><dd>{env.version}</dd>
              <dt>Environment</dt><dd>{env.app_env}</dd>
              <dt>Data mode</dt><dd>{env.data_mode}</dd>
              <dt>Experiments</dt><dd>{env.experiments_enabled ? 'enabled' : 'disabled'}</dd>
              <dt>Allowed origins</dt><dd className="small mono">{env.cors_origins.join(', ')}</dd>
            </dl>
          </Card>
          <Card title="Access">
            <p className="small muted">{env.write_protected ? 'This deployment requires a write token for changes (corrections, decisions, settings).' : 'Write protection is off (local demo).'}</p>
            <Field label="Write token" htmlFor="set-token" help="Stored only in this browser">
              <input id="set-token" className="input" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
            </Field>
            <div className="row" style={{ marginTop: 8 }}><Button size="sm" onClick={() => { tokenStore.set(token); toast('Token saved in this browser.'); }}>Save token</Button>
              <Button size="sm" variant="ghost" onClick={() => { tokenStore.set(''); setToken(''); }}>Clear</Button></div>
          </Card>
        </div>
      </div>
    </>
  );
}
