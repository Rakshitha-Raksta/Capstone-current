import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { DOMAIN_LABELS, humanize, n, pct, stageLabel } from '../../lib/format';
import { nextStep } from '../../lib/caseState';
import { Badge, Card, EmptyState, ErrorState, Skeleton } from '../../components/ui';
import SpatialScene from '../../components/SpatialScene';
import { stagesAt } from '../../lib/controlRoom';

/* eslint-disable @typescript-eslint/no-explicit-any */
const EXAMPLE_DOMAINS = ['bill', 'documents', 'diagnoses', 'policy'];

export default function Home() {
  const ov = useApi<any>(() => api.overview());
  const graph = useApi<any>(() => api.graph());
  const exps = useApi<any[]>(() => api.experiments());
  const latest = (exps.data || []).find((e) => e.status === 'COMPLETED' && e.has_summary);
  const exp = useApi<any>(() => (latest ? api.experiment(latest.id) : Promise.resolve(null)), [latest?.id]);
  const nav = useNavigate();

  const sc = ov.data?.status_counts || {};
  return (
    <div className="stack home">
      <section className="hero" aria-labelledby="home-title">
        <div className="hero-text">
          <p className="eyebrow">Healthcare claim preparation · capstone prototype</p>
          <h1 id="home-title">Find what is blocking a claim, fix it once, and re-check only what changed.</h1>
          <p className="lead">
            ClearClaim checks each discharged patient's records, hospital bill and insurance policy through an
            11-stage workflow. When something is wrong it shows the <strong>root blocker</strong> (not every symptom),
            the <strong>evidence</strong>, <strong>who owns the fix</strong> and <strong>what to change</strong>.
            After a correction it <strong>re-runs only the affected stages</strong> and reuses the rest, and every
            change is kept in an <strong>audit trail</strong>.
          </p>
          <div className="row" style={{ gap: 10, marginTop: 14 }}>
            <Link className="btn btn-primary btn-lg" to="/demo">Try guided demo →</Link>
            <Link className="btn btn-lg" to="/studio">Open execution studio</Link>
            <Link className="btn btn-lg" to="/claims">Browse cases</Link>
          </div>
          <p className="small muted" style={{ marginTop: 12 }}>
            Statuses are internal claim-preparation states, not insurer decisions. This prototype has not been
            evaluated in a hospital and makes no claim about discharge time.
          </p>
        </div>
        {graph.data && (
          <figure className="hero-3d" style={{ margin: 0 }}>
            <SpatialScene stages={graph.data.stages} waves={graph.data.waves} snaps={stagesAt([], graph.data.stages.map((x: any) => x.id), null)}
              interactive={false} height={330} label="The real 11-stage workflow and its dependencies (illustration; nothing is running)" />
            <figcaption className="small">The real 11-stage workflow · illustration, nothing is running · <Link to="/control-room">open the Agent Control Room →</Link></figcaption>
          </figure>)}
      </section>

      {ov.error ? <ErrorState message={ov.error} onRetry={ov.reload} /> : !ov.data ? <Skeleton rows={3} /> : ov.data.total_cases === 0 ? (
        <Card><EmptyState title="No cases loaded yet">Run <span className="mono">python scripts/setup_demo.py --reset</span> to load the synthetic demo.</EmptyState></Card>
      ) : (
        <section aria-labelledby="counts-title" className="stack" style={{ gap: 8 }}>
          <h2 id="counts-title">Live case counts <span className="small faint">from the current database</span></h2>
          <div className="grid g-4">
            <CountTile label="Cases" value={ov.data.total_cases} to="/claims" hint="encounters loaded" />
            <CountTile label="Blocked" value={sc.BLOCKED || 0} to="/claims?status=BLOCKED" hint="an issue must be fixed" />
            <CountTile label="Needs review" value={sc.NEEDS_REVIEW || 0} to="/claims?status=NEEDS_REVIEW" hint="a person must decide" />
            <CountTile label="Preparation complete" value={sc.PREPARATION_COMPLETE || 0} to="/claims?status=PREPARATION_COMPLETE" hint="no open issues" />
          </div>
        </section>
      )}

      <section aria-labelledby="how-title" className="stack" style={{ gap: 8 }}>
        <h2 id="how-title">What ClearClaim does</h2>
        <div className="grid g-4 how">
          <HowCard n={1} title="Finds the root blocker" text="Separates the issue that must be fixed from the downstream symptoms it causes, using documented causal rules." />
          <HowCard n={2} title="Shows evidence and owner" text="Expected vs recorded values, the policy rule and page, the team that owns the fix and a suggested correction." />
          <HowCard n={3} title="Re-runs only what is affected" text="After a correction, only stages that read the changed data are re-checked; unchanged results are reused." />
          <HowCard n={4} title="Records everything" text="Every correction, decision and re-run is kept in a tamper-evident audit trail with before/after values." />
        </div>
      </section>

      <Card title="Targeted reprocessing, explained" meta="from the live workflow dependency graph">
        {graph.error ? <ErrorState message={graph.error} onRetry={graph.reload} /> : !graph.data ? <Skeleton rows={2} /> : <RerunExplainer g={graph.data} />}
      </Card>

      <div className="grid g-2">
        <Card title="Measured on the benchmark" meta={latest ? `experiment record #${latest.id} · ${latest.name}` : undefined}
          actions={<Link to="/experiments">Experiments →</Link>}>
          {!latest ? (
            <div className="small muted">No experiment records are loaded in this database yet. Open <Link to="/experiments">Experiments</Link> to load the recorded runs.</div>
          ) : exp.error ? <ErrorState message={exp.error} /> : !exp.data ? <Skeleton rows={2} /> : <ExperimentHeadline summary={exp.data.summary} />}
        </Card>
        <Card title="Needs attention now" meta="highest priority first" flush actions={<Link to="/claims?status=BLOCKED,NEEDS_REVIEW">All →</Link>}>
          {!ov.data ? <div style={{ padding: 14 }}><Skeleton rows={3} /></div> : ov.data.attention.length === 0 ? <EmptyState title="Nothing needs attention" /> : (
            <div className="table-wrap"><table className="tbl">
              <thead><tr><th>Case</th><th>Status</th><th>Main issue</th></tr></thead>
              <tbody>{ov.data.attention.slice(0, 5).map((c: any) => (
                <tr key={c.id} className="clickable" onClick={() => nav(`/claims/${c.id}`)}>
                  <td><Link to={`/claims/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.case_ref}</Link></td>
                  <td><Badge value={c.status} /></td>
                  <td className="small">{c.root_blocker_title || humanize(c.root_blocker_code)}<div className="faint">{nextStep(c)}</div></td>
                </tr>))}</tbody>
            </table></div>)}
        </Card>
      </div>
    </div>
  );
}

function CountTile({ label, value, hint, to }: { label: string; value: number; hint: string; to: string }) {
  return (
    <Link to={to} className="card stat stat-link">
      <div className="label">{label}</div>
      <div className="value">{n(value, 0)}</div>
      <div className="hint">{hint}</div>
    </Link>
  );
}

function HowCard({ n: k, title, text }: { n: number; title: string; text: string }) {
  return (
    <div className="card how-card">
      <span className="step-num" aria-hidden="true">{k}</span>
      <h3>{title}</h3>
      <p className="small muted">{text}</p>
    </div>
  );
}

function RerunExplainer({ g }: { g: any }) {
  const domains = EXAMPLE_DOMAINS.filter((d) => g.rerun_plans?.[d]);
  const [dom, setDom] = useState(domains[0] || 'bill');
  const plan = new Set<string>(g.rerun_plans?.[dom] || []);
  const order: string[] = g.stages.map((s: any) => s.id);
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row" role="group" aria-label="Example correction">
        <span className="small muted">If someone corrects the…</span>
        {domains.map((d) => (
          <button key={d} type="button" className="chip" aria-pressed={dom === d} onClick={() => setDom(d)}>{DOMAIN_LABELS[d] || d}</button>
        ))}
      </div>
      <ol className="stage-strip" aria-label={`Stages after correcting the ${DOMAIN_LABELS[dom] || dom}`}>
        {order.map((s) => (
          <li key={s} className={plan.has(s) ? 'rechk' : 'skip'}>
            <span>{stageLabel(s)}</span>
            <span className="small">{plan.has(s) ? 're-checked' : 'not touched'}</span>
          </li>
        ))}
      </ol>
      <p className="small" style={{ margin: 0 }}>
        <strong>{plan.size} of {order.length}</strong> stages read the {(DOMAIN_LABELS[dom] || dom).toLowerCase()} or depend on a stage that does.
        The other {order.length - plan.size} keep their previous results. Among the re-checked stages, any whose inputs
        turn out to be identical (compared by input hash) are <strong>reused</strong> instead of executed again.
        A full rerun would execute all {order.length}.
      </p>
    </div>
  );
}

function ExperimentHeadline({ summary }: { summary: any }) {
  const agg = summary?.aggregate || {};
  const B = agg.B_sequential_full, C = agg.C_parallel_targeted;
  const m = (a: any, k: string) => a?.[k]?.mean as number | undefined;
  const bLoop = m(B, 'loop_stage_executions'), cLoop = m(C, 'loop_stage_executions');
  const bUn = m(B, 'loop_unnecessary_executions'), cUn = m(C, 'loop_unnecessary_executions');
  if (bLoop == null || cLoop == null) return <div className="small muted">This record has no correction-loop metrics.</div>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="small">Stage executions while correcting all injected faults (mean per seed, {summary.config?.seeds?.length} seed(s)):</div>
      <dl className="kv">
        <dt>Full rerun (baseline)</dt><dd><strong>{n(bLoop, 0)}</strong> executions, {n(bUn, 0)} of them unnecessary</dd>
        <dt>Targeted rerun</dt><dd><strong>{n(cLoop, 0)}</strong> executions, {n(cUn, 0)} unnecessary</dd>
        <dt>Difference</dt><dd>{pct(1 - cLoop / bLoop)} fewer executions{bUn ? `, ${pct(1 - (cUn || 0) / bUn)} fewer unnecessary` : ''}</dd>
      </dl>
      <div className="small faint">Controlled benchmark ({summary.config?.clinical_source === 'SYNTHETIC' ? 'synthetic cases' : 'real MIMIC demo encounters with synthetic billing and insurance'}); the faults and the detectors were written by the same team. Not a hospital measurement.</div>
    </div>
  );
}
