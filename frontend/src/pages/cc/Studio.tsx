import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { DOMAIN_LABELS, humanize, money, ms, stageLabel } from '../../lib/format';
import { caseState } from '../../lib/caseState';
import { Badge, Button, Card, Disclosure, EmptyState, ErrorState, Field, PageHead, Skeleton } from '../../components/ui';
import { draftFor } from './CorrectionDialog';
import StageGraph from '../../components/StageGraph';

/* eslint-disable @typescript-eslint/no-explicit-any */
type Mode = 'full' | 'recheck' | 'correction';
type StageView = { key: string; label: string; cls: string; detail?: string; t?: number; order?: number; ev?: any };

const MODES: { id: Mode; title: string; text: string }[] = [
  { id: 'full', title: 'Full execution', text: 'Every one of the 11 stages executes (full rerun policy). Best for watching each stage work.' },
  { id: 'recheck', title: 'Re-check (targeted)', text: 'Treats all inputs as changed, but any stage whose inputs hash identically is reused instead of executed.' },
  { id: 'correction', title: 'Correct and re-run', text: 'Applies the suggested correction for the main issue, then re-runs only the affected stages.' },
];

/** Stage state from the events observed so far (live) or up to the teaching cursor. */
function stageView(events: any[], stage: string): StageView {
  let v: StageView = { key: 'pending', label: 'Pending', cls: 'pending' };
  let order = 0;
  for (const e of events) {
    if (e.type === 'stage_started') order += 1;
    if (e.stage !== stage) continue;
    if (e.type === 'stage_started') v = { key: 'running', label: 'Running', cls: 'running', detail: e.reason, t: e.t_ms, order, ev: e };
    else if (e.type === 'stage_reused') v = { key: 'reused', label: 'Reused', cls: 'reuse', detail: e.reason, t: e.t_ms, ev: e };
    else if (e.type === 'stage_not_rerun') v = { key: 'notrun', label: 'Not re-run', cls: 'skip', detail: e.reason, t: e.t_ms, ev: e };
    else if (e.type === 'stage_skipped') v = { key: 'skipped', label: 'Skipped', cls: 'fail', detail: e.reason, t: e.t_ms, ev: e };
    else if (e.type === 'stage_finished') {
      const base = { t: e.t_ms, order: v.order, ev: e, detail: v.detail };
      if (e.status === 'FAILED') v = { ...base, key: 'failed', label: 'Failed', cls: 'fail', detail: e.error };
      else if (e.stage_status === 'BLOCKED') v = { ...base, key: 'blocked', label: 'Blocked', cls: 'blocked' };
      else if (e.stage_status === 'WARN') v = { ...base, key: 'issue', label: 'Issue found', cls: 'issue' };
      else v = { ...base, key: 'done', label: 'Completed', cls: 'exec' };
    }
  }
  return v;
}

function narrate(e: any): string {
  switch (e.type) {
    case 'run_started': return `Run #${e.run_id} started (${humanize(e.trigger).toLowerCase()}, ${e.rerun_policy} policy, ${e.execution_mode} mode). ${e.force ? 'All 11 stages will execute.' : `${e.planned_stages.length} stage(s) read a changed input or depend on one.`}`;
    case 'stage_started': return `${stageLabel(e.stage)} started: ${e.reason}.`;
    case 'stage_finished': return e.status === 'FAILED' ? `${stageLabel(e.stage)} FAILED after ${e.attempts} attempt(s): ${e.error}`
      : `${stageLabel(e.stage)} finished in ${ms(e.duration_ms)}: ${e.findings.length ? `${e.findings.length} finding(s) (${e.findings.map((f: any) => f.title).join('; ')})` : 'no findings'}${e.output_changed ? '' : '; output identical to before'}.`;
    case 'stage_reused': return `${stageLabel(e.stage)} reused: ${e.reason} (#${e.reused_from_execution}).`;
    case 'stage_not_rerun': return `${stageLabel(e.stage)} not re-run: ${e.reason}.`;
    case 'stage_skipped': return `${stageLabel(e.stage)} skipped: ${e.reason}.`;
    case 'run_finished': return `Run finished: ${e.status === 'SUCCEEDED' ? 'succeeded' : 'FAILED'}; ${e.stages_executed} executed, ${e.stages_reused} reused, outcome ${humanize(e.outcome_status)}, ${ms(e.wall_ms)} in total.${e.error ? ` ${e.error}` : ''}`;
    default: return e.type;
  }
}

