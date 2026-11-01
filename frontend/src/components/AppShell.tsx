import React, { createContext, useContext, useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { api } from '../lib/api';
import { Button } from './ui';
import ThemeSwitch from './ThemeSwitch';

export interface Meta {
  product: string; version: string; environment: string; data_mode: string; write_protected: boolean;
  execution_mode: string; rerun_policy: string; routing_policy: string; disclaimer: string;
  active_dataset: null | { source_label: string; manifest_mode: string; generator_version: string; n_encounters: number };
}
const MetaCtx = createContext<{ meta: Meta | null; refreshCounts: () => void; counts: { open: number; attention: number } }>({
  meta: null, refreshCounts: () => undefined, counts: { open: 0, attention: 0 },
});
export const useMeta = () => useContext(MetaCtx);

type NavItem = { to: string; label: string; icon: string; count?: 'open' | 'attention'; end?: boolean };
const NAV: NavItem[] = [
  { to: '/', label: '3D command center', icon: '◈', end: true },
  { to: '/one', label: 'ClearClaim One workspace', icon: '◇' },
  { to: '/about', label: 'About ClearClaim', icon: '◱' },
  { to: '/control-room', label: 'Agent Control Room', icon: '◎' },
  { to: '/demo', label: 'Guided demo', icon: '▶' },
  { to: '/studio', label: 'Execution studio', icon: '⧉' },
  { to: '/claims', label: 'Cases', icon: '≡', count: 'attention' },
  { to: '/review', label: 'Review queue', icon: '✓', count: 'open' },
  { to: '/experiments', label: 'Experiments', icon: '∿' },
  { to: '/audit', label: 'Audit trail', icon: '⧗' },
];
const ADVANCED: NavItem[] = [
  { to: '/operations', label: 'Operations dashboard', icon: '▤' },
  { to: '/workflow', label: 'Workflow & reruns', icon: '⇉' },
  { to: '/policies', label: 'Policy library', icon: '§' },
  { to: '/data', label: 'Data laboratory', icon: '⌗' },
  { to: '/settings', label: 'Settings', icon: '⚙' },
];
const isAdvancedPath = (p: string) => ADVANCED.some((a) => p === a.to || p.startsWith(`${a.to}/`))
  || p.startsWith('/legacy') || p.startsWith('/policy-source-viewer');

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [counts, setCounts] = useState({ open: 0, attention: 0 });
  const [navOpen, setNavOpen] = useState(false);
  const loc = useLocation();
  const [advOpen, setAdvOpen] = useState(() => isAdvancedPath(loc.pathname));
  useEffect(() => { if (isAdvancedPath(loc.pathname)) setAdvOpen(true); }, [loc.pathname]);

  const refreshCounts = React.useCallback(() => {
    api.overview().then((o) => {
      const sc = o.status_counts || {};
      setCounts({
        open: (o.work_queues || []).reduce((a: number, q: { open: number }) => a + q.open, 0),
        attention: (sc.BLOCKED || 0) + (sc.NEEDS_REVIEW || 0) + (sc.ERROR || 0),
      });
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMeta(null));
    api.health().then((h) => setHealthy(h.status === 'ok')).catch(() => setHealthy(false));
    refreshCounts();
  }, [refreshCounts]);
  useEffect(() => { setNavOpen(false); }, [loc.pathname]);

  return (
    <MetaCtx.Provider value={{ meta, refreshCounts, counts }}>
      <a href="#main" className="visually-hidden">Skip to content</a>
      <div className={`cc-shell ${loc.pathname === '/' ? 'ws-mode' : ''}`}>
        <nav className={`cc-nav ${navOpen ? 'open' : ''}`} aria-label="Primary">
          <div className="cc-brand">
            <div className="cc-brand-mark" aria-hidden="true">C</div>
            <div><div className="cc-brand-name">ClearClaim</div><div className="cc-brand-sub">Discharge &amp; claim preparation</div></div>
          </div>
          <div className="cc-nav-section">Start here</div>
          {NAV.map((n) => <NavItemLink key={n.to} n={n} counts={counts} />)}
          <button type="button" className="cc-nav-group" aria-expanded={advOpen} aria-controls="nav-advanced"
            onClick={() => setAdvOpen((v) => !v)}>
            <span aria-hidden="true">{advOpen ? '▾' : '▸'}</span> Advanced tools
          </button>
          {advOpen && <div id="nav-advanced">{ADVANCED.map((n) => <NavItemLink key={n.to} n={n} counts={counts} />)}</div>}
          <div className="cc-nav-foot">
            <div>Data: <strong>{meta ? (meta.active_dataset?.source_label || 'none imported') : '…'}</strong></div>
            <div>Mode: <strong>{meta?.data_mode || '…'}</strong> · v{meta?.version || '…'}</div>
          </div>
        </nav>
        <div className="cc-main">
          <header className="cc-topbar">
            <Button className="cc-menu-btn" size="sm" variant="ghost" aria-label="Open navigation" aria-expanded={navOpen}
              onClick={() => setNavOpen((v) => !v)}>☰</Button>
            {meta && meta.active_dataset?.source_label === 'SYNTHETIC' && (
              <span className="badge b-advisory" title="All patient, billing and insurance records are synthetic">Synthetic demo data</span>
            )}
            {meta && meta.active_dataset && meta.active_dataset.source_label !== 'SYNTHETIC' && (
              <span className="badge b-accent" title="Clinical encounters are real de-identified MIMIC-IV records; bills, policies, claim dates, checklists and notes are synthetic">
                {meta.active_dataset.source_label}: real encounters · synthetic billing &amp; insurance</span>
            )}
            <span className="small muted disclaimer">Internal preparation status only — not an insurer decision</span>
            <span className="spacer" />
            {meta?.write_protected && <span className="badge b-outline">Write-protected</span>}
            <ThemeSwitch />
            <span className="small muted" aria-live="polite">
              {healthy === null ? 'API …' : healthy
                ? <span className="badge b-complete" title="API and database reachable"><span className="dot" aria-hidden="true" />API online</span>
                : <span className="badge b-blocked" title="Start the backend on port 5001"><span className="dot" aria-hidden="true" />API unreachable</span>}
            </span>
          </header>
          <main id="main" className="cc-page" tabIndex={-1}>{children}</main>
        </div>
      </div>
    </MetaCtx.Provider>
  );
}

function NavItemLink({ n, counts }: { n: NavItem; counts: { open: number; attention: number } }) {
  return (
    <NavLink to={n.to} end={n.end} className={({ isActive }) => (isActive ? 'active' : '')}>
      <span aria-hidden="true" style={{ width: 14, textAlign: 'center', opacity: 0.8 }}>{n.icon}</span>
      {n.label}
      {n.count && counts[n.count] > 0 && <span className="count" aria-label={`${counts[n.count]} items`}>{counts[n.count]}</span>}
    </NavLink>
  );
}
