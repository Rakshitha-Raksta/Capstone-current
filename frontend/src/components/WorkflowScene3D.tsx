import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { stageLabel } from '../lib/format';
import { fmtMs, STATE_SHORT, type StageSnap, type StageState } from '../lib/controlRoom';
import { MARK_TEXT, type SceneMark } from '../lib/marks';

/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * WebGL (Three.js) scene of the REAL workflow graph: lit, shadow-casting 3D stage blocks on a
 * perspective grid floor, tube pipes along the real dependency edges, an orbit camera, and crisp
 * HTML labels projected onto the nodes. Every visual state comes from `snaps`, derived only from
 * recorded backend events; nothing here animates on its own except a running stage's pulse and the
 * data packet on an edge whose downstream stage is running in the replay.
 */
export interface SceneProps {
  stages: { id: string; deps: string[] }[]; waves: string[][]; snaps: Record<string, StageSnap>;
  selected?: string | null; onSelect?: (id: string) => void; playing?: boolean; interactive?: boolean;
  height?: number | string; overlay?: React.ReactNode; label?: string;
  /** Optional analysis marks (root blocker, impact, correction preview). They never change execution states. */
  marks?: Record<string, SceneMark>;
}

const MARK_RING: Record<SceneMark, [number, number]> = {
  root: [0xf87171, 0.9], consequence: [0xfbbf24, 0.75], downstream: [0x38bdf8, 0.45], rerun: [0x22d3ee, 0.85], kept: [0x64748b, 0.35],
};
const LINKED = new Set<SceneMark>(['root', 'consequence', 'downstream', 'rerun']);

const COLORS: Record<StageState, { body: number; top: number; emissive: number; glow: number }> = {
  waiting: { body: 0x2c3d57, top: 0x48638a, emissive: 0x0b1a2e, glow: 0.2 },
  ready: { body: 0x1b2f45, top: 0x2b5c78, emissive: 0x0e7490, glow: 0.25 },
  running: { body: 0x0c3d5c, top: 0x38bdf8, emissive: 0x38bdf8, glow: 0.9 },
  completed: { body: 0x0f3d31, top: 0x34d399, emissive: 0x10b981, glow: 0.35 },
  issue: { body: 0x40310c, top: 0xfbbf24, emissive: 0xf59e0b, glow: 0.35 },
  blocked: { body: 0x47181d, top: 0xf87171, emissive: 0xef4444, glow: 0.45 },
  failed: { body: 0x5a1218, top: 0xff4d5e, emissive: 0xff1f3d, glow: 0.8 },
  reused: { body: 0x1e2636, top: 0x64748b, emissive: 0x334155, glow: 0.15 },
  notrun: { body: 0x1d2636, top: 0x34405a, emissive: 0x000000, glow: 0 },
  skipped: { body: 0x2a1418, top: 0x7f1d1d, emissive: 0x7f1d1d, glow: 0.2 },
};
const DONE = new Set(['completed', 'issue', 'blocked']);
const BOX = { w: 2.4, h: 1.0, d: 1.6 };

export function webglAvailable(): boolean {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch { return false; }
}

function layout(waves: string[][]) {
  // U-shape in depth: upstream waves along the back row (left to right), then the workflow turns
  // toward the viewer and downstream waves run along the front row. The parallel pair is split into
  // two lanes (back and front of its row). Positions follow the real dependency waves.
  const pos: Record<string, THREE.Vector3> = {};
  const half = Math.ceil(waves.length / 2);
  waves.forEach((w, i) => {
    const back = i < half;
    const col = back ? i : waves.length - 1 - i;
    const x = (col - (half - 1) / 2) * 6.6;
    const z = back ? -4.6 : 4.6;
    w.forEach((s, j) => {
      const off = w.length === 1 ? 0 : (j - (w.length - 1) / 2) * 2;
      pos[s] = new THREE.Vector3(x, 0, z + off * 1.7);
    });
  });
  return pos;
}

