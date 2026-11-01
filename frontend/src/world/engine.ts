import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { StageSnap, StageState } from '../lib/controlRoom';
import type { SceneMark } from '../lib/marks';
import { DEPARTMENTS, departmentOf, DISTRICT, STATION, type V3, type ViewId } from './stations';

/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * ClearClaim World: the single WebGL environment behind the whole command center.
 *
 * Districts (all fed only by backend data passed in through the set* methods):
 *   deck     the 11 real workflow stages laid out by dependency wave, with tube pipes on the real edges
 *            and department pads under the stages each desk owns (owner from the backend DAG)
 *   policy   a gallery wall of the 59 cited rules, coloured by the outcome recorded for this encounter
 *   history  a constellation of historical peer claims (x = bill total, y = length of stay, z = room
 *            category) with the current encounter and the median / threshold band
 *   lab      a 3D Gantt chart: one lane per measured run, bars at recorded start/end offsets
 *   audit    the encounter's audit events as a chain of linked blocks, tinted by the chain check
 *
 * Rendering is on demand: a frame is drawn only while something moves (camera flight, damping, a
 * running pulse, replay packets) or after data changes, so an idle scene costs nothing.
 */

export type Pick =
  | { kind: 'stage'; id: string } | { kind: 'rule'; id: string } | { kind: 'peer'; index: number }
  | { kind: 'bar'; lane: number; stage: string } | { kind: 'audit'; id: number } | { kind: 'department'; id: string };

export interface StageDef { id: string; deps: string[]; label?: string; owner?: string }
export interface RuleDot { id: string; status: string; group: string }
export interface PeerDot { total: number; los: number; room: string | null }
export interface LabLane { label: string; bars: { stage: string; start: number; end: number; status: string }[] }
export interface AuditBlock { id: number; type: string }

const STATE_COLOR: Record<StageState, { body: number; top: number; emissive: number; glow: number }> = {
  waiting: { body: 0x223047, top: 0x3d5577, emissive: 0x0b1a2e, glow: 0.15 },
  ready: { body: 0x1b2f45, top: 0x2b5c78, emissive: 0x0e7490, glow: 0.25 },
  running: { body: 0x0c3d5c, top: 0x38bdf8, emissive: 0x38bdf8, glow: 0.95 },
  completed: { body: 0x10372d, top: 0x34d399, emissive: 0x10b981, glow: 0.35 },
  issue: { body: 0x3d2f0d, top: 0xfbbf24, emissive: 0xf59e0b, glow: 0.4 },
  blocked: { body: 0x45171c, top: 0xf87171, emissive: 0xef4444, glow: 0.55 },
  failed: { body: 0x5a1218, top: 0xff4d5e, emissive: 0xff1f3d, glow: 0.85 },
  reused: { body: 0x1e2636, top: 0x7c8aa3, emissive: 0x334155, glow: 0.15 },
  notrun: { body: 0x1a2232, top: 0x2f3a52, emissive: 0x000000, glow: 0 },
  skipped: { body: 0x2a1418, top: 0x7f1d1d, emissive: 0x7f1d1d, glow: 0.2 },
};
const MARK_RING: Record<SceneMark, number> = { root: 0xf87171, consequence: 0xfbbf24, downstream: 0x38bdf8, rerun: 0x22d3ee, kept: 0x64748b };
export const RULE_COLOR: Record<string, number> = {
  PASS: 0x34d399, WARNING: 0xfbbf24, PARTIAL_DEDUCTION: 0x38bdf8, FAIL: 0xf87171,
  NO_RESULT: 0x3b4a63, NOT_APPLICABLE: 0x223250, QUARANTINED: 0xa78bfa, CATALOG: 0x4b6a92,
};
const BAR_COLOR: Record<string, number> = { SUCCEEDED: 0x2dd4bf, FAILED: 0xf87171, REUSED: 0x64748b };
const BOX = { w: 2.6, h: 1.0, d: 1.7 };
const v3 = (a: V3) => new THREE.Vector3(a[0], a[1], a[2]);

export function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch { return false; }
}

/** Positions of the stages on the deck: a U-shape following the dependency waves (back row, then front row). */
export function deckLayout(waves: string[][]): Record<string, THREE.Vector3> {
  const pos: Record<string, THREE.Vector3> = {};
  const half = Math.ceil(waves.length / 2);
  waves.forEach((w, i) => {
    const back = i < half;
    const col = back ? i : waves.length - 1 - i;
    const x = (col - (half - 1) / 2) * 6.8;
    const z = back ? -4.8 : 4.8;
    w.forEach((s, j) => {
      const off = w.length === 1 ? 0 : (j - (w.length - 1) / 2) * 2;
      pos[s] = new THREE.Vector3(x, 0, z + off * 1.8);
    });
  });
  return pos;
}

type Label = { el: HTMLElement; at: () => THREE.Vector3 | null; minView?: (v: ViewId) => boolean };

