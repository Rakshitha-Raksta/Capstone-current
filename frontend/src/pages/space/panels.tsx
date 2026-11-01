import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { DOMAIN_LABELS, dateTime, humanize, money, n, stageLabel } from '../../lib/format';
import { fmtMs, STATE_LABEL } from '../../lib/controlRoom';
import { findingDiff, outputDiff } from '../one/model';
import { DEPARTMENTS, departmentOf, type ViewId } from '../../world/stations';
import { KV, Sev, show } from './parts';

/* eslint-disable @typescript-eslint/no-explicit-any */
type C = any;
const readable = (t: string) => (t || '').replace(
  /\b(ingestion|data_validation|discharge_readiness|billing|eligibility|adjudication|historical|reconciliation|root_blocker|review_routing|claim_generation)\b/g,
  (m) => stageLabel(m).toLowerCase());
const dom = (d: string) => DOMAIN_LABELS[d] || d;

function Next({ c, to, children }: { c: C; to: ViewId; children: React.ReactNode }) {
  return <button type="button" className="next" onClick={() => c.go(to)}>{children} <span aria-hidden="true">→</span></button>;
}
function NoCase({ c }: { c: C }) {
  if (c.detail) return null;
  return <p className="muted">Load an encounter in the top bar. {c.attention?.items?.length ? 'The highest-priority blocked encounter loads automatically.' : 'No blocked or review encounters are loaded; run scripts/setup_demo.py.'}</p>;
}

/* ============================================================ 1. overview */
export function OverviewPanel({ c }: { c: C }) {
  const o = c.overview;
  const r = c.root;
  return (
    <>
      <p className="lede">ClearClaim coordinates the work between a patient's discharge and the insurance claim. It runs 11 deterministic checks
        as a dependency graph, finds the <em>root</em> blocker behind each problem, cites the evidence, and after a correction re-runs only the
        stages that the change can affect.</p>
      <p className="fine">It prepares work for people. It does not decide clinical discharge, does not adjudicate claims, and does not contact any hospital or insurer system.</p>
      <NoCase c={c} />
      {c.detail && (
        <section className="card hero-case" aria-label="This encounter">
          <div className="hc-top"><span className="mono">{c.detail.case.case_ref}</span><span className={`chip tone-${c.state.tone}`}>{c.state.headline}</span></div>
          <p>{c.state.sentence}</p>
          <KV rows={[['Diagnosis group', c.detail.encounter.drg_description || '—'], ['Amount billed', money(c.detail.case.amount_billed)],
            ['Amount at risk (blocking findings)', money(c.detail.case.amount_at_risk)], ['Open review tasks', c.detail.case.open_tasks]]} />
        </section>
      )}
      {r && (
        <section className="card blocker" aria-label="Highest-priority blocker">
          <div className="eyebrow">Highest-priority blocker · {stageLabel(r.stage)}</div>
          <h2>{r.title}</h2>
          <KV rows={[['Expected', show(r.expected)], ['Recorded', show(r.actual)], ['Owner', r.owner_team], ['Evidence', r.rule_id ? `${r.rule_id} · ${r.source_document} p.${r.source_page}` : 'case data (see Investigate)']]} />
          <p className="next-action"><strong>Next action:</strong> {r.next_action}</p>
          <Next c={c} to="investigate">Investigate this blocker</Next>
        </section>
      )}
      {c.detail && !r && c.state?.complete && <p className="ok-line">No blockers remain. The claim-preparation packet can be produced (internal status).</p>}
      {o && (
        <section aria-label="Across all encounters">
          <h3 className="h3">Across all {o.total_cases} encounters</h3>
          <div className="status-bar" role="img" aria-label={Object.entries(o.status_counts).map(([k, v]) => `${humanize(k)} ${v}`).join(', ')}>
            {Object.entries(o.status_counts).map(([k, v]: any) => <span key={k} className={`sb sb-${k}`} style={{ flexGrow: v }} title={`${humanize(k)}: ${v}`} />)}
          </div>
          <ul className="legend">{Object.entries(o.status_counts).map(([k, v]: any) => <li key={k}><span className={`dot sb-${k}`} />{humanize(k)} {v}</li>)}</ul>
          <h3 className="h3">Needs attention</h3>
          <ul className="queue">
            {(c.attention?.items || []).slice(0, 6).map((x: any) => (
              <li key={x.id}><button type="button" className={String(x.hadm_id) === String(c.enc?.case?.hadm_id) ? 'on' : ''} onClick={() => c.load(String(x.hadm_id))}>
                <span className="mono">{x.hadm_id}</span><span className={`chip sm tone-${x.status === 'BLOCKED' ? 'blocked' : 'review'}`}>{humanize(x.status)}</span>
                <span className="q-why">{x.root_blocker_title || humanize(x.root_blocker_code)}</span></button></li>))}
          </ul>
        </section>
      )}
      <p className="fine">{c.meta?.disclaimer}</p>
    </>
  );
}