export default function WorkflowScene3D({ stages, waves, snaps, selected = null, onSelect, playing = false, interactive = true, height = 560, overlay, label, marks }: SceneProps) {
  const host = useRef<HTMLDivElement>(null);
  const labelRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const api = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const pos = useMemo(() => layout(waves), [waves]);
  const order = useMemo(() => waves.flat(), [waves]);
  const reduced = typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  // ---------- build the scene once ----------
  useEffect(() => {
    const el = host.current;
    if (!el) return undefined;
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.className = 'wg-canvas';
    renderer.domElement.setAttribute('aria-hidden', 'true');
    el.prepend(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x050b16);
    scene.fog = new THREE.Fog(0x050b16, 40, 95);
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);
    const home = { pos: new THREE.Vector3(-5, 24, 18), target: new THREE.Vector3(0, 0, 0.4) };
    camera.position.copy(home.pos);

    scene.add(new THREE.HemisphereLight(0xb6e6ff, 0x0b1424, 1.25));
    scene.add(new THREE.AmbientLight(0x6f8fb8, 0.35));
    const sun = new THREE.DirectionalLight(0xffffff, 2.3);
    sun.position.set(-10, 18, 10);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -26, right: 26, top: 18, bottom: -18, near: 1, far: 70 });
    scene.add(sun);
    const rim = new THREE.PointLight(0x2dd4bf, 30, 40, 2);
    rim.position.set(8, 6, 6);
    scene.add(rim);
    const violet = new THREE.PointLight(0x8b5cf6, 18, 40, 2);
    violet.position.set(-14, 5, -8);
    scene.add(violet);

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(80, 50), new THREE.MeshStandardMaterial({ color: 0x07111f, roughness: 0.95, metalness: 0.05 }));
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);
    const grid = new THREE.GridHelper(80, 80, 0x1f7f90, 0x15324c);
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.75;
    grid.position.y = 0.01;
    scene.add(grid);

    // nodes: body (box with lit front/top/side faces), a glowing top plate and a base ring
    const nodes: Record<string, any> = {};
    order.forEach((id) => {
      const g = new THREE.Group();
      g.position.copy(pos[id]);
      const body = new THREE.Mesh(new THREE.BoxGeometry(BOX.w, BOX.h, BOX.d), new THREE.MeshStandardMaterial({ color: COLORS.waiting.body, roughness: 0.45, metalness: 0.35 }));
      body.position.y = BOX.h / 2;
      body.castShadow = true; body.receiveShadow = true;
      const top = new THREE.Mesh(new THREE.BoxGeometry(BOX.w - 0.18, 0.06, BOX.d - 0.18),
        new THREE.MeshStandardMaterial({ color: COLORS.waiting.top, emissive: 0x000000, emissiveIntensity: 0, roughness: 0.3, metalness: 0.2 }));
      top.position.y = BOX.h + 0.03;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(1.45, 0.05, 12, 64), new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0 }));
      ring.rotation.x = Math.PI / 2;
      ring.position.y = 0.04;
      body.userData.id = id; top.userData.id = id;
      g.add(body, top, ring);
      scene.add(g);
      nodes[id] = { g, body, top, ring, lift: 0, liftTarget: 0, pulse: false };
    });

    // pipes along the real dependency edges
    const edges: any[] = [];
    stages.forEach((s) => s.deps.forEach((d) => {
      const a = pos[d], b = pos[s.id];
      if (!a || !b) return;
      const mid = a.clone().lerp(b, 0.5);
      const curve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(a.x, 0.45, a.z), new THREE.Vector3(mid.x, 1.25, mid.z), new THREE.Vector3(b.x, 0.45, b.z)]);
      const mat = new THREE.MeshStandardMaterial({ color: 0x35557f, emissive: 0x000000, emissiveIntensity: 0, roughness: 0.4, metalness: 0.5 });
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 48, 0.1, 14, false), mat);
      tube.castShadow = true;
      const packet = new THREE.Mesh(new THREE.SphereGeometry(0.16, 16, 16), new THREE.MeshBasicMaterial({ color: 0xe0fbff }));
      packet.visible = false;
      scene.add(tube, packet);
      edges.push({ from: d, to: s.id, curve, mat, packet, flowing: false, t: 0 });
    }));

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.copy(home.target);
    controls.enableDamping = !reduced;
    controls.enableZoom = false;                     // wheel scrolls the page; Ctrl+wheel and buttons zoom
    controls.minPolarAngle = 0.25; controls.maxPolarAngle = 1.35;
    controls.minDistance = 8; controls.maxDistance = 90;
    controls.enabled = interactive;
    controls.update();

    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    let down: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY }; };
    const onUp = (e: PointerEvent) => {
      if (!down || !interactive) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4;
      down = null;
      if (moved) return;
      const r = renderer.domElement.getBoundingClientRect();
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const hit = ray.intersectObjects(Object.values(nodes).flatMap((n: any) => [n.body, n.top]))[0];
      if (hit) api.current?.onSelect?.(hit.object.userData.id);
    };
    const onWheel = (e: WheelEvent) => {
      if (!interactive || !(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      dolly(e.deltaY < 0 ? 0.9 : 1.1);
    };
    renderer.domElement.addEventListener('pointerdown', onDown);
    renderer.domElement.addEventListener('pointerup', onUp);
    renderer.domElement.addEventListener('wheel', onWheel, { passive: false });

    const camGoal = { pos: null as THREE.Vector3 | null, target: null as THREE.Vector3 | null };
    let userMoved = false;
    controls.addEventListener('start', () => { userMoved = true; });
    // place the home camera so every stage block (and its label) is on screen, seen from above-front
    const HOME_DIR = new THREE.Vector3(-0.1, 0.8, 0.62).normalize();
    function fitHome() {
      // the HUD covers the top of the canvas: fit the blocks and labels into NDC y in [-0.74, 0.45] (toolbar at the bottom)
      const pts = Object.values(pos).flatMap((p) => [new THREE.Vector3(p.x - 1.4, 0, p.z - 0.9), new THREE.Vector3(p.x + 1.4, 3.4, p.z + 0.9)]);
      const probe = camera.clone();
      const span = (tz: number, d: number) => {
        home.target.set(0, 0, tz);
        probe.position.copy(home.target).addScaledVector(HOME_DIR, d);
        probe.lookAt(home.target);
        probe.updateMatrixWorld();
        let lo = Infinity, hi = -Infinity, xm = 0;
        pts.forEach((q) => { const v2 = q.clone().project(probe); lo = Math.min(lo, v2.y); hi = Math.max(hi, v2.y); xm = Math.max(xm, Math.abs(v2.x)); });
        return { lo, hi, xm };
      };
      let d = 10, tz = 0;
      for (; d < 90; d += 0.5) {
        // slide the target so the projected block span is centred in the free area, then test the fit
        for (let k = 0; k < 6; k++) { const r = span(tz, d); tz -= ((r.lo + r.hi) / 2 + 0.145) * d * 0.35; }
        const r = span(tz, d);
        if (r.xm < 0.9 && r.hi < 0.45 && r.lo > -0.74) break;
      }
      home.target.set(0, 0, tz);
      home.pos.copy(home.target).addScaledVector(HOME_DIR, d);
      if (!userMoved && !camGoal.pos) { camera.position.copy(home.pos); controls.target.copy(home.target); controls.update(); }
    }
    function dolly(f: number) {
      const off = camera.position.clone().sub(controls.target).multiplyScalar(f);
      const len = Math.min(controls.maxDistance, Math.max(controls.minDistance, off.length()));
      camGoal.pos = controls.target.clone().add(off.setLength(len));
      camGoal.target = controls.target.clone();
    }
    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = `${w}px`; renderer.domElement.style.height = `${h}px`;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      fitHome();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    const v = new THREE.Vector3();
    const clock = new THREE.Clock();
    renderer.setAnimationLoop(() => {
      const dt = Math.min(0.05, clock.getDelta());
      const tsec = clock.elapsedTime;
      if (camGoal.pos && camGoal.target) {
        const k = reduced ? 1 : 1 - Math.pow(0.002, dt);
        camera.position.lerp(camGoal.pos, k);
        controls.target.lerp(camGoal.target, k);
        if (camera.position.distanceTo(camGoal.pos) < 0.01) { camGoal.pos = null; camGoal.target = null; }
      }
      controls.update();
      Object.values(nodes).forEach((n: any) => {
        n.lift += (n.liftTarget - n.lift) * (reduced ? 1 : Math.min(1, dt * 8));
        n.g.position.y = n.lift;
        if (n.pulse && !reduced) {
          const p = 0.5 + 0.5 * Math.sin(tsec * 6);
          n.ring.material.opacity = 0.35 + 0.55 * p;
          n.top.material.emissiveIntensity = 0.6 + 0.6 * p;
        }
      });
      edges.forEach((e) => {
        if (e.flowing && !reduced) {
          e.t = (e.t + dt * 1.4) % 1;
          e.packet.position.copy(e.curve.getPointAt(e.t));
          e.packet.visible = true;
        } else e.packet.visible = false;
      });
      renderer.render(scene, camera);
      // project HTML labels onto their nodes
      const w = el.clientWidth, h = el.clientHeight;
      order.forEach((id) => {
        const lab = labelRefs.current[id];
        const n = nodes[id];
        if (!lab || !n) return;
        v.set(n.g.position.x, n.g.position.y + BOX.h + 1.0, n.g.position.z).project(camera);
        const x = (v.x * 0.5 + 0.5) * w, y = (-v.y * 0.5 + 0.5) * h;
        lab.style.transform = `translate(-50%, -100%) translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
        lab.style.zIndex = String(1000 + Math.round((1 - v.z) * 1000));
        lab.style.visibility = v.z < 1 ? 'visible' : 'hidden';
      });
    });

    api.current = {
      nodes, edges, camera, controls, home, camGoal, dolly, renderer,
      reset: () => { userMoved = false; camGoal.pos = home.pos.clone(); camGoal.target = home.target.clone(); },
      focus: (id: string | null) => {
        if (!id || !nodes[id]) { camGoal.pos = home.pos.clone(); camGoal.target = home.target.clone(); return; }
        const p = pos[id];
        camGoal.target = new THREE.Vector3(p.x, 0.4, p.z);
        camGoal.pos = new THREE.Vector3(p.x - 3, 11, p.z + 12);
      },
      tilt: () => {
        const off = camera.position.clone().sub(controls.target);
        const sph = new THREE.Spherical().setFromVector3(off);
        sph.phi = sph.phi > 0.8 ? 0.45 : 1.05;
        camGoal.pos = controls.target.clone().add(new THREE.Vector3().setFromSpherical(sph));
        camGoal.target = controls.target.clone();
      },
    };
    // read-only camera readout for automated checks (position rounded to 0.1 units)
    el.dataset.ready = '1';
    const readout = () => { el.dataset.camera = camera.position.toArray().map((x) => x.toFixed(1)).join(','); };
    controls.addEventListener('change', readout);
    readout();
    setReady(true);
    return () => {
      renderer.setAnimationLoop(null);
      ro.disconnect();
      renderer.domElement.removeEventListener('pointerdown', onDown);
      renderer.domElement.removeEventListener('pointerup', onUp);
      renderer.domElement.removeEventListener('wheel', onWheel);
      controls.dispose();
      scene.traverse((o: any) => { o.geometry?.dispose?.(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m: any) => m.dispose()); });
      renderer.dispose();
      renderer.domElement.remove();
      api.current = null;
    };
  // the scene graph depends only on the (static) workflow structure
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order, pos, stages, interactive]);

  useEffect(() => { if (api.current) api.current.onSelect = onSelect; }, [onSelect, ready]);

  // ---------- apply recorded states ----------
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    order.forEach((id) => {
      const st: StageState = snaps[id]?.state || 'waiting';
      const c = COLORS[st];
      const n = a.nodes[id];
      n.body.material.color.setHex(c.body);
      n.top.material.color.setHex(c.top);
      n.top.material.emissive.setHex(c.emissive);
      n.top.material.emissiveIntensity = c.glow;
      n.liftTarget = st === 'running' ? 0.6 : 0;
      n.pulse = st === 'running';
      const mk = marks?.[id];
      n.ring.material.opacity = st === 'running' ? 0.8 : selected === id ? 0.6 : mk ? MARK_RING[mk][1] : 0;
      n.ring.material.color.setHex(st === 'running' ? 0x38bdf8 : selected === id ? 0xa78bfa : mk ? MARK_RING[mk][0] : 0x38bdf8);
      n.body.material.opacity = st === 'notrun' ? 0.75 : 1;
      n.body.material.transparent = st === 'notrun';
    });
    const sel = selected ? stages.find((s) => s.id === selected) : null;
    a.edges.forEach((e: any) => {
      const sa = snaps[e.from]?.state, sb = snaps[e.to]?.state;
      const delivered = DONE.has(sa) && ['running', 'completed', 'issue', 'blocked', 'failed'].includes(sb);
      const hi = !!sel && ((e.to === selected && sel.deps.includes(e.from)) || e.from === selected);
      e.flowing = playing && delivered && sb === 'running';
      const ma = marks?.[e.from], mb = marks?.[e.to];
      const linked = !!ma && !!mb && LINKED.has(ma) && LINKED.has(mb);
      e.mat.color.setHex(hi ? 0xa78bfa : linked ? 0x22d3ee : delivered ? 0x2dd4bf : 0x35557f);
      e.mat.emissive.setHex(hi ? 0x7c3aed : linked ? 0x0891b2 : delivered ? 0x14b8a6 : 0x000000);
      e.mat.emissiveIntensity = hi ? 0.9 : linked ? 0.85 : delivered ? 0.7 : 0;
    });
  }, [snaps, selected, playing, ready, order, stages, marks]);

  useEffect(() => { api.current?.focus(selected); }, [selected, ready]);

  const running = order.filter((s) => snaps[s]?.state === 'running');
  const upSel = selected ? new Set(stages.find((s) => s.id === selected)?.deps || []) : new Set<string>();
  const downSel = new Set(stages.filter((s) => selected && s.deps.includes(selected)).map((s) => s.id));
  return (
    <div className="wg" ref={host} style={{ height }} role="group" data-renderer="webgl"
      aria-label={label || 'Three-dimensional view of the 11 workflow stages and their real dependencies. Select a stage to inspect it.'}>
      <div className="wg-labels">
        {order.map((id, k) => {
          const sn = snaps[id] || ({ state: 'waiting', label: 'Waiting' } as StageSnap);
          const fin = sn.duration !== undefined && ['completed', 'issue', 'blocked', 'failed'].includes(sn.state);
          const nf = (sn.findings || []).length;
          const rel = !selected ? '' : id === selected ? 'sel' : upSel.has(id) ? 'up' : downSel.has(id) ? 'down' : marks?.[id] ? '' : 'other';
          const mk = marks?.[id];
          return (
            <div key={id} ref={(r) => { labelRefs.current[id] = r; }} className={`s3d-node wg-label st-${sn.state} ${rel} ${mk ? `mk-${mk}` : ''}`} data-stage={id} data-state={sn.state} data-mark={mk || undefined}>
              <button type="button" className="s3d-bb" aria-pressed={selected === id} tabIndex={interactive ? 0 : -1}
                aria-label={`Stage ${k + 1}, ${stageLabel(id)}: ${STATE_SHORT[sn.state]}${mk ? `, ${MARK_TEXT[mk]}` : ''}${fin ? `, ${fmtMs(sn.duration)}` : ''}${nf ? `, ${nf} finding${nf > 1 ? 's' : ''}` : ''}`}
                onClick={() => interactive && onSelect?.(id)}>
                <span className="bb-top"><span className="bb-n">{waves.flat().indexOf(id) + 1}</span><span className="bb-name">{stageLabel(id)}</span></span>
                <span className="bb-row"><span className="bb-state">{STATE_SHORT[sn.state]}</span>
                  {fin ? <span className="bb-ms">{fmtMs(sn.duration)}</span> : null}
                  {nf ? <span className="bb-f" title={`${nf} finding(s)`}>{nf}</span> : null}</span>
                {mk ? <span className={`bb-mark mk-${mk}`}>{MARK_TEXT[mk]}</span> : null}
              </button>
            </div>);
        })}
      </div>
      {interactive && (
        <div className="s3d-ui s3d-tools wg-tools" role="toolbar" aria-label="Camera">
          <button type="button" aria-label="Zoom in" onClick={() => api.current?.dolly(0.8)}>＋</button>
          <button type="button" aria-label="Zoom out" onClick={() => api.current?.dolly(1.25)}>－</button>
          <button type="button" onClick={() => { if (selected) onSelect?.(selected); api.current?.reset(); }}>Reset view</button>
          <button type="button" aria-label="Tilt camera" onClick={() => api.current?.tilt()}>Tilt</button>
          <span className="s3d-hint">Drag to orbit · right-drag to pan · Ctrl+wheel to zoom · click a block</span>
        </div>)}
      {running.length > 1 && <div className="s3d-ui s3d-conc" role="status">{running.length} stages executing at the same instant: {running.map(stageLabel).join(' ∥ ')}</div>}
      {overlay}
    </div>
  );
}