export class World {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(40, 1, 0.1, 400);
  readonly controls: OrbitControls;
  private host: HTMLElement;
  private reduced: boolean;
  private onPick: (p: Pick | null) => void;
  private dirty = true;
  private clock = new THREE.Clock();
  private flight: { fromP: THREE.Vector3; fromT: THREE.Vector3; toP: THREE.Vector3; toT: THREE.Vector3; t0: number; dur: number } | null = null;
  private labels = new Map<string, Label>();
  private view: ViewId = 'overview';
  private inset = { right: 0, left: 0, top: 0, bottom: 0 };
  private ro: ResizeObserver;
  private pickables: THREE.Object3D[] = [];
  private disposed = false;

  // deck
  private deck = new THREE.Group();
  private nodes: Record<string, { g: THREE.Group; body: THREE.Mesh; top: THREE.Mesh; ring: THREE.Mesh; halo: THREE.Mesh; lift: number; liftTo: number; pulse: boolean }> = {};
  private edges: { from: string; to: string; curve: THREE.CatmullRomCurve3; mat: THREE.MeshStandardMaterial; packet: THREE.Mesh; flowing: boolean; t: number }[] = [];
  private pads: Record<string, THREE.Mesh> = {};
  pos: Record<string, THREE.Vector3> = {};
  private stages: StageDef[] = [];
  // districts
  private policy = new THREE.Group();
  private ruleTiles: { id: string; mesh: THREE.Mesh; base: THREE.Vector3 }[] = [];
  private history = new THREE.Group();
  private peerPos: THREE.Vector3[] = [];
  private current: THREE.Mesh | null = null;
  private lab = new THREE.Group();
  private bars: THREE.Mesh[] = [];
  private cursor: THREE.Mesh | null = null;
  private labScale = 1;
  private labLanes = 0;
  private audit = new THREE.Group();
  private auditBlocks: THREE.Mesh[] = [];
  private beacons: Record<string, THREE.Mesh> = {};

