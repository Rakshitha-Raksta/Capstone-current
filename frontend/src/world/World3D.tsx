import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import * as THREE from 'three';
import { stageLabel } from '../lib/format';
import { fmtMs, STATE_SHORT, type StageSnap } from '../lib/controlRoom';
import { MARK_TEXT, type SceneMark } from '../lib/marks';
import { World, type AuditBlock, type LabLane, type PeerDot, type Pick, type RuleDot, type StageDef } from './engine';
import { DEPARTMENTS, DISTRICT, STATION, type ViewId } from './stations';

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface WorldProps {
  view: ViewId;
  stages: StageDef[]; waves: string[][];
  snaps: Record<string, StageSnap>; marks: Record<string, SceneMark>;
  selectedStage: string | null; playing: boolean; department: string | null;
  rules: RuleDot[]; selectedRule: string | null;
  peers: PeerDot[]; current: { total: number | null; los: number | null } | null;
  band: { median: number; lo: number; hi: number; medianLos: number | null } | null; selectedPeer: number | null;
  lanes: LabLane[]; laneOrder: string[]; selectedBar: { lane: number; stage: string } | null; labCursor: number | null;
  audit: AuditBlock[]; auditOk: boolean | null; selectedAudit: number | null;
  /** camera focus on the deck for investigate / correct (a stage id) */
  focusStage: string | null;
  insets: { right: number; left: number; top: number; bottom: number };
  onPick: (p: Pick | null) => void;
  onReady?: () => void;
}
export interface WorldHandle { zoom: (f: number) => void; reset: () => void; focus: (id: string) => void }

const DISTRICT_LABELS: { key: string; view: ViewId; title: string; sub: string }[] = [
  { key: 'policy', view: 'policy', title: 'Policy archive', sub: '59 cited rules' },
  { key: 'history', view: 'history', title: 'Historical peers', sub: 'Outlier screen' },
  { key: 'lab', view: 'lab', title: 'Execution lab', sub: 'Measured runs' },
  { key: 'audit', view: 'audit', title: 'Audit chain', sub: 'Hash-linked events' },
  { key: 'deck', view: 'investigate', title: 'Workflow deck', sub: '11 deterministic stages' },
];