/* ============================================================ stage inspector (shared) */
export function StageInspector({ c, id }: { c: C; id: string }) {
  const st = c.stages.find((s: any) => s.id === id);
  if (!st) return null;
  const sn = c.snaps[id];
  const ex = c.lastRun?.stages?.find((s: any) => s.stage === id);
  const findings = (c.detail?.findings || []).filter((f: any) => f.stage === id);
  const ups = Object.entries(st.upstream_inputs || {}).map(([k, v]: [string, any]) => `${stageLabel(k.split('#')[0])} (${v === '*' ? 'full result' : v === 'findings' ? 'findings' : (v as string[]).join(', ')})`);
  const events = (c.auditEv || []).filter((e: any) => JSON.stringify(e.details || {}).includes(id)).slice(-4);
  const rulesHere = (c.rules?.rules || []).filter((r: any) => r.outcome?.stage === id);
  return (
    <section className="card inspector" aria-label={`Stage ${stageLabel(id)}`}>
      <div className="eyebrow">Stage {c.ids.indexOf(id) + 1} · {st.owner} · deterministic rules, no language model</div>
      <h2>{st.label}</h2>
      <p>{st.purpose}</p>
      <KV rows={[
        ['Status', sn ? STATE_LABEL[sn.state as keyof typeof STATE_LABEL] : '—'],
        ['Measured duration', sn?.duration !== undefined ? fmtMs(sn.duration) : 'not recorded'],
        ['In the latest run', ex?.execution ? `${humanize(ex.execution.status)}${ex.execution.reused_from_id ? ` (output of execution #${ex.execution.reused_from_id})` : ''}` : ex?.output_source || '—'],
        ['Waits for', st.deps.length ? st.deps.map(stageLabel).join(', ') : 'nothing (first stage)'],
        ['Feeds', st.downstream?.length ? st.downstream.map(stageLabel).join(', ') : 'nothing (last stage)'],
        ['Reads case data', (st.input_domains || []).map(dom).join(', ') || 'none'],
        ['Reads upstream', ups.join('; ') || 'none'],
        ['Input hash', ex?.execution?.input_hash ? <span className="mono">{ex.execution.input_hash.slice(0, 16)}…</span> : undefined],
      ]} />
      {findings.length > 0 && (<><h3 className="h3">Findings ({findings.length})</h3>
        <ul className="findings">{findings.map((f: any) => (
          <li key={f.id}><Sev s={f.severity} /> <strong>{f.title}</strong>{f.role ? <span className="role"> · {humanize(f.role)}</span> : null}
            {(f.expected != null || f.actual != null) && <div className="ea">expected <b>{show(f.expected)}</b> · recorded <b>{show(f.actual)}</b></div>}
            {f.rule_id && <div className="cite">{f.rule_id} · {f.source_document} p.{f.source_page}</div>}</li>))}</ul></>)}
      {rulesHere.length > 0 && <p className="small">{rulesHere.length} cited rule result(s) recorded here. <button type="button" className="link" onClick={() => c.go('policy')}>Open in policy intelligence</button></p>}
      {st.checks?.length > 0 && <details><summary>What this stage checks ({st.checks.length})</summary><ul className="checks">{st.checks.map((x: any, i: number) => <li key={i}>{typeof x === 'string' ? x : x.title || x.code}</li>)}</ul></details>}
      {events.length > 0 && <details><summary>Related audit events</summary><ul className="checks">{events.map((e: any) => <li key={e.id}>#{e.id} {humanize(e.event_type)} · {e.summary}</li>)}</ul></details>}
      <button type="button" className="link" onClick={() => c.setStage(null)}>Close stage</button>
    </section>
  );
}

/* ============================================================ 2. investigate */
export function InvestigatePanel({ c }: { c: C }) {
  const { roots, root, detail } = c;
  if (!detail) return <NoCase c={c} />;
  const cons = root ? (detail.root_blocker?.consequences || []).filter((x: any) => x.caused_by === root.code) : [];
  return (
    <>
      {c.stage && <StageInspector c={c} id={c.stage} />}
      <h3 className="h3">Root blockers ({roots.length})</h3>
      {!roots.length && <p className="ok-line">No root blockers on this encounter.</p>}
      <ul className="roots" role="listbox" aria-label="Root blockers">
        {roots.map((r: any, i: number) => (
          <li key={`${r.code}-${i}`} role="option" aria-selected={i === c.rootIdx}>
            <button type="button" className={i === c.rootIdx ? 'on' : ''} onClick={() => { c.setRootIdx(i); c.setStage(null); }}>
              <Sev s={r.severity} /> <span className="r-title">{r.title}</span><span className="r-stage">{stageLabel(r.stage)}</span>
            </button>
          </li>))}
      </ul>
      {root && !c.stage && (
        <section className="card blocker" aria-label="Selected blocker">
          <div className="eyebrow">{humanize(root.code)} · raised by {stageLabel(root.stage)}</div>
          <h2>{root.title}</h2>
          {root.message && <p>{readable(root.message)}</p>}
          <KV rows={[['Expected', show(root.expected)], ['Recorded', show(root.actual)], ['Amount', root.amount ? money(root.amount) : undefined],
            ['Owner', `${root.owner_team} (${stageLabel(root.owner_stage)})`], ['Data to correct', dom(root.owner_domain)],
            ['Correctable by a data change', root.correctable ? 'yes' : 'no: needs a reviewer decision'],
            ['Citation', root.rule_id ? `${root.rule_id} · ${root.source_document}, page ${root.source_page}` : undefined]]} />
          {root.evidence && Object.keys(root.evidence).length > 0 && (
            <details open><summary>Evidence recorded by the stage</summary>
              <KV rows={Object.entries(root.evidence).map(([k, v]) => [humanize(k), show(v)] as [string, React.ReactNode])} /></details>)}
          <h3 className="h3">What it affects</h3>
          <p className="small">{cons.length ? `${cons.length} downstream finding(s) are consequences of this root and are not routed separately: ${cons.map((x: any) => x.title).join('; ')}.` : 'No downstream findings were attributed to it.'}</p>
          <p className="small">A correction to <b>{dom(root.owner_domain)}</b> re-evaluates: {(root.rerun_stages || []).map(stageLabel).join(' → ')}.</p>
          <p className="next-action"><strong>Next action:</strong> {root.next_action}</p>
          <div className="row-actions">
            <Next c={c} to="correct">{root.correctable ? 'Preview the correction' : 'Record a review decision'}</Next>
            {root.rule_id && <button type="button" className="link" onClick={() => { c.setRule(root.rule_id); c.go('policy'); }}>Open rule {root.rule_id}</button>}
          </div>
        </section>)}
      <p className="fine">Tip: click any block in the 3D deck (or its label) to inspect that stage; upstream and downstream stages stay lit.</p>
    </>
  );
}

/* ============================================================ 3. departments */
export function DepartmentsPanel({ c }: { c: C }) {
  const queues = c.overview?.work_queues || [];
  const tasks = (c.detail?.tasks || []).filter((t: any) => t.status === 'OPEN');
  return (
    <>
      <p className="small">Each stage is owned by the desk named in the backend workflow definition (<span className="mono">dag.py</span>). Departments below group those owners; select one to light its stages.</p>
      {c.stage && <StageInspector c={c} id={c.stage} />}
      <ul className="depts">
        {DEPARTMENTS.map((d) => {
          const own = c.stages.filter((s: any) => departmentOf(s.owner)?.id === d.id);
          const ownIds = own.map((s: any) => s.id);
          const q = queues.filter((x: any) => ownIds.includes(x.owner_stage)).reduce((a: number, x: any) => a + x.open, 0);
          const mine = tasks.filter((t: any) => ownIds.includes(t.owner_stage));
          return (
            <li key={d.id} style={{ ['--dep' as any]: d.css }}>
              <button type="button" className={`dept ${c.department === d.id ? 'on' : ''}`} aria-pressed={c.department === d.id} onClick={() => c.setDepartment(c.department === d.id ? null : d.id)}>
                <span className="dept-title">{d.title}</span>
                <span className="dept-owners">{d.owners.join(' · ')}</span>
                <span className="dept-stages">{own.map((s: any) => (
                  <span key={s.id} className={`mini st-${c.snaps[s.id]?.state || 'waiting'}`}>{stageLabel(s.id)}</span>))}</span>
                <span className="dept-q">{c.detail ? `${mine.length} open task(s) on this encounter · ` : ''}{q} open across all encounters</span>
              </button>
              {c.department === d.id && <p className="fine">{d.note}{mine.length ? ` Open here: ${mine.map((t: any) => t.title).join('; ')}.` : ''}</p>}
            </li>);
        })}
      </ul>
      <p className="fine">No department turnaround times are shown: the repository has no measured operational data for them. Manual-time figures used in the experiments are labelled assumptions (see Audit &amp; research).</p>
    </>
  );
}

/* ============================================================ 4. policy */
const RULE_STATUS: Record<string, string> = {
  PASS: 'Pass', WARNING: 'Warning', PARTIAL_DEDUCTION: 'Partial deduction', FAIL: 'Fail', NO_RESULT: 'No result recorded',
  NOT_APPLICABLE: 'Other policy version', QUARANTINED: 'Quarantined', CATALOG: 'Catalogue',
};
export function PolicyPanel({ c }: { c: C }) {
  const all: any[] = c.rules?.rules || [];
  const [f, setF] = useState<string>('APPLIES');
  const [q, setQ] = useState('');
  const counts = useMemo(() => all.reduce((a: any, r: any) => { const s = r.outcome?.status || 'CATALOG'; a[s] = (a[s] || 0) + 1; return a; }, {}), [all]);
  const rows = all.filter((r) => {
    const s = r.outcome?.status || 'CATALOG';
    if (f === 'APPLIES' && (s === 'NOT_APPLICABLE')) return false;
    if (f !== 'ALL' && f !== 'APPLIES' && s !== f) return false;
    const t = q.trim().toLowerCase();
    return !t || `${r.rule_id} ${r.title} ${r.rule_type} ${r.insurer}`.toLowerCase().includes(t);
  });
  const sel = all.find((r) => r.rule_id === c.rule);
  if (!c.rules) return <p className="muted" role="status">Loading the rule catalogue…</p>;
  return (
    <>
      <p className="small">The {c.rules.total} rules were extracted from {c.rules.documents.length} insurer and regulator documents. Each tile on the 3D wall is one rule, coloured by the result the eligibility and adjudication stages recorded for {c.rules.policy_version ? <>policy <span className="mono">{c.rules.policy_version}</span></> : 'this encounter'}.</p>
      <ul className="legend">{Object.entries(counts).map(([k, v]: any) => <li key={k}><span className={`dot rs-${k}`} />{RULE_STATUS[k] || k} {v}</li>)}</ul>
      {sel && <RuleCard r={sel} c={c} />}
      <div className="filters">
        <label className="sr" htmlFor="rule-q">Search rules</label>
        <input id="rule-q" placeholder="Search id, title, type…" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="sr" htmlFor="rule-f">Filter by outcome</label>
        <select id="rule-f" value={f} onChange={(e) => setF(e.target.value)}>
          <option value="APPLIES">This encounter's policy</option><option value="ALL">All 59 rules</option>
          {Object.keys(RULE_STATUS).filter((k) => counts[k]).map((k) => <option key={k} value={k}>{RULE_STATUS[k]}</option>)}
        </select>
      </div>
      <ul className="rules">
        {rows.map((r) => (
          <li key={r.rule_id}><button type="button" className={c.rule === r.rule_id ? 'on' : ''} onClick={() => c.setRule(r.rule_id)}>
            <span className={`dot rs-${r.outcome?.status || 'CATALOG'}`} /><span className="mono">{r.rule_id}</span><span className="r-title">{r.title}</span>
            <span className="r-stage">{RULE_STATUS[r.outcome?.status || 'CATALOG']}</span></button></li>))}
        {!rows.length && <li className="muted">No rules match.</li>}
      </ul>
      <p className="fine">{c.rules.notes.join(' ')}</p>
    </>
  );
}
function RuleCard({ r, c }: { r: any; c: C }) {
  const o = r.outcome;
  return (
    <section className="card rule-card" aria-label={`Rule ${r.rule_id}`}>
      <div className="eyebrow">{r.rule_id} · {humanize(r.rule_type)} · {r.insurer}</div>
      <h2>{r.title}</h2>
      <KV rows={[['Product / UIN', `${r.product} · ${r.uin}`], ['Condition', r.condition], ['Value', [r.value, r.unit].filter(Boolean).join(' ')],
        ['Applies to', r.applies_to], ['Exception', r.exception], ['Review status', r.review_status]]} />
      <blockquote className="source">
        <p>“{r.source_text}”</p>
        <footer>{r.source_document}, page {r.source_page}{r.source_section ? `, ${r.source_section}` : ''}
          {r.document?.source_url && <> · <a href={r.document.source_url} target="_blank" rel="noreferrer noopener">official source</a></>}
          {r.document?.sha256 && <> · SHA-256 <span className="mono">{String(r.document.sha256).slice(0, 12)}…</span></>}</footer>
      </blockquote>
      <p className="fine">Extraction: {humanize(r.extraction_method)}, confidence {r.confidence}. <Link to={`/policy-source-viewer?document=${encodeURIComponent(r.source_document)}&page=${r.source_page}`}>Open the extracted document text</Link></p>
      {o && (
        <div className={`outcome rs-${o.status}`}>
          <strong>For this encounter: {RULE_STATUS[o.status] || o.status}</strong>
          {o.results ? o.results.map((x: any, i: number) => (
            <div key={i} className="small">{stageLabel(o.stage)}: {x.reason} <span className="ea">expected <b>{show(x.expected)}</b> · recorded <b>{show(x.actual)}</b></span></div>))
            : <div className="small">{o.explanation}</div>}
          {o.results?.some((x: any) => x.actual === 'NOT_PROVIDED') && <div className="small">Missing information: the encounter did not provide a value this rule needs.</div>}
        </div>)}
      <button type="button" className="link" onClick={() => c.setRule(null)}>Close rule</button>
    </section>
  );
}

/* ============================================================ 5. history */
export function HistoryPanel({ c }: { c: C }) {
  const h = c.hist;
  if (!c.detail) return <NoCase c={c} />;
  if (!h) return <p className="muted" role="status">Loading historical peers…</p>;
  const thr = h.method.z_threshold;
  const p = c.peer !== null ? h.peers[c.peer] : null;
  return (
    <>
      <section className="card">
        <div className="eyebrow">Method</div>
        <h2>{h.method.name}</h2>
        <p className="small">{h.method.matching} Scored attributes: {h.method.attributes.join(', ')}. <span className="mono">{h.method.formula}</span>; outlier if |z| &gt; {thr}; at least {h.method.min_peers} peers.</p>
      </section>
      <KV rows={[['This encounter', `${h.current.hadm_id} · DRG ${h.current.drg_code || '—'} (${h.current.drg_type || '—'})`], ['Diagnosis group', h.current.drg_description],
        ['Bill total', money(h.current.total_billed)], ['Length of stay', h.current.los_days != null ? `${h.current.los_days} day(s)` : '—'],
        ['Claim type / room', `${h.current.claim_type || '—'} / ${h.current.room_category || '—'}`], ['Clinical data source', h.current.data_source]]} />
      {h.status !== 'COMPARED' ? <p className="callout">{h.reason}</p> : (
        <>
          <div className={`zline ${h.outlier ? 'out' : ''}`}>
            <div><span>z (bill total)</span><b>{n(h.z_total)}</b></div><div><span>z (length of stay)</span><b>{h.z_los == null ? '—' : n(h.z_los)}</b></div>
            <div><span>Peers</span><b>{h.n_peers}</b></div><div><span>Result</span><b>{h.outlier ? 'Outlier (advisory)' : 'Within range'}</b></div>
          </div>
          <p className="small">Peer medians: bill {money(h.median_total)}, stay {h.median_los} day(s). In the 3D constellation x is bill total, height is length of stay, depth is room category; the teal band is the |z| ≤ {thr} range on bill total.</p>
          {h.matches_recorded ? <p className="fine">Recomputed z equals the value the historical stage recorded in its latest run.</p>
            : <p className="callout">The recomputed z differs from the stage's recorded output (the reference data changed since that run). The recorded output is authoritative for the run.</p>}
          {p && (
            <section className="card" aria-label="Selected peer">
              <div className="eyebrow">Peer {p.hist_claim_id} · {p.provenance}</div>
              <KV rows={[['Bill total', `${money(p.total_billed)} (${p.diff_total >= 0 ? '+' : ''}${money(p.diff_total)} vs this encounter)`],
                ['Length of stay', `${p.los_days} day(s) (${p.diff_los >= 0 ? '+' : ''}${p.diff_los})`], ['Claim type', `${p.claim_type}${p.same_claim_type ? ' (same)' : ' (differs)'}`],
                ['Room', `${p.room_category}${p.same_room ? ' (same)' : ' (differs)'}`]]} />
            </section>)}
          <h3 className="h3">Closest peers</h3>
          <table className="tbl"><thead><tr><th>Peer</th><th>Bill</th><th>Stay</th><th>Room</th></tr></thead>
            <tbody>{h.peers.slice(0, 10).map((x: any, i: number) => (
              <tr key={x.hist_claim_id} className={c.peer === i ? 'on' : ''} onClick={() => c.setPeer(i)}>
                <td><button type="button" className="link mono" onClick={() => c.setPeer(i)}>{x.hist_claim_id}</button></td><td>{money(x.total_billed)}</td><td>{x.los_days}</td><td>{x.room_category}</td></tr>))}</tbody></table>
          <p className="fine">Provenance of peers: {Object.entries(h.provenance || {}).map(([k, v]) => `${k} ${v}`).join(', ')}.</p>
        </>)}
      <h3 className="h3">How it feeds review</h3>
      <p className="small">{h.review_contribution || 'No comparison was made, so it contributes nothing to review.'}</p>
      <details open><summary>Limitations</summary><ul className="checks">{h.method.limitations.map((x: string) => <li key={x}>{x}</li>)}</ul></details>
    </>
  );
}

/* ============================================================ 6. lab */
export function LabPanel({ c }: { c: C }) {
  const rec = c.labRec;
  const job = c.labJob.job;
  const busy = job && ['starting', 'queued', 'running'].includes(job.status);
  const [reps, setReps] = useState(5);
  const s = rec?.summary;
  const st = s?.stats;
  return (
    <>
      <section className="card times" aria-label="Four kinds of time">
        <h2>Four kinds of time, kept apart</h2>
        <ol>
          <li><b>Software execution time</b>: measured backend wall-clock milliseconds (shown here).</li>
          <li><b>Critical-path duration</b>: the longest dependency chain of measured stage durations.</li>
          <li><b>Re-execution efficiency</b>: stage executions avoided after a correction (a count, not a time).</li>
          <li><b>Hospital workflow time</b>: not measured. The experiments use labelled manual-minute <em>assumptions</em> only.</li>
        </ol>
      </section>
      {!c.detail ? <NoCase c={c} /> : (
        <div className="row-actions">
          <label className="small">Repetitions <select value={reps} onChange={(e) => setReps(Number(e.target.value))}>{[3, 5, 10].map((v) => <option key={v}>{v}</option>)}</select></label>
          <button type="button" className="tbtn primary" disabled={busy} onClick={() => c.runCompare(reps)}>{busy ? 'Running on isolated copies…' : 'Measure sequential vs parallel'}</button>
          {c.draft && <button type="button" className="tbtn" disabled={busy} onClick={() => c.runCorrectionCompare({ op: c.draft.op, payload: c.draft.payload, reason: 'Execution lab: full vs targeted comparison on isolated copies', expected_version: c.detail.case.version })}>Full vs targeted after correction</button>}
          {busy && <button type="button" className="link" onClick={c.labJob.cancel}>Stop</button>}
        </div>)}
      {busy && <p className="small" role="status">{c.labJob.events.length} recorded event(s) so far{job.current ? ` · ${job.current}` : ''}. Live data is not changed: every run uses an isolated copy of the database.</p>}
      {c.labErr && <p className="p-error" role="alert">{c.labErr}</p>}
      {rec && rec.kind === 'cr_comparison' && st && (
        <section className="card" aria-label="Measured comparison">
          <div className="eyebrow">Record #{rec.id} · {rec.summary.repetitions} repetitions · {dateTime(rec.started_at)}</div>
          <table className="tbl"><thead><tr><th>Median of measured runs</th><th>Sequential</th><th>Parallel</th></tr></thead>
            <tbody>
              {[['Wall-clock', 'wall_ms'], ['End to end (incl. commit)', 'end_to_end_ms'], ['Sum of stage durations', 'stage_sum_ms'], ['Critical path', 'critical_path_ms'], ['Max concurrency', 'max_concurrency']].map(([l, k]) => (
                <tr key={k}><td>{l}</td><td>{k === 'max_concurrency' ? st.sequential[k]?.median : fmtMs(st.sequential[k]?.median)}</td><td>{k === 'max_concurrency' ? st.parallel[k]?.median : fmtMs(st.parallel[k]?.median)}</td></tr>))}
              <tr><td>Spread (min–max wall)</td><td>{fmtMs(st.sequential.wall_ms?.min)}–{fmtMs(st.sequential.wall_ms?.max)}</td><td>{fmtMs(st.parallel.wall_ms?.min)}–{fmtMs(st.parallel.wall_ms?.max)}</td></tr>
            </tbody></table>
          <p className="verdict">{s.wall_reduction_pct == null ? 'No reduction computed.' : s.wall_reduction_pct > 0
            ? `Parallel was ${n(s.wall_reduction_pct)}% faster by median wall-clock time on this encounter.`
            : `Parallel was ${n(-s.wall_reduction_pct)}% slower by median wall-clock time on this encounter.`}
            {' '}{s.parallel_overlapping_pairs?.length ? `Stages that actually overlapped: ${s.parallel_overlapping_pairs.map((p: string[]) => p.map(stageLabel).join(' ∥ ')).join('; ')}.` : 'No stages actually overlapped in time.'}</p>
          <KV rows={[['Same input snapshot', s.same_input_snapshot ? 'yes' : 'NO'], ['Outputs identical', s.outputs_match ? `yes (${s.distinct_output_fingerprints} fingerprint)` : 'NO'], ['Failed runs', s.failed], ['Method', s.method]]} />
          <p className="fine">The 3D lanes show the median run of each mode at recorded start/end offsets. Stage work here is sub-millisecond Python, so thread start-up overhead can outweigh the one parallel wave; that is a real measured result, not a defect in the chart.</p>
        </section>)}
      {rec && rec.kind === 'cr_correction' && (
        <section className="card" aria-label="Full vs targeted">
          <div className="eyebrow">Record #{rec.id} · correction {rec.summary.correction.op}</div>
          <table className="tbl"><thead><tr><th></th><th>Full re-run</th><th>Targeted re-run</th></tr></thead>
            <tbody>
              <tr><td>Stages executed</td><td>{rec.summary.runs[0].metrics.stages_executed}</td><td>{rec.summary.runs[1].metrics.stages_executed}</td></tr>
              <tr><td>Stages reused</td><td>{rec.summary.runs[0].metrics.stages_reused ?? 0}</td><td>{rec.summary.runs[1].metrics.stages_reused ?? 0}</td></tr>
              <tr><td>Wall-clock</td><td>{fmtMs(rec.summary.runs[0].metrics.wall_ms)}</td><td>{fmtMs(rec.summary.runs[1].metrics.wall_ms)}</td></tr>
            </tbody></table>
          <p className="verdict">{rec.summary.executions_avoided} stage execution(s) avoided; final outputs {rec.summary.outputs_match ? 'identical' : `DIFFER in ${rec.summary.mismatched_stages.join(', ')}`}.</p>
          <p className="fine">{rec.summary.method}</p>
        </section>)}
      {rec?.lanes?.length > 0 && (
        <div className="row-actions">
          <button type="button" className="tbtn" onClick={c.labReplay.playing ? () => c.labReplay.setPlaying(false) : c.labReplay.play}>{c.labReplay.playing ? 'Pause replay' : 'Replay lanes'}</button>
          <select aria-label="Replay slow-down" value={c.labReplay.slow} onChange={(e) => c.labReplay.setSlow(Number(e.target.value))}>{[500, 1000, 5000].map((v) => <option key={v} value={v}>{v}× slower</option>)}</select>
          {c.labReplay.t !== null && <span className="chip sm tone-review">REPLAY of recorded events · {fmtMs(c.labReplay.t)}</span>}
        </div>)}
      {c.bar && rec?.lanes?.[c.bar.lane] && (() => {
        const lane = rec.lanes[c.bar.lane];
        const b = lane.bars.find((x: any) => x.stage === c.bar.stage);
        return b ? <p className="small"><b>{stageLabel(b.stage)}</b> in {lane.label}: {b.status === 'REUSED' ? 'reused (not executed)' : `${fmtMs(b.start)} → ${fmtMs(b.end)} (${fmtMs(b.end - b.start)})`}</p> : null;
      })()}
      {!rec && c.detail && !busy && <p className="muted">No measured comparison is recorded for this encounter yet. Run one: it takes a few seconds and never changes live data.</p>}
      <p className="fine">Cohort-level results (4 cohorts × 3 seeds) are in Audit &amp; research.</p>
    </>
  );
}

/* ============================================================ 7. correct */
function getPath(o: any, path: string) { return path.split('.').reduce((a, k) => (a == null ? a : a[k]), o); }
function setPath(o: any, path: string, v: any) {
  const x = JSON.parse(JSON.stringify(o)); const ks = path.split('.'); let cur = x;
  ks.slice(0, -1).forEach((k) => { cur = cur[k]; }); cur[ks[ks.length - 1]] = v; return x;
}
export function CorrectPanel({ c }: { c: C }) {
  const { root, draft, preview, run, detail, task } = c;
  const [payload, setPayload] = useState<any>(null);
  const [reason, setReason] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState('');
  useEffect(() => { setPayload(draft?.payload ?? null); setConfirm(false); setErr(null); setReason(''); }, [draft]);
  if (!detail) return <NoCase c={c} />;
  if (run) return <VerifyRun c={c} />;
  if (!root) return <p className="ok-line">This encounter has no root blocker to correct.</p>;
  const body = () => {
    const pl = JSON.parse(JSON.stringify(payload ?? draft.payload));
    if (draft.op === 'set_discharge_time' && pl.value && pl.value.length === 16) pl.value += ':00';
    const missing = draft.fields.filter((f: any) => { const v = getPath(pl, f.key); return v === '' || v == null || Number.isNaN(v); });
    if (missing.length) { setErr(`Enter: ${missing.map((f: any) => f.label).join(', ')}.`); return null; }
    if (reason.trim().length < 3) { setErr('Record a reason (at least 3 characters); it is stored in the audit trail.'); return null; }
    if (!confirm) { setErr('Tick the confirmation box: this changes the encounter data.'); return null; }
    setErr(null);
    return { op: draft.op, payload: pl, reason: reason.trim(), review_task_id: task?.id, expected_version: detail.case.version };
  };
  return (
    <>
      <ol className="steps"><li className="on">Issue</li><li className={preview ? 'on' : ''}>Preview plan</li><li>Confirm</li><li>Verify</li></ol>
      <section className="card blocker">
        <div className="eyebrow">The issue · {stageLabel(root.stage)}</div>
        <h2>{root.title}</h2>
        <KV rows={[['Expected', show(root.expected)], ['Recorded', show(root.actual)], ['Rule / dependency', root.rule_id ? `${root.rule_id} (${root.source_document} p.${root.source_page})` : `owned by ${stageLabel(root.owner_stage)} (${dom(root.owner_domain)})`],
          ['Suggested correction', draft ? `${humanize(draft.op)}` : 'none: a reviewer decision is required'], ['Basis of the suggestion', draft ? 'built from the evidence the stage recorded (expected value, line number, document)' : root.next_action]]} />
      </section>
      {!draft && task && (
        <section className="card">
          <h2>Record a review decision</h2>
          <p className="small">{task.reason}</p>
          <label className="fl" htmlFor="dec-note">Justification (stored in the audit trail)</label>
          <textarea id="dec-note" value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
          <div className="row-actions">{(task.allowed_decisions || []).map((d: string) => (
            <button key={d} type="button" className="tbtn" onClick={() => { if (note.trim().length < 5) { setErr('Write a justification of at least 5 characters.'); return; } c.decide(d, note.trim()); }}>{humanize(d)}</button>))}</div>
          {err && <p className="p-error" role="alert">{err}</p>}
        </section>)}
      {!draft && !task && <p className="callout">No data correction or open review task applies to this blocker.</p>}
      {draft && preview && (
        <section className="card" aria-label="Recomputation plan">
          <div className="eyebrow">Preview · from the orchestrator's own dependency model · nothing has changed</div>
          <h2>Changing {dom(preview.domain)} re-evaluates {preview.planned.length} of {c.ids.length} stages</h2>
          <ul className="plan">{preview.planned.map((s: string) => <li key={s}><span className="mini st-running">{stageLabel(s)}</span> {readable(preview.why[s])}</li>)}</ul>
          <p className="small">Kept as they are: {preview.kept.map(stageLabel).join(', ') || 'none'}.</p>
          <details><summary>How to read the plan</summary><ul className="checks">{preview.notes.map((x: string) => <li key={x}>{x}</li>)}</ul></details>
        </section>)}
      {draft && (
        <section className="card" aria-label="Correction form">
          <h2>Proposed change</h2>
          <p className="small mono">{draft.op}{draft.fields.length === 0 ? ' — no values needed' : ''}</p>
          {draft.fields.map((fl: any) => (
            <div key={fl.key}><label className="fl" htmlFor={`cf-${fl.key}`}>{fl.label}</label>
              <input id={`cf-${fl.key}`} type={fl.type} value={getPath(payload, fl.key) ?? ''}
                onChange={(e) => setPayload(setPath(payload, fl.key, fl.type === 'number' ? Number(e.target.value) : e.target.value))} />
              {fl.help && <p className="fine">{fl.help}</p>}</div>))}
          <label className="fl" htmlFor="cf-reason">Reason (stored in the audit trail)</label>
          <textarea id="cf-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={500} />
          <label className="check"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> I confirm this changes the encounter data and re-runs the planned stages.</label>
          {err && <p className="p-error" role="alert">{err}</p>}
          <div className="row-actions">
            <button type="button" className="tbtn primary" onClick={() => { const b = body(); if (b) c.applyCorrection(b); }}>Confirm and apply</button>
            <button type="button" className="link" onClick={() => { const b = body(); if (b) { c.runCorrectionCompare(b); c.go('lab'); } }}>Dry run on isolated copies (full vs targeted)</button>
          </div>
        </section>)}
    </>
  );
}
function VerifyRun({ c }: { c: C }) {
  const { run, outcome } = c;
  const fd = run.after ? findingDiff(run.before, run.after) : { resolved: [], added: [] };
  const od = run.afterRun ? outputDiff(run.beforeRun, run.afterRun) : [];
  const newEvents = (c.auditEv || []).filter((e: any) => run.runId && JSON.stringify(e.details || {}).includes(`${run.runId}`)).slice(-6);
  return (
    <>
      <ol className="steps"><li className="on">Issue</li><li className="on">Preview plan</li><li className="on">Confirm</li><li className={run.status === 'finished' ? 'on' : ''}>Verify</li></ol>
      {run.status === 'running' && <p role="status">Applying on the backend… {run.events.length} recorded event(s).</p>}
      {(run.status === 'rejected' || run.status === 'error') && (
        <section className="p-error" role="alert"><b>{run.status === 'rejected' ? 'The backend rejected the change; nothing was modified.' : 'The run failed.'}</b> {run.error}
          <div><button type="button" className="link" onClick={() => c.setRun(null)}>Back</button></div></section>)}
      {run.status === 'finished' && (
        <>
          <section className="card" aria-label="Verified outcome">
            <div className="eyebrow">Verified from the recorded run #{run.runId}</div>
            <h2>{humanize(run.before.case.status)} → {humanize(run.after.case.status)}</h2>
            <KV rows={[['Executed', outcome.executed.map(stageLabel).join(', ') || 'none'], ['Reused (input hash unchanged)', outcome.reused.map(stageLabel).join(', ') || 'none'],
              ['Not re-run (outside the plan)', outcome.notRerun.map(stageLabel).join(', ') || 'none'], ['Output changed', outcome.changed.map(stageLabel).join(', ') || 'none'],
              ['Amount at risk', `${money(run.before.case.amount_at_risk)} → ${money(run.after.case.amount_at_risk)}`]]} />
          </section>
          <section className="card"><h2>Findings</h2>
            <p className="small">Resolved: {fd.resolved.length ? fd.resolved.map((f: any) => f.title).join('; ') : 'none'}.</p>
            <p className="small">New: {fd.added.length ? fd.added.map((f: any) => f.title).join('; ') : 'none'}.</p></section>
          {od.length > 0 && <section className="card"><h2>Before → after values</h2>
            <table className="tbl"><thead><tr><th>Stage · field</th><th>Before</th><th>After</th></tr></thead>
              <tbody>{od.flatMap((r: any) => r.fields.slice(0, 6).map((f: any) => <tr key={`${r.stage}.${f.key}`}><td>{stageLabel(r.stage)} · {f.key}</td><td>{show(f.before)}</td><td>{show(f.after)}</td></tr>))}</tbody></table></section>}
          <p className="small">Audit: {newEvents.length ? `${newEvents.length} event(s) recorded for this run.` : 'see the audit chain.'} Chain check: {c.chain?.ok ? `intact (${c.chain.checked} events)` : c.chain ? `BROKEN at event #${c.chain.first_broken_event_id}` : 'not available'}.</p>
          <div className="row-actions"><button type="button" className="next" onClick={() => c.go('audit')}>Inspect the audit trail →</button>
            <button type="button" className="link" onClick={() => c.setRun(null)}>Next blocker</button></div>
        </>)}
    </>
  );
}

/* ============================================================ 8. audit & research */
export function AuditPanel({ c }: { c: C }) {
  const [res, setRes] = useState<any>(null);
  useEffect(() => { api.intelResearch().then(setRes).catch(() => setRes(null)); }, []);
  const ev: any[] = c.auditEv || [];
  const sel = ev.find((e) => e.id === c.auditSel);
  const M = (arm: any, k: string, pct = false) => (arm?.[k] ? (pct ? `${(arm[k].mean * 100).toFixed(1)}%` : n(arm[k].mean, 1)) : '—');
  return (
    <>
      <section className={`card chain ${c.chain?.ok ? 'ok' : c.chain ? 'bad' : ''}`} aria-label="Audit chain">
        <h2>{c.chain ? (c.chain.ok ? 'Hash chain intact' : `Hash chain broken at event #${c.chain.first_broken_event_id}`) : 'Chain check unavailable'}</h2>
        {c.chain && <p className="small">{c.chain.checked} events recomputed with SHA-256{c.chain.unchained_legacy_events ? `; ${c.chain.unchained_legacy_events} legacy events predate the chain` : ''}. Head <span className="mono">{String(c.chain.head || '').slice(0, 16)}…</span></p>}
      </section>
      {c.detail && <>
        <h3 className="h3">This encounter's events ({ev.length}{ev.length > 28 ? ', last 28 drawn in 3D' : ''})</h3>
        {sel && <section className="card"><div className="eyebrow">#{sel.id} · {dateTime(sel.ts)} · {sel.actor}</div><h2>{humanize(sel.event_type)}</h2><p className="small">{sel.summary}</p>
          {sel.details && <details><summary>Details</summary><pre className="pre">{JSON.stringify(sel.details, null, 2).slice(0, 1600)}</pre></details>}</section>}
        <ul className="events">{ev.slice().reverse().slice(0, 14).map((e) => (
          <li key={e.id}><button type="button" className={c.auditSel === e.id ? 'on' : ''} onClick={() => c.setAuditSel(e.id)}>
            <span className="mono">#{e.id}</span><span className="r-title">{humanize(e.event_type)}</span><span className="r-stage">{dateTime(e.ts)}</span></button></li>))}</ul>
      </>}
      <h3 className="h3">Research results (recorded, verified runs)</h3>
      {!res ? <p className="muted">Loading recorded results…</p> : (
        <div className="tbl-wrap" tabIndex={0} role="region" aria-label="Research results table"><table className="tbl small-tbl">
          <thead><tr><th>Cohort</th><th>Loop executions B → C</th><th>Unnecessary B → C</th><th>Tasks: all findings → root</th><th>Coverage C</th><th>Verifier</th></tr></thead>
          <tbody>{res.runs.filter((r: any) => r.available).map((r: any) => {
            const B = r.arms.B_sequential_full, Cc = r.arms.C_parallel_targeted;
            return <tr key={r.run}><td>{r.label}</td><td>{M(B, 'loop_stage_executions')} → {M(Cc, 'loop_stage_executions')}</td>
              <td>{M(B, 'loop_unnecessary_executions')} → {M(Cc, 'loop_unnecessary_executions')}</td><td>{M(r.arms.C_minus_root_routing, 'tasks_created_total')} → {M(Cc, 'tasks_created_total')}</td>
              <td>{M(Cc, 'issue_coverage_by_tasks.recall', true)}</td><td>{r.verification ? `${r.verification.mismatches} mismatches` : 'not verified'}</td></tr>;
          })}</tbody></table></div>)}
      {res && <p className="fine">B = {res.arms_legend.B_sequential_full}. C = {res.arms_legend.C_parallel_targeted}. All-findings = {res.arms_legend.C_minus_root_routing}. Loop = the correction loop after the first pass; unnecessary = re-executions whose output did not change. {res.notes.join(' ')} Full analysis: docs/research/EXPERIMENT_RESULTS.md.</p>}
    </>
  );
}