  constructor(host: HTMLElement, opts: { reduced: boolean; onPick: (p: Pick | null) => void }) {
    this.host = host;
    this.reduced = opts.reduced;
    this.onPick = opts.onPick;
    const r = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.05;
    r.domElement.className = 'world-canvas';
    r.domElement.setAttribute('aria-hidden', 'true');
    host.prepend(r.domElement);
    this.renderer = r;

    const s = this.scene;
    s.background = new THREE.Color(0x040913);
    s.fog = new THREE.Fog(0x040913, 90, 210);
    s.add(new THREE.HemisphereLight(0xbfe3ff, 0x0a1322, 1.1));
    s.add(new THREE.AmbientLight(0x6f8fb8, 0.3));
    const sun = new THREE.DirectionalLight(0xffffff, 2.1);
    sun.position.set(-20, 40, 24);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -30, right: 30, top: 22, bottom: -22, near: 1, far: 120 });
    s.add(sun);
    const teal = new THREE.PointLight(0x2dd4bf, 40, 60, 2); teal.position.set(10, 8, 8); s.add(teal);
    const violet = new THREE.PointLight(0x8b5cf6, 24, 60, 2); violet.position.set(-16, 7, -10); s.add(violet);

    this.buildGround();
    s.add(this.deck, this.policy, this.history, this.lab, this.audit);

    this.camera.position.copy(v3(STATION.overview.pos));
    const c = new OrbitControls(this.camera, r.domElement);
    c.target.copy(v3(STATION.overview.target));
    c.enableDamping = !this.reduced;
    c.dampingFactor = 0.09;
    c.enableZoom = true;
    c.zoomSpeed = 0.6;
    c.minPolarAngle = 0.12; c.maxPolarAngle = 1.38;
    c.minDistance = 6; c.maxDistance = 150;
    c.addEventListener('change', () => { this.dirty = true; });
    c.addEventListener('start', () => { this.flight = null; });
    c.update();
    this.controls = c;

    let down: { x: number; y: number } | null = null;
    r.domElement.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY }; });
    r.domElement.addEventListener('pointerup', (e) => {
      if (!down) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5;
      down = null;
      if (!moved) this.onPick(this.pickAt(e.clientX, e.clientY));
    });
    r.domElement.addEventListener('pointermove', (e) => {
      if (e.buttons) return;
      r.domElement.style.cursor = this.pickAt(e.clientX, e.clientY) ? 'pointer' : 'grab';
    });

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.resize();
    r.setAnimationLoop(() => this.frame());
  }

  // ------------------------------------------------------------------ static environment
  private buildGround() {
    const s = this.scene;
    const floor = new THREE.Mesh(new THREE.CircleGeometry(160, 96), new THREE.MeshStandardMaterial({ color: 0x060d19, roughness: 0.96, metalness: 0.04 }));
    floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; s.add(floor);
    const grid = new THREE.GridHelper(220, 110, 0x16405a, 0x0e2236);
    const gm = grid.material as THREE.Material; gm.transparent = true; gm.opacity = 0.55;
    grid.position.y = 0.01; s.add(grid);
    // district plinths and transit paths from the hub, so every place reads as part of one floor plan
    const plinth = (at: V3, w: number, d: number, color: number) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(w, 0.18, d), new THREE.MeshStandardMaterial({ color, roughness: 0.7, metalness: 0.25 }));
      m.position.set(at[0], 0.09, at[2]); m.receiveShadow = true; s.add(m);
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), new THREE.LineBasicMaterial({ color: 0x1f6f8a, transparent: true, opacity: 0.6 }));
      edge.position.copy(m.position); s.add(edge);
    };
    plinth(DISTRICT.deck, 42, 22, 0x0a1628);
    plinth(DISTRICT.policy, 30, 22, 0x0b1426);
    plinth(DISTRICT.history, 30, 24, 0x0a1727);
    plinth(DISTRICT.lab, 44, 22, 0x0a1527);
    plinth(DISTRICT.audit, 44, 10, 0x0b1526);
    const pathMat = new THREE.MeshBasicMaterial({ color: 0x1b5c78, transparent: true, opacity: 0.55 });
    const path = (a: V3, b: V3) => {
      const A = v3(a), B = v3(b);
      const len = A.distanceTo(B);
      const m = new THREE.Mesh(new THREE.PlaneGeometry(0.35, len), pathMat);
      m.rotation.x = -Math.PI / 2;
      m.rotation.z = -Math.atan2(B.x - A.x, B.z - A.z);
      m.position.set((A.x + B.x) / 2, 0.2, (A.z + B.z) / 2);
      s.add(m);
    };
    path([21, 0, 0], [DISTRICT.history[0] - 15, 0, DISTRICT.history[2]]);
    path([-21, 0, 0], [DISTRICT.policy[0] + 15, 0, DISTRICT.policy[2]]);
    path([0, 0, -11], [0, 0, DISTRICT.lab[2] + 11]);
    path([0, 0, 11], [0, 0, DISTRICT.audit[2] - 5]);
    // beacons: soft light columns that mark each district from far away
    const beacon = (key: string, at: V3, color: number) => {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 14, 8, 1, true),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.35, depthWrite: false }));
      m.position.set(at[0], 7, at[2]); s.add(m); this.beacons[key] = m;
    };
    beacon('policy', [DISTRICT.policy[0] - 14, 0, DISTRICT.policy[2] - 10], 0xa78bfa);
    beacon('history', [DISTRICT.history[0] + 14, 0, DISTRICT.history[2] - 11], 0x2dd4bf);
    beacon('lab', [DISTRICT.lab[0] - 21, 0, DISTRICT.lab[2] - 10], 0x38bdf8);
    beacon('audit', [DISTRICT.audit[0] - 21, 0, DISTRICT.audit[2] - 4], 0xf472b6);
  }

  // ------------------------------------------------------------------ deck (workflow)
  setWorkflow(stages: StageDef[], waves: string[][]) {
    this.clear(this.deck);
    this.nodes = {}; this.edges = []; this.pads = {};
    this.stages = stages;
    this.pos = deckLayout(waves);
    const owner = Object.fromEntries(stages.map((s) => [s.id, s.owner]));
    // department pads: one pad under each stage, tinted by the owning department
    Object.entries(this.pos).forEach(([id, p]) => {
      const dep = departmentOf(owner[id]);
      const pad = new THREE.Mesh(new THREE.BoxGeometry(BOX.w + 1.4, 0.06, BOX.d + 1.1),
        new THREE.MeshStandardMaterial({ color: dep?.color ?? 0x334155, emissive: dep?.color ?? 0x000000, emissiveIntensity: 0.08, transparent: true, opacity: 0.22, roughness: 0.6 }));
      pad.position.set(p.x, 0.21, p.z);
      pad.userData.pick = { kind: 'department', id: dep?.id || '' };
      this.deck.add(pad); this.pads[id] = pad;
    });
    Object.entries(this.pos).forEach(([id, p]) => {
      const g = new THREE.Group(); g.position.copy(p);
      const body = new THREE.Mesh(new THREE.BoxGeometry(BOX.w, BOX.h, BOX.d), new THREE.MeshStandardMaterial({ color: STATE_COLOR.waiting.body, roughness: 0.42, metalness: 0.4 }));
      body.position.y = BOX.h / 2 + 0.24; body.castShadow = true; body.receiveShadow = true;
      const top = new THREE.Mesh(new THREE.BoxGeometry(BOX.w - 0.2, 0.07, BOX.d - 0.2),
        new THREE.MeshStandardMaterial({ color: STATE_COLOR.waiting.top, emissive: 0x000000, roughness: 0.3, metalness: 0.2 }));
      top.position.y = BOX.h + 0.28;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(1.75, 0.055, 10, 72), new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0 }));
      ring.rotation.x = Math.PI / 2; ring.position.y = 0.27;
      const halo = new THREE.Mesh(new THREE.CylinderGeometry(1.25, 1.25, 3.2, 32, 1, true),
        new THREE.MeshBasicMaterial({ color: 0xf87171, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }));
      halo.position.y = 1.8;
      body.userData.pick = { kind: 'stage', id }; top.userData.pick = { kind: 'stage', id };
      g.add(body, top, ring, halo);
      this.deck.add(g);
      this.nodes[id] = { g, body, top, ring, halo, lift: 0, liftTo: 0, pulse: false };
    });
    stages.forEach((s) => s.deps.forEach((d) => {
      const a = this.pos[d], b = this.pos[s.id];
      if (!a || !b) return;
      const mid = a.clone().lerp(b, 0.5);
      const curve = new THREE.CatmullRomCurve3([new THREE.Vector3(a.x, 0.7, a.z), new THREE.Vector3(mid.x, 1.6, mid.z), new THREE.Vector3(b.x, 0.7, b.z)]);
      const mat = new THREE.MeshStandardMaterial({ color: 0x2f4d73, emissive: 0x000000, roughness: 0.4, metalness: 0.5 });
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 40, 0.1, 10, false), mat);
      const packet = new THREE.Mesh(new THREE.SphereGeometry(0.17, 14, 14), new THREE.MeshBasicMaterial({ color: 0xe0fbff }));
      packet.visible = false;
      this.deck.add(tube, packet);
      this.edges.push({ from: d, to: s.id, curve, mat, packet, flowing: false, t: 0 });
    }));
    this.refreshPickables();
    this.dirty = true;
  }

  setDeckState(snaps: Record<string, StageSnap>, marks: Record<string, SceneMark>, selected: string | null, playing: boolean, department: string | null) {
    const sel = selected ? this.stages.find((s) => s.id === selected) : null;
    const owner = Object.fromEntries(this.stages.map((s) => [s.id, s.owner]));
    Object.entries(this.nodes).forEach(([id, n]) => {
      const st: StageState = snaps[id]?.state || 'waiting';
      const c = STATE_COLOR[st];
      (n.body.material as THREE.MeshStandardMaterial).color.setHex(c.body);
      const tm = n.top.material as THREE.MeshStandardMaterial;
      tm.color.setHex(c.top); tm.emissive.setHex(c.emissive); tm.emissiveIntensity = c.glow;
      n.liftTo = st === 'running' ? 0.7 : selected === id ? 0.35 : 0;
      n.pulse = st === 'running';
      const mk = marks[id];
      const rm = n.ring.material as THREE.MeshBasicMaterial;
      rm.opacity = st === 'running' ? 0.85 : selected === id ? 0.9 : mk ? 0.75 : 0;
      rm.color.setHex(st === 'running' ? 0x38bdf8 : selected === id ? 0xe2e8f0 : mk ? MARK_RING[mk] : 0x38bdf8);
      const hm = n.halo.material as THREE.MeshBasicMaterial;
      hm.opacity = mk === 'root' ? 0.16 : 0;
      const bm = n.body.material as THREE.MeshStandardMaterial;
      const dim = (sel && id !== selected && !sel.deps.includes(id) && !this.stages.some((t) => t.id === id && t.deps.includes(selected!)))
        || (department && departmentOf(owner[id])?.id !== department);
      bm.transparent = st === 'notrun' || !!dim; bm.opacity = dim ? 0.35 : st === 'notrun' ? 0.7 : 1;
      const pad = this.pads[id];
      if (pad) {
        const pm = pad.material as THREE.MeshStandardMaterial;
        const on = this.view === 'departments' || department;
        const mine = !department || departmentOf(owner[id])?.id === department;
        pm.opacity = on ? (mine ? 0.7 : 0.1) : 0.2;
        pm.emissiveIntensity = on && mine ? 0.45 : 0.08;
      }
    });
    const DONE = ['completed', 'issue', 'blocked'];
    this.edges.forEach((e) => {
      const sa = snaps[e.from]?.state, sb = snaps[e.to]?.state;
      const delivered = !!sa && DONE.includes(sa) && !!sb && ['running', 'completed', 'issue', 'blocked', 'failed'].includes(sb);
      const hi = !!sel && ((e.to === selected && sel.deps.includes(e.from)) || e.from === selected);
      const ma = marks[e.from], mb = marks[e.to];
      const linked = !!ma && !!mb && ma !== 'kept' && mb !== 'kept';
      e.flowing = playing && delivered && sb === 'running';
      e.mat.color.setHex(hi ? 0xe2e8f0 : linked ? 0x22d3ee : delivered ? 0x2dd4bf : 0x2f4d73);
      e.mat.emissive.setHex(hi ? 0x94a3b8 : linked ? 0x0891b2 : delivered ? 0x14b8a6 : 0x000000);
      e.mat.emissiveIntensity = hi ? 0.7 : linked ? 0.85 : delivered ? 0.6 : 0;
    });
    this.dirty = true;
  }

  // ------------------------------------------------------------------ policy gallery
  setRules(rules: RuleDot[], selected: string | null) {
    if (this.ruleTiles.length !== rules.length || this.ruleTiles.some((t, i) => t.id !== rules[i].id)) {
      this.clear(this.policy); this.ruleTiles = [];
      const C = v3(DISTRICT.policy);
      const cols = 12, R = 13;
      const geo = new THREE.BoxGeometry(1.25, 1.0, 0.18);
      rules.forEach((r, i) => {
        const col = i % cols, row = Math.floor(i / cols);
        const th = ((col - (cols - 1) / 2) / (cols - 1)) * 1.5;
        const base = new THREE.Vector3(C.x + Math.sin(th) * R, 1.1 + row * 1.3, C.z - Math.cos(th) * R + 6);
        const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: RULE_COLOR.CATALOG, emissive: RULE_COLOR.CATALOG, emissiveIntensity: 0.15, roughness: 0.35, metalness: 0.3 }));
        m.position.copy(base);
        m.lookAt(C.x, base.y, C.z + 6);
        m.castShadow = true;
        m.userData.pick = { kind: 'rule', id: r.id };
        this.policy.add(m);
        this.ruleTiles.push({ id: r.id, mesh: m, base });
      });
      const back = new THREE.Mesh(new THREE.CylinderGeometry(R + 0.6, R + 0.6, 7.6, 48, 1, true, Math.PI - 0.82, 1.64),
        new THREE.MeshStandardMaterial({ color: 0x0b1730, roughness: 0.9, side: THREE.BackSide, transparent: true, opacity: 0.85 }));
      back.position.set(C.x, 3.9, C.z + 6);
      this.policy.add(back);
      this.refreshPickables();
    }
    this.ruleTiles.forEach((t, i) => {
      const col = RULE_COLOR[rules[i].status] ?? RULE_COLOR.CATALOG;
      const m = t.mesh.material as THREE.MeshStandardMaterial;
      m.color.setHex(col); m.emissive.setHex(col);
      m.emissiveIntensity = t.id === selected ? 0.9 : rules[i].status === 'NOT_APPLICABLE' ? 0.08 : 0.25;
      const toward = v3(DISTRICT.policy).add(new THREE.Vector3(0, 0, 6)).sub(t.base).setY(0).normalize();
      t.mesh.position.copy(t.base).addScaledVector(toward, t.id === selected ? 0.9 : 0);
      t.mesh.scale.setScalar(t.id === selected ? 1.18 : 1);
    });
    this.dirty = true;
  }

  // ------------------------------------------------------------------ historical constellation
  setHistory(peers: PeerDot[], current: { total: number | null; los: number | null } | null,
    band: { median: number; lo: number; hi: number; medianLos: number | null } | null, selected: number | null) {
    this.clear(this.history); this.current = null; this.peerPos = [];
    const H = v3(DISTRICT.history);
    const base = new THREE.Mesh(new THREE.BoxGeometry(24, 0.05, 14), new THREE.MeshStandardMaterial({ color: 0x0c1d33, roughness: 0.8 }));
    base.position.set(H.x, 0.22, H.z); this.history.add(base);
    const axisMat = new THREE.LineBasicMaterial({ color: 0x3b6f91 });
    const axes = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(H.x - 12, 0.25, H.z + 7), new THREE.Vector3(H.x + 12, 0.25, H.z + 7),
      new THREE.Vector3(H.x - 12, 0.25, H.z + 7), new THREE.Vector3(H.x - 12, 9, H.z + 7),
      new THREE.Vector3(H.x - 12, 0.25, H.z + 7), new THREE.Vector3(H.x - 12, 0.25, H.z - 7)]);
    this.history.add(new THREE.LineSegments(axes, axisMat));
    if (!peers.length) { this.refreshPickables(); this.dirty = true; return; }
    const totals = peers.map((p) => p.total).concat(current?.total != null ? [current.total] : []).concat(band ? [band.lo, band.hi] : []);
    const stays = peers.map((p) => p.los).concat(current?.los != null ? [current.los] : []);
    const tMin = Math.min(...totals), tMax = Math.max(...totals), lMax = Math.max(1, ...stays);
    const rooms = Array.from(new Set(peers.map((p) => p.room || '—'))).sort();
    const X = (t: number) => H.x - 11 + ((t - tMin) / Math.max(1, tMax - tMin)) * 22;
    const Y = (l: number) => 0.6 + (l / lMax) * 8;
    const Z = (r: string | null) => H.z + (rooms.length > 1 ? (rooms.indexOf(r || '—') / (rooms.length - 1) - 0.5) * 10 : 0);
    const im = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(0.2, 1), new THREE.MeshStandardMaterial({ color: 0x7dd3fc, emissive: 0x0e7490, emissiveIntensity: 0.6, roughness: 0.3 }), peers.length);
    const mtx = new THREE.Matrix4();
    peers.forEach((p, i) => {
      const at = new THREE.Vector3(X(p.total), Y(p.los), Z(p.room));
      this.peerPos.push(at);
      mtx.makeScale(i === selected ? 2 : 1, i === selected ? 2 : 1, i === selected ? 2 : 1).setPosition(at);
      im.setMatrixAt(i, mtx);
      im.setColorAt(i, new THREE.Color(i === selected ? 0xffffff : 0x7dd3fc));
    });
    im.userData.pick = { kind: 'peer' };
    this.history.add(im);
    if (band) {
      const box = new THREE.Mesh(new THREE.BoxGeometry(Math.max(0.1, X(band.hi) - X(band.lo)), 8.6, 11),
        new THREE.MeshBasicMaterial({ color: 0x2dd4bf, transparent: true, opacity: 0.06, depthWrite: false }));
      box.position.set((X(band.hi) + X(band.lo)) / 2, 4.6, H.z); this.history.add(box);
      const med = new THREE.Mesh(new THREE.PlaneGeometry(0.06, 8.6), new THREE.MeshBasicMaterial({ color: 0x2dd4bf, transparent: true, opacity: 0.8, side: THREE.DoubleSide }));
      med.position.set(X(band.median), 4.6, H.z + 6.9); this.history.add(med);
      if (band.medianLos != null) {
        const ml = new THREE.Mesh(new THREE.PlaneGeometry(22, 0.06), new THREE.MeshBasicMaterial({ color: 0x2dd4bf, transparent: true, opacity: 0.5, side: THREE.DoubleSide }));
        ml.position.set(H.x, Y(band.medianLos), H.z + 6.9); this.history.add(ml);
      }
    }
    if (current && current.total != null && current.los != null) {
      const m = new THREE.Mesh(new THREE.OctahedronGeometry(0.55), new THREE.MeshStandardMaterial({ color: 0xfbbf24, emissive: 0xf59e0b, emissiveIntensity: 0.9 }));
      m.position.set(X(current.total), Y(current.los), H.z);
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, m.position.y), new THREE.MeshBasicMaterial({ color: 0xfbbf24, transparent: true, opacity: 0.6 }));
      stem.position.set(m.position.x, m.position.y / 2, m.position.z);
      this.history.add(m, stem); this.current = m;
    }
    this.refreshPickables();
    this.dirty = true;
  }
  historyAxis(which: 'x' | 'y') {
    const H = DISTRICT.history;
    return which === 'x' ? new THREE.Vector3(H[0] + 9, 0.4, H[2] + 7.6) : new THREE.Vector3(H[0] - 12, 9.6, H[2] + 7);
  }
  currentPos() { return this.current ? this.current.position.clone().add(new THREE.Vector3(0, 0.9, 0)) : null; }
  peerAt(i: number | null) { return i === null || !this.peerPos[i] ? null : this.peerPos[i].clone().add(new THREE.Vector3(0, 0.5, 0)); }

  // ------------------------------------------------------------------ execution lab (3D Gantt)
  setLab(lanes: LabLane[], order: string[], selected: { lane: number; stage: string } | null) {
    this.clear(this.lab); this.bars = []; this.cursor = null; this.labLanes = lanes.length;
    const L = v3(DISTRICT.lab);
    const maxEnd = Math.max(0.001, ...lanes.flatMap((l) => l.bars.map((b) => b.end)));
    const W = 34;
    this.labScale = W / maxEnd;
    const rowD = 0.62;
    lanes.forEach((lane, li) => {
      const z0 = L.z - 7 + li * (order.length * rowD + 2.4);
      const floor = new THREE.Mesh(new THREE.BoxGeometry(W + 2, 0.06, order.length * rowD + 0.6),
        new THREE.MeshStandardMaterial({ color: li === 0 ? 0x0f2238 : 0x112a2a, roughness: 0.8 }));
      floor.position.set(L.x, 0.22, z0 + (order.length * rowD) / 2 - rowD / 2); this.lab.add(floor);
      lane.bars.forEach((b) => {
        const row = order.indexOf(b.stage);
        if (row < 0) return;
        const w = Math.max(0.06, (b.end - b.start) * this.labScale);
        const h = b.status === 'REUSED' ? 0.12 : 0.55;
        const sel = selected && selected.lane === li && selected.stage === b.stage;
        const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, rowD * 0.72),
          new THREE.MeshStandardMaterial({ color: BAR_COLOR[b.status] ?? 0x2dd4bf, emissive: BAR_COLOR[b.status] ?? 0x2dd4bf, emissiveIntensity: sel ? 0.9 : 0.3, roughness: 0.35 }));
        m.position.set(L.x - W / 2 + b.start * this.labScale + w / 2, 0.25 + h / 2, z0 + row * rowD);
        m.castShadow = true;
        m.userData.pick = { kind: 'bar', lane: li, stage: b.stage };
        this.lab.add(m); this.bars.push(m);
      });
    });
    if (lanes.length) {
      const depth = lanes.length * (order.length * rowD + 2.4);
      const cur = new THREE.Mesh(new THREE.PlaneGeometry(depth, 2.2), new THREE.MeshBasicMaterial({ color: 0xe2e8f0, transparent: true, opacity: 0.12, side: THREE.DoubleSide, depthWrite: false }));
      cur.rotation.y = Math.PI / 2;
      cur.position.set(L.x - W / 2, 1.2, L.z - 7 + depth / 2 - 1.5);
      cur.visible = false;
      this.lab.add(cur); this.cursor = cur;
    }
    this.refreshPickables();
    this.dirty = true;
  }
  /** Replay cursor at `t` ms of recorded time (null hides it). */
  setLabCursor(t: number | null) {
    if (!this.cursor) return;
    this.cursor.visible = t !== null;
    if (t !== null) this.cursor.position.x = DISTRICT.lab[0] - 17 + t * this.labScale;
    this.dirty = true;
  }
  laneAnchor(i: number) {
    if (i >= this.labLanes) return null;
    return new THREE.Vector3(DISTRICT.lab[0] - 18.5, 0.6, DISTRICT.lab[2] - 7 + i * (11 * 0.62 + 2.4) + 3);
  }

  // ------------------------------------------------------------------ audit chain
  setAudit(blocks: AuditBlock[], ok: boolean | null, selected: number | null) {
    this.clear(this.audit); this.auditBlocks = [];
    const A = v3(DISTRICT.audit);
    const shown = blocks.slice(-28);
    const step = shown.length > 1 ? Math.min(1.6, 40 / shown.length) : 1.6;
    const x0 = A.x - ((shown.length - 1) * step) / 2;
    const tint = ok === false ? 0xf87171 : ok ? 0x34d399 : 0x64748b;
    const link = new THREE.MeshStandardMaterial({ color: tint, emissive: tint, emissiveIntensity: 0.4 });
    shown.forEach((b, i) => {
      const x = x0 + i * step;
      const typeColor = /CORRECTION/.test(b.type) ? 0xf472b6 : /REVIEW|DECISION/.test(b.type) ? 0xa78bfa : /RUN|PIPELINE|STAGE/.test(b.type) ? 0x38bdf8 : 0x94a3b8;
      const m = new THREE.Mesh(new THREE.BoxGeometry(step * 0.62, 0.9, 1.4),
        new THREE.MeshStandardMaterial({ color: typeColor, emissive: typeColor, emissiveIntensity: b.id === selected ? 0.9 : 0.22, roughness: 0.35, metalness: 0.3 }));
      m.position.set(x, b.id === selected ? 1.25 : 0.75, A.z);
      m.castShadow = true;
      m.userData.pick = { kind: 'audit', id: b.id };
      this.audit.add(m); this.auditBlocks.push(m);
      if (i > 0) {
        const c = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, step * 0.38, 8), link);
        c.rotation.z = Math.PI / 2; c.position.set(x - step / 2, 0.75, A.z);
        this.audit.add(c);
      }
    });
    this.refreshPickables();
    this.dirty = true;
  }
  auditAnchor(id: number | null) {
    const m = this.auditBlocks.find((b) => b.userData.pick.id === id);
    return m ? m.position.clone().add(new THREE.Vector3(0, 0.8, 0)) : null;
  }

  // ------------------------------------------------------------------ camera & stations
  setView(view: ViewId, focus?: THREE.Vector3 | null) {
    this.view = view;
    const st = STATION[view];
    let toP = v3(st.pos), toT = v3(st.target);
    if (focus && (view === 'investigate' || view === 'correct')) {
      toT = new THREE.Vector3(focus.x * 0.3, 0.5, focus.z * 0.15);
      toP = toT.clone().add(new THREE.Vector3(view === 'correct' ? 4 : -3, 22, 26));
    }
    this.flyTo(toP, toT);
  }
  focusStage(id: string) {
    const p = this.pos[id];
    if (p) this.flyTo(new THREE.Vector3(p.x - 3, 10, p.z + 13), new THREE.Vector3(p.x, 0.6, p.z));
  }
  resetView() { this.setView(this.view); }
  flyTo(toP: THREE.Vector3, toT: THREE.Vector3) {
    if (this.reduced) {
      this.camera.position.copy(toP); this.controls.target.copy(toT); this.controls.update(); this.flight = null; this.dirty = true; return;
    }
    const d = this.camera.position.distanceTo(toP);
    // wall-clock based so the flight ends on time even at low frame rates (software rendering)
    this.flight = { fromP: this.camera.position.clone(), fromT: this.controls.target.clone(), toP, toT, t0: performance.now(), dur: Math.min(2.2, 0.9 + d / 90) * 1000 };
  }
  zoom(f: number) {
    const off = this.camera.position.clone().sub(this.controls.target).multiplyScalar(f);
    this.flyTo(this.controls.target.clone().add(off), this.controls.target.clone());
  }
  /** Reserve screen space for 2D panels so the projection centre sits in the free area. */
  setInsets(i: Partial<typeof this.inset>) { this.inset = { ...this.inset, ...i }; this.resize(); }

  // ------------------------------------------------------------------ labels (HTML overlay)
  registerLabel(key: string, el: HTMLElement | null, at: () => THREE.Vector3 | null, minView?: (v: ViewId) => boolean) {
    if (!el) { this.labels.delete(key); return; }
    this.labels.set(key, { el, at, minView });
    this.dirty = true;
  }
  stageAnchor(id: string) {
    const n = this.nodes[id];
    return n ? new THREE.Vector3(n.g.position.x, n.g.position.y + BOX.h + 1.2, n.g.position.z) : null;
  }
  ruleAnchor(id: string | null) {
    const t = this.ruleTiles.find((r) => r.id === id);
    return t ? t.mesh.position.clone().add(new THREE.Vector3(0, 0.9, 0)) : null;
  }
  get currentView() { return this.view; }
  departmentAnchor(depId: string) {
    // tag sits on the outer edge of the deck beside the department's first stage, clear of the stage labels
    // billing and eligibility share a column (the parallel pair), so the insurance tag uses its last stage
    const own = this.stages.filter((s) => departmentOf(s.owner)?.id === depId);
    const anchor = depId === 'insurance' ? own[own.length - 1] : own[0];
    const p = anchor && this.pos[anchor.id];
    if (!p) return null;
    return new THREE.Vector3(p.x, 0.3, p.z > 0 ? 9.6 : -8.2);
  }

  // ------------------------------------------------------------------ internals
  private pickAt(cx: number, cy: number): Pick | null {
    const r = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, this.camera);
    const hit = ray.intersectObjects(this.pickables, false)[0];
    if (!hit) return null;
    const p = hit.object.userData.pick;
    if (p.kind === 'peer') return hit.instanceId === undefined ? null : { kind: 'peer', index: hit.instanceId };
    return p as Pick;
  }
  private refreshPickables() {
    this.pickables = [];
    this.scene.traverse((o) => { if (o.userData.pick) this.pickables.push(o); });
  }
  private clear(g: THREE.Group) {
    [...g.children].forEach((o) => {
      g.remove(o);
      o.traverse((x: any) => { x.geometry?.dispose?.(); (Array.isArray(x.material) ? x.material : x.material ? [x.material] : []).forEach((m: any) => m.dispose()); });
    });
  }
  private resize() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    const el = this.renderer.domElement;
    el.style.width = `${w}px`; el.style.height = `${h}px`;
    this.camera.aspect = w / h;
    // shift the projection so the scene centre sits in the area not covered by panels
    const dx = (this.inset.right - this.inset.left) / 2, dy = (this.inset.bottom - this.inset.top) / 2;
    if (dx || dy) this.camera.setViewOffset(w, h, dx, dy, w, h); else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }
  private frame() {
    if (this.disposed) return;
    const dt = Math.min(0.05, this.clock.getDelta());
    const t = this.clock.elapsedTime;
    let animating = false;
    if (this.flight) {
      const f = this.flight;
      const u = Math.min(1, (performance.now() - f.t0) / f.dur);
      const k = u < 0.5 ? 4 * u ** 3 : 1 - (-2 * u + 2) ** 3 / 2;
      // arc upward mid-flight so long transitions read as moving through the space
      const lift = Math.sin(Math.PI * k) * Math.min(18, f.fromP.distanceTo(f.toP) * 0.18);
      this.camera.position.lerpVectors(f.fromP, f.toP, k).add(new THREE.Vector3(0, lift, 0));
      this.controls.target.lerpVectors(f.fromT, f.toT, k);
      if (u >= 1) this.flight = null;
      animating = true;
    }
    if (this.controls.update()) animating = true;
    Object.values(this.nodes).forEach((n) => {
      if (Math.abs(n.liftTo - n.lift) > 0.002) {
        n.lift += (n.liftTo - n.lift) * (this.reduced ? 1 : Math.min(1, dt * 8));
        n.g.position.y = n.lift; animating = true;
      }
      if (n.pulse && !this.reduced) {
        const p = 0.5 + 0.5 * Math.sin(t * 6);
        (n.ring.material as THREE.MeshBasicMaterial).opacity = 0.35 + 0.55 * p;
        (n.top.material as THREE.MeshStandardMaterial).emissiveIntensity = 0.6 + 0.6 * p;
        animating = true;
      }
    });
    this.edges.forEach((e) => {
      if (e.flowing && !this.reduced) {
        e.t = (e.t + dt * 1.3) % 1;
        e.packet.position.copy(e.curve.getPointAt(e.t)); e.packet.visible = true; animating = true;
      } else if (e.packet.visible) { e.packet.visible = false; animating = true; }
    });
    if (this.current && !this.reduced && this.view === 'history') { this.current.rotation.y += dt * 0.8; animating = true; }
    if (!animating && !this.dirty) return;
    this.dirty = false;
    this.renderer.render(this.scene, this.camera);
    this.placeLabels();
  }
  private placeLabels() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    const v = new THREE.Vector3();
    const camPos = this.camera.position;
    this.labels.forEach((l) => {
      const p = l.at();
      const allowed = !l.minView || l.minView(this.view);
      if (!p || !allowed) { l.el.style.visibility = 'hidden'; return; }
      v.copy(p).project(this.camera);
      const x = (v.x * 0.5 + 0.5) * w, y = (-v.y * 0.5 + 0.5) * h;
      const vis = v.z < 1 && x > -40 && x < w + 40 && y > -40 && y < h + 40;
      l.el.style.visibility = vis ? 'visible' : 'hidden';
      l.el.style.transform = `translate(-50%, -100%) translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      const dist = camPos.distanceTo(p);
      l.el.style.zIndex = String(10000 - Math.round(dist * 10));
      l.el.style.setProperty('--lab-scale', String(Math.max(0.72, Math.min(1, 34 / dist))));
    });
  }
  dispose() {
    this.disposed = true;
    this.renderer.setAnimationLoop(null);
    this.ro.disconnect();
    this.controls.dispose();
    [this.deck, this.policy, this.history, this.lab, this.audit].forEach((g) => this.clear(g));
    this.scene.traverse((o: any) => { o.geometry?.dispose?.(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m: any) => m.dispose()); });
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.labels.clear();
  }
}

export { DEPARTMENTS };