export default function Studio() {
  const [sp, setSp] = useSearchParams();
  const reg = useApi<any>(() => api.studioStages());
  const [hadm, setHadm] = useState(sp.get('encounter') || '');
  const [inputErr, setInputErr] = useState<string | null>(null);
  const [enc, setEnc] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [lookupBusy, setLookupBusy] = useState(false);
  const [mode, setMode] = useState<Mode>('full');
  const [reason, setReason] = useState('');
  const [run, setRun] = useState<any>(null);            // polling state from /studio/runs/{token}
  const [events, setEvents] = useState<any[]>([]);
  const [startErr, setStartErr] = useState<string | null>(null);
  const [record, setRecord] = useState<any>(null);       // persisted /studio/pipeline-runs/{id}
  const [after, setAfter] = useState<any>(null);         // case detail after the run
  const [open, setOpen] = useState<string | null>(null);
  const [teach, setTeach] = useState(false);
  const [cursor, setCursor] = useState(0);
  // graph by default; the text list suits narrow screens and screen-reader users
  const [view, setView] = useState<'graph' | 'list'>(() => (typeof window !== 'undefined' && window.innerWidth < 820 ? 'list' : 'graph'));
  const poll = useRef<number | null>(null);

  const lookup = useCallback(async (value: string) => {
    const v = value.trim();
    setInputErr(null); setEnc(null); setDetail(null); setRun(null); setEvents([]); setRecord(null); setAfter(null); setStartErr(null); setOpen(null);
    if (!/^\d{1,12}$/.test(v)) { setInputErr('Enter a numeric Encounter ID, for example 98000056.'); return; }
    setLookupBusy(true);
    try {
      const e = await api.studioEncounter(v);
      setEnc(e);
      setSp({ encounter: v }, { replace: true });
      const d = await api.caseDetail(e.case.id);
      setDetail(d);
      const root = d.root_blocker?.roots?.[0];
      setReason(root ? `Execution studio: ${root.title.toLowerCase()} corrected from the evidence` : '');
    } catch (err) { setInputErr((err as Error).message); } finally { setLookupBusy(false); }
  }, [setSp]);
  useEffect(() => { if (sp.get('encounter')) lookup(sp.get('encounter')!); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  useEffect(() => () => { if (poll.current) window.clearTimeout(poll.current); }, []);

  const root = detail?.root_blocker?.roots?.[0];
  const draft = root && detail ? draftFor(root, detail) : null;
  const canCorrect = !!(root && root.correctable && draft && draft.fields.length === 0);

  const start = async () => {
    setStartErr(null); setRecord(null); setAfter(null); setEvents([]); setTeach(false); setCursor(0); setOpen(null);
    let body: any = { hadm_id: String(enc.case.hadm_id), mode };
    if (mode === 'correction') {
      if (!canCorrect) { setStartErr('This case has no correction that can be applied without extra input. Use the case page.'); return; }
      if (reason.trim().length < 3) { setStartErr('Write a reason of at least 3 characters; it is stored in the audit trail.'); return; }
      const task = detail.tasks.find((t: any) => t.status === 'OPEN' && t.code === root.code);
      body = { ...body, correction: { op: draft!.op, payload: draft!.payload, reason: reason.trim(), review_task_id: task?.id, expected_version: detail.case.version } };
    }
    try {
      const r = await api.studioStart(body);
      setRun({ status: r.status, token: r.token });
      let seen: any[] = [];
      const tick = async (n: number) => {
        try {
          const s = await api.studioRun(r.token, n);
          seen = [...seen, ...s.events];
          setEvents(seen);
          setRun({ ...s, token: r.token });
          if (s.status === 'queued' || s.status === 'running') { poll.current = window.setTimeout(() => tick(s.next_after), 120); return; }
          if (s.run_id) setRecord(await api.studioPipelineRun(s.run_id));
          setAfter(await api.caseDetail(enc.case.id));

        } catch (err) { setStartErr((err as Error).message); }
      };
      tick(0);
    } catch (err) {
      const e = err as ApiError;
      setStartErr(e.status === 401 || e.status === 503 ? e.message : e.message);
    }
  };

  const shownEvents = teach ? events.slice(0, cursor) : events;
  const views = useMemo(() => Object.fromEntries((reg.data?.stages || []).map((s: any) => [s.id, stageView(shownEvents, s.id)])), [reg.data, shownEvents]);
  const finished = run && !['queued', 'running'].includes(run.status);
  const runEnd = events.find((e) => e.type === 'run_finished');
  const failed = run?.status === 'error' || run?.run_status === 'FAILED';

  return (
    <>
      <PageHead title="Execution studio" sub="Enter one Encounter ID, run the real workflow, and inspect every stage in execution order." />
      <div className="callout small" style={{ marginBottom: 16 }}>
        Runs use the same orchestrator and correction logic as the rest of ClearClaim, on the <strong>synthetic demo data</strong> loaded in this
        database, and they change it (new runs, and corrections if you choose them). Statuses are internal claim-preparation states, not insurer decisions.
        {reg.data && <> <strong>{reg.data.llm.note}</strong></>}
      </div>

      <div className="grid g-main-rail">
        <Card title="1 · Choose an encounter">
          <form className="row" style={{ gap: 8, alignItems: 'flex-end' }} noValidate onSubmit={(e) => { e.preventDefault(); lookup(hadm); }}>
            <Field label="Encounter ID" htmlFor="studio-hadm" help="Digits only, for example 98000056." error={inputErr}>
              <input id="studio-hadm" className="input" inputMode="numeric" value={hadm} maxLength={12} style={{ width: 180 }}
                aria-invalid={!!inputErr} onChange={(e) => { setHadm(e.target.value); setInputErr(null); }} />
            </Field>
            <Button type="submit" busy={lookupBusy}>Look up</Button>
          </form>
          {enc && (
            <div className="stack" style={{ gap: 6, marginTop: 12 }}>
              <div className="row"><Link to={`/claims/${enc.case.id}`}>{enc.case.case_ref}</Link><Badge value={enc.case.status} />
                <span className="badge b-outline">{enc.case.data_source}</span></div>
              <div className="small">{enc.case.root_blocker_title ? <>Main issue: <strong>{enc.case.root_blocker_title}</strong></> : 'No root blocker at present.'}
                {enc.last_run ? <span className="faint"> · last run #{enc.last_run.id} ({humanize(enc.last_run.trigger).toLowerCase()})</span> : <span className="faint"> · never processed</span>}</div>
            </div>
          )}
        </Card>
        <Card title="Stage types">
          {reg.error ? <ErrorState message={reg.error} onRetry={reg.reload} /> : !reg.data ? <Skeleton rows={3} /> : (
            <div className="small stack" style={{ gap: 6 }}>
              <div><span className="badge b-complete">Deterministic rules</span> all {reg.data.stages.length} workflow stages ({reg.data.stages.filter((s: any) => s.uses_llm).length} use an LLM).</div>
              <div className="muted">{reg.data.llm.policy_explainer_note}</div>
              {reg.data.llm.policy_explainer_provider === 'ExtractiveProvider'
                ? <div><span className="badge b-outline">Policy explainer: extractive</span> no LLM is called anywhere.</div>
                : <div className="callout review small" role="note"><strong>An LLM provider is configured</strong> (<span className="mono">{reg.data.llm.policy_explainer_provider}</span>) because an API key is set in the server environment.
                    It is used only by the separate policy explainer on the case page, never by these 11 stages, and it sends the question and retrieved policy passages to that provider.</div>}
            </div>)}
        </Card>
      </div>

      {enc && (
        <Card title="2 · Run the workflow" className="studio-run">
          <fieldset className="mode-pick">
            <legend className="visually-hidden">Run mode</legend>
            {MODES.map((m) => {
              const disabled = m.id === 'correction' && !canCorrect;
              return (
                <label key={m.id} className={`mode-opt ${mode === m.id ? 'on' : ''} ${disabled ? 'off' : ''}`}>
                  <input type="radio" name="mode" value={m.id} checked={mode === m.id} disabled={disabled} onChange={() => setMode(m.id)} />
                  <span><strong>{m.title}</strong><span className="small muted"> {m.text}{disabled ? ' Not available: no root issue with an evidence-determined fix.' : ''}</span></span>
                </label>);
            })}
          </fieldset>
          {mode === 'correction' && canCorrect && (
            <div className="stack" style={{ gap: 8, marginTop: 10 }}>
              <div className="evidence small"><strong>Correction:</strong> <span className="mono">{draft!.op}</span> for “{root.title}”. {root.message}</div>
              <Field label="Reason (recorded in the audit trail)" htmlFor="studio-reason">
                <textarea id="studio-reason" className="textarea" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
              </Field>
            </div>)}
          <div className="row" style={{ marginTop: 12 }}>
            <Button variant="primary" busy={!!run && !finished} onClick={start}>Run workflow</Button>
            {run && <span className="small" aria-live="polite">{!finished ? `Status: ${run.status}…` : run.status === 'finished' ? `Run #${run.run_id} finished` : `Run ${run.status}`}</span>}
          </div>
          {startErr && <div className="error-state" role="alert" style={{ marginTop: 10 }}>{startErr}</div>}
          {run?.status === 'rejected' && <div className="error-state" role="alert" style={{ marginTop: 10 }}><strong>Correction rejected (HTTP {run.http_status}); nothing was changed.</strong> {run.error}</div>}
          {run?.status === 'error' && <div className="error-state" role="alert" style={{ marginTop: 10 }}><strong>The run could not complete.</strong> {run.error}</div>}
        </Card>
      )}

      {enc && reg.data && (run || events.length > 0) && (
        <Card title="3 · Execution timeline" meta={runEnd ? `run #${runEnd.run_id} · ${ms(runEnd.wall_ms)} measured by the orchestrator` : 'live: events appear as the orchestrator reports them'}
          actions={finished && events.length > 0 ? (
            <label className="small row" style={{ gap: 6 }}><input type="checkbox" checked={teach} onChange={(e) => { setTeach(e.target.checked); setCursor(e.target.checked ? 1 : events.length); }} /> Teaching mode</label>) : undefined}>
          {failed && <div className="error-state" role="alert" style={{ marginBottom: 12 }}><strong>This run FAILED.</strong> {runEnd?.error || run?.error} Its results are not treated as a successful preparation.</div>}
          {teach && (
            <div className="teach" aria-live="polite">
              <div className="row">
                <Button size="sm" disabled={cursor <= 1} onClick={() => setCursor((c) => Math.max(1, c - 1))}>← Previous</Button>
                <span className="small">Event {cursor} of {events.length}</span>
                <Button size="sm" variant="primary" disabled={cursor >= events.length} onClick={() => setCursor((c) => Math.min(events.length, c + 1))}>Next step →</Button>
                <Button size="sm" variant="ghost" onClick={() => setCursor(events.length)}>Show all</Button>
              </div>
              <p className="plain" style={{ margin: '8px 0 0' }}>{events[cursor - 1] ? narrate(events[cursor - 1]) : ''}</p>
              <p className="small faint" style={{ margin: '4px 0 0' }}>Teaching mode steps through the events recorded from this run. The run itself was not paused: pausing would hold the database open for writing.</p>
            </div>)}
          <div className="row" style={{ marginBottom: 10 }}>
            <div className="view-switch" role="group" aria-label="Timeline view">
              <button type="button" aria-pressed={view === 'graph'} onClick={() => setView('graph')}>Graph</button>
              <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>List</button>
            </div>
            <span className="small faint">Node colours change only when the orchestrator reports an event for that stage.</span>
          </div>
          {view === 'graph' ? (
            <StageGraph stages={reg.data.stages} waves={reg.data.waves} views={views} selected={open}
              onSelect={(id) => setOpen(open === id ? null : id)} />
          ) : (
          <ol className="studio-waves" aria-label="Workflow stages in execution order">
            {reg.data.waves.map((wave: string[], wi: number) => (
              <li key={wi} className="studio-wave">
                <span className="wave-no" aria-hidden="true">{wi + 1}</span>
                <div className="wave-stages">
                  {wave.map((s) => {
                    const v = views[s];
                    return (
                      <button key={s} type="button" className={`stage-card st-${v.cls} ${open === s ? 'sel' : ''}`} aria-expanded={open === s}
                        aria-controls="stage-detail" data-stage={s} data-state={v.key} onClick={() => setOpen(open === s ? null : s)}>
                        <span className="sc-top"><strong>{stageLabel(s)}</strong><span className={`sc-badge st-${v.cls}`}>{v.label}</span></span>
                        <span className="small sc-meta">
                          {v.ev?.type === 'stage_finished' && v.ev.status !== 'FAILED' ? `${ms(v.ev.duration_ms)} · ${v.ev.findings.length} finding(s)` : v.t != null ? `at +${ms(v.t)}` : 'no event yet'}
                          {v.order ? ` · #${v.order} to start` : ''}
                        </span>
                        {v.detail && <span className="small sc-why">{v.detail}</span>}
                      </button>);
                  })}
                  {wave.length > 1 && <span className="small faint wave-note">These stages have no dependency on each other; in parallel mode they run concurrently.</span>}
                </div>
              </li>))}
          </ol>
          )}
          {open && <StageDetail id={open} reg={reg.data.stages.find((x: any) => x.id === open)} rec={record?.stages.find((x: any) => x.stage === open)} ev={views[open]} />}
          <Disclosure title="Event log" summary={`${events.length} event(s) reported by the orchestrator`}>
            <div className="table-wrap"><table className="tbl">
              <thead><tr><th className="num">#</th><th className="num">t (ms)</th><th>Event</th><th>Stage</th><th>Detail</th></tr></thead>
              <tbody>{events.map((e) => <tr key={e.seq}><td className="num">{e.seq + 1}</td><td className="num">{e.t_ms.toFixed(3)}</td><td className="mono small">{e.type}</td><td className="small">{e.stage ? stageLabel(e.stage) : '—'}</td><td className="small">{narrate(e)}</td></tr>)}</tbody>
            </table></div>
          </Disclosure>
        </Card>
      )}

      {finished && events.length > 0 && reg.data && <WhyTable events={events} stages={reg.data.stages} />}

      {finished && after && (
        <Card title="5 · Result for this encounter">
          <FinalSummary d={after} failed={failed} />
        </Card>
      )}

      {!enc && !inputErr && (
        <Card><EmptyState title="Enter an Encounter ID to begin">Find IDs on the <Link to="/claims">Cases</Link> page (the number in CC-…), for example 98000056.</EmptyState></Card>
      )}
    </>
  );
}

function StageDetail({ id, reg, rec, ev }: { id: string; reg: any; rec: any; ev: StageView }) {
  const x = rec?.execution;
  return (
    <section id="stage-detail" className="stage-detail" aria-label={`Details for ${stageLabel(id)}`}>
      <h3>{stageLabel(id)} <span className="badge b-complete">Deterministic rules</span> <span className="small faint mono">{reg.implementation}</span></h3>
      <div className="grid g-2" style={{ marginTop: 10 }}>
        <dl className="kv">
          <dt>Owner</dt><dd>{reg.owner}</dd>
          <dt>Depends on</dt><dd>{reg.deps.length ? reg.deps.map(stageLabel).join(', ') : 'nothing (first stage)'}</dd>
          <dt>Reads inputs</dt><dd>{reg.input_domains.length ? reg.input_domains.map((d: string) => DOMAIN_LABELS[d] || d).join(', ') : 'no case inputs; upstream results only'}</dd>
          <dt>Upstream values</dt><dd className="small">{Object.keys(reg.upstream_inputs).length ? Object.entries(reg.upstream_inputs).map(([k, v]) => `${stageLabel(k.split('#')[0])}: ${Array.isArray(v) ? v.join(', ') : v}`).join(' · ') : 'none'}</dd>
          <dt>This run</dt><dd>{ev.label}{ev.detail ? <div className="small muted">{ev.detail}</div> : null}</dd>
          <dt>Duration</dt><dd>{x ? (x.status === 'SUCCEEDED' || x.status === 'FAILED' ? `${ms(x.duration_ms)} · ${x.attempts} attempt(s)` : 'not executed in this run') : 'available after the run is saved'}</dd>
          <dt>Input hash</dt><dd className="mono small">{x?.input_hash ? x.input_hash.slice(0, 16) + '…' : '—'}</dd>
          {x?.error && <><dt>Error</dt><dd style={{ color: 'var(--blocked)' }}>{x.error}</dd></>}
        </dl>
        <div className="stack" style={{ gap: 8 }}>
          <div className="small"><strong>Checks implemented</strong> <span className="faint">(finding codes read from this stage's source)</span></div>
          {reg.checks.length ? <div className="row" style={{ gap: 4 }}>{reg.checks.map((c: any) => <span key={c.code} className="badge b-outline" title={c.code}>{c.title}</span>)}</div>
            : <div className="small muted">{reg.note || 'This stage raises no findings of its own.'}</div>}
          {reg.checks.length > 0 && reg.note && <div className="small muted">{reg.note}</div>}
        </div>
      </div>
      <div className="stack" style={{ gap: 8, marginTop: 12 }}>
        <div className="small"><strong>Findings and evidence</strong> {rec?.output_source && <span className="faint">({rec.output_source})</span>}</div>
        {!rec ? <div className="small muted">Available after the run is saved.</div> : rec.findings.length === 0 ? <div className="small muted">No findings.</div> : (
          <div className="table-wrap"><table className="tbl">
            <thead><tr><th>Finding</th><th>Severity</th><th>Expected</th><th>Recorded</th><th>Evidence</th></tr></thead>
            <tbody>{rec.findings.map((f: any, i: number) => (
              <tr key={i}><td className="small"><strong>{f.title}</strong><div className="faint">{f.message}</div></td><td><Badge value={f.severity} /></td>
                <td className="small">{f.expected ?? '—'}</td><td className="small">{f.actual ?? '—'}</td>
                <td className="small mono">{f.rule_id ? `${f.rule_id} ` : ''}{f.amount != null ? `${money(f.amount)} ` : ''}{f.evidence ? JSON.stringify(f.evidence) : ''}</td></tr>))}</tbody>
          </table></div>)}
        <Disclosure title="Stage output" summary={rec?.output ? 'the data this stage produced (patient identifiers hidden)' : 'not available yet'}>
          {rec?.output ? <pre className="note-text" style={{ maxHeight: 360 }}>{JSON.stringify(rec.output, null, 2)}</pre> : <div className="small muted">Output is read from the saved execution once the run finishes.</div>}
        </Disclosure>
      </div>
    </section>
  );
}

function WhyTable({ events, stages }: { events: any[]; stages: any[] }) {
  const start = events.find((e) => e.type === 'run_started');
  const rows = stages.map((s: any) => {
    const v = stageView(events, s.id);
    const started = events.find((e) => e.type === 'stage_started' && e.stage === s.id);
    const reused = events.find((e) => e.type === 'stage_reused' && e.stage === s.id);
    const why = reused ? `${reused.considered_because ? `Re-checked (${reused.considered_because}), but its ` : ''}${reused.considered_because ? reused.reason : reused.reason.charAt(0).toUpperCase() + reused.reason.slice(1)}.`
      : started ? `${started.reason}${started.previous_input_hash && started.previous_input_hash !== started.input_hash ? '; its input hash differed from the previous execution, so it executed' : ''}.`
        : v.detail ? `${v.detail}.` : '—';
    return { s, v, why };
  });
  const n = (k: string) => rows.filter((r) => r.v.key === k).length;
  const executed = rows.filter((r) => ['done', 'issue', 'blocked', 'failed'].includes(r.v.key)).length;
  return (
    <Card title="4 · Executed vs reused, and why" meta={start ? `${humanize(start.trigger)} · ${start.rerun_policy} policy${start.changed_domains?.length ? ` · changed: ${start.changed_domains.map((d: string) => DOMAIN_LABELS[d] || d).join(', ')}` : ''}` : undefined}>
      <p className="small" style={{ marginTop: 0 }}><strong>{executed}</strong> executed · <strong>{n('reused')}</strong> reused · <strong>{n('notrun')}</strong> not re-run · <strong>{n('skipped')}</strong> skipped. Reasons are the orchestrator's own decisions for this run.</p>
      <div className="table-wrap"><table className="tbl why">
        <thead><tr><th>Stage</th><th>Outcome</th><th>Why</th></tr></thead>
        <tbody>{rows.map(({ s, v, why }) => <tr key={s.id}><td>{stageLabel(s.id)}</td><td><span className={`sc-badge st-${v.cls}`}>{v.label}</span></td><td className="small">{why}</td></tr>)}</tbody>
      </table></div>
    </Card>
  );
}

function FinalSummary({ d, failed }: { d: any; failed: boolean }) {
  const st = caseState(d);
  const open = st.openTasks;
  return (
    <div className="stack" style={{ gap: 10 }}>
      {failed && <div className="error-state" role="alert">The last run failed, so the case is not treated as prepared.</div>}
      <div className={`callout ${st.tone}`} role="status"><strong>{st.headline}.</strong> {st.sentence}</div>
      <div className="grid g-2">
        <div><div className="small muted">Root blockers ({st.blockers.length})</div>
          {st.blockers.length ? <ul className="small" style={{ margin: '4px 0 0', paddingLeft: 18 }}>{st.blockers.map((b: any, i: number) => <li key={i}>{b.title} <span className="faint">({stageLabel(b.stage)})</span></li>)}</ul> : <div className="small">none</div>}</div>
        <div><div className="small muted">Open review tasks ({open.length})</div>
          {open.length ? <ul className="small" style={{ margin: '4px 0 0', paddingLeft: 18 }}>{open.map((t: any) => <li key={t.id}>{t.title} <span className="faint">({t.allowed_decisions.map(humanize).join(' / ')})</span></li>)}</ul> : <div className="small">none</div>}</div>
      </div>
      <div className="row"><Link className="btn" to={`/claims/${d.case.id}`}>Open the case page</Link><Link className="btn" to={`/audit?case=${d.case.id}`}>Audit trail for this case</Link></div>
      <div className="small faint">Internal claim-preparation status only. It is not an insurer decision.</div>
    </div>
  );
}