const World3D = forwardRef<WorldHandle, WorldProps>(function World3D(p, ref) {
  const host = useRef<HTMLDivElement>(null);
  const world = useRef<World | null>(null);
  const pickRef = useRef(p.onPick);
  pickRef.current = p.onPick;
  const [ready, setReady] = useState(false);
  const reduced = typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  useEffect(() => {
    if (!host.current) return undefined;
    const w = new World(host.current, { reduced, onPick: (x) => pickRef.current(x) });
    world.current = w;
    (window as any).__ccWorld = w;          // read-only hook for automated browser checks
    host.current.dataset.ready = '1';
    setReady(true);
    p.onReady?.();
    return () => { w.dispose(); world.current = null; delete (window as any).__ccWorld; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useImperativeHandle(ref, () => ({
    zoom: (f) => world.current?.zoom(f),
    reset: () => world.current?.resetView(),
    focus: (id) => world.current?.focusStage(id),
  }), []);

  const w = world.current;
  useEffect(() => { if (w && p.stages.length) w.setWorkflow(p.stages, p.waves); }, [w, ready, p.stages, p.waves]);
  useEffect(() => { w?.setDeckState(p.snaps, p.marks, p.selectedStage, p.playing, p.department); },
    [w, ready, p.snaps, p.marks, p.selectedStage, p.playing, p.department, p.view, p.stages]);
  useEffect(() => { if (w && p.rules.length) w.setRules(p.rules, p.selectedRule); }, [w, ready, p.rules, p.selectedRule]);
  useEffect(() => { w?.setHistory(p.peers, p.current, p.band, p.selectedPeer); }, [w, ready, p.peers, p.current, p.band, p.selectedPeer]);
  useEffect(() => { w?.setLab(p.lanes, p.laneOrder, p.selectedBar); }, [w, ready, p.lanes, p.laneOrder, p.selectedBar]);
  useEffect(() => { w?.setLabCursor(p.labCursor); }, [w, ready, p.labCursor, p.lanes]);
  useEffect(() => { w?.setAudit(p.audit, p.auditOk, p.selectedAudit); }, [w, ready, p.audit, p.auditOk, p.selectedAudit]);
  useEffect(() => { w?.setInsets(p.insets); }, [w, ready, p.insets]);
  useEffect(() => {
    if (!w) return;
    const f = p.focusStage && (p.view === 'investigate' || p.view === 'correct') ? w.pos[p.focusStage] : null;
    w.setView(p.view, f);
  }, [w, ready, p.view, p.focusStage]);

  // label anchors (re-registered when their targets change)
  const reg = (key: string, at: () => any, minView?: (v: ViewId) => boolean) => (el: HTMLElement | null) => world.current?.registerLabel(key, el, at, minView);
  const order = p.waves.flat();
  const sel = p.selectedStage ? p.stages.find((s) => s.id === p.selectedStage) : null;
  const up = new Set(sel?.deps || []);
  const down = new Set(p.stages.filter((s) => p.selectedStage && s.deps.includes(p.selectedStage)).map((s) => s.id));
  const districtAnchor = (k: string) => () => {
    const [x, , z] = DISTRICT[k as keyof typeof DISTRICT];
    const dz: Record<string, number> = { deck: 12.5, policy: 12, history: 13, lab: 12, audit: 6 };
    return new THREE.Vector3(x, 0.4, z + dz[k]);
  };
  return (
    <div className="world" ref={host} data-view={p.view} role="application"
      aria-label={`ClearClaim 3D workspace, ${STATION[p.view].title}. Drag to orbit, scroll to zoom. Stage labels are buttons.`}>
      <div className="world-labels">
        {DISTRICT_LABELS.map((d) => (
          <div key={d.key} className={`w-district ${p.view === d.view ? 'here' : ''}`} aria-hidden="true"
            ref={reg(`d:${d.key}`, districtAnchor(d.key), (v) => d.key !== 'deck' || v === 'overview')}>
            <span className="wd-title">{d.title}</span><span className="wd-sub">{d.sub}</span>
          </div>
        ))}
        {DEPARTMENTS.map((d) => (
          <div key={d.id} className={`w-dept ${p.department === d.id ? 'on' : ''}`} aria-hidden="true" style={{ ['--dep' as any]: d.css }}
            ref={reg(`dep:${d.id}`, () => world.current?.departmentAnchor(d.id) || null, (v) => v === 'departments')}>
            {d.title}
          </div>
        ))}
        {order.map((id, k) => {
          const sn = p.snaps[id] || ({ state: 'waiting', label: 'Waiting' } as StageSnap);
          const fin = sn.duration !== undefined && ['completed', 'issue', 'blocked', 'failed'].includes(sn.state);
          const nf = (sn.findings || []).length;
          const mk = p.marks[id];
          const rel = !p.selectedStage ? '' : id === p.selectedStage ? 'sel' : up.has(id) ? 'up' : down.has(id) ? 'down' : 'other';
          return (
            <div key={id} className={`w-stage st-${sn.state} ${rel} ${mk ? `mk-${mk}` : ''}`} data-stage={id} data-state={sn.state} data-mark={mk || undefined}
              ref={reg(`s:${id}`, () => world.current?.stageAnchor(id) || null, (v) => STATION[v].deckLabels || (v === 'overview' && mk === 'root'))}>
              <button type="button" aria-pressed={p.selectedStage === id}
                aria-label={`Stage ${k + 1}, ${stageLabel(id)}: ${STATE_SHORT[sn.state]}${mk ? `, ${MARK_TEXT[mk]}` : ''}${fin ? `, ${fmtMs(sn.duration)}` : ''}${nf ? `, ${nf} finding${nf > 1 ? 's' : ''}` : ''}${up.has(id) ? ', upstream of selection' : down.has(id) ? ', downstream of selection' : ''}`}
                onClick={() => p.onPick({ kind: 'stage', id })}>
                <span className="ws-n">{k + 1}</span>
                <span className="ws-body"><span className="ws-name">{stageLabel(id)}</span>
                  <span className="ws-meta"><span className="ws-state">{STATE_SHORT[sn.state]}</span>
                    {fin ? <span className="ws-ms">{fmtMs(sn.duration)}</span> : null}
                    {nf ? <span className="ws-f">{nf}</span> : null}</span>
                  {mk && mk !== 'downstream' && mk !== 'kept' ? <span className={`ws-mark mk-${mk}`}>{MARK_TEXT[mk]}</span> : null}
                  {rel === 'up' ? <span className="ws-rel">upstream</span> : rel === 'down' ? <span className="ws-rel">downstream</span> : null}
                </span>
              </button>
            </div>);
        })}
        <div className="w-pin rule" aria-hidden="true" ref={reg('pin:rule', () => world.current?.ruleAnchor(p.selectedRule) || null, (v) => v === 'policy')}>{p.selectedRule}</div>
        <div className="w-pin current" aria-hidden="true" ref={reg('pin:current', () => world.current?.currentPos() || null, (v) => v === 'history')}>This encounter</div>
        <div className="w-pin axis" aria-hidden="true" ref={reg('ax:x', () => world.current?.historyAxis('x') || null, (v) => v === 'history')}>Bill total →</div>
        <div className="w-pin axis" aria-hidden="true" ref={reg('ax:y', () => world.current?.historyAxis('y') || null, (v) => v === 'history')}>↑ Length of stay</div>
        <div className="w-pin peer" aria-hidden="true" ref={reg('pin:peer', () => world.current?.peerAt(p.selectedPeer) || null, (v) => v === 'history')}>Peer</div>
        {p.lanes.map((l, i) => (
          <div key={i} className="w-pin lane" aria-hidden="true" ref={reg(`lane:${i}:${l.label}`, () => world.current?.laneAnchor(i) || null, (v) => v === 'lab')}>{l.label}</div>
        ))}
        <div className="w-pin audit" aria-hidden="true" ref={reg('pin:audit', () => world.current?.auditAnchor(p.selectedAudit) || null, (v) => v === 'audit')}>Event #{p.selectedAudit}</div>
      </div>
    </div>
  );
});

export default World3D;
