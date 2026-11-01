import React, { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import { humanize } from '../lib/format';

/* ---------------- badges ---------------- */

const STATUS_CLASS: Record<string, string> = {
  BLOCKED: 'b-blocked', NEEDS_REVIEW: 'b-review', PREPARATION_COMPLETE: 'b-complete', ERROR: 'b-blocked',
  NEW: 'b-info', PROCESSING: 'b-info',
  BLOCKING: 'b-blocked', REVIEW: 'b-review', ADVISORY: 'b-advisory', INFO: 'b-info',
  OPEN: 'b-review', RESOLVED_BY_CORRECTION: 'b-complete', ACCEPTED_WITH_JUSTIFICATION: 'b-complete',
  CONFIRMED_BLOCKED: 'b-blocked', SUPERSEDED: 'b-info',
  SUCCEEDED: 'b-complete', REUSED: 'b-info', FAILED: 'b-blocked', SKIPPED_DEPENDENCY_FAILED: 'b-blocked',
  RUNNING: 'b-info', COMPLETED: 'b-complete',
  ROOT: 'b-blocked', CONSEQUENCE: 'b-outline',
};
const STATUS_TEXT: Record<string, string> = {
  PREPARATION_COMPLETE: 'Preparation complete', NEEDS_REVIEW: 'Needs review', BLOCKED: 'Blocked',
  RESOLVED_BY_CORRECTION: 'Resolved by correction', ACCEPTED_WITH_JUSTIFICATION: 'Accepted with justification',
  CONFIRMED_BLOCKED: 'Confirmed blocked', SKIPPED_DEPENDENCY_FAILED: 'Skipped (dependency failed)',
  ROOT: 'Root blocker', CONSEQUENCE: 'Consequence',
};

export function Badge({ value, title }: { value: string | null | undefined; title?: string }) {
  if (!value) return <span className="faint">—</span>;
  return (
    <span className={`badge ${STATUS_CLASS[value] || 'b-info'}`} title={title}>
      <span className="dot" aria-hidden="true" />
      {STATUS_TEXT[value] || humanize(value)}
    </span>
  );
}

/* ---------------- buttons ---------------- */

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md';
  busy?: boolean;
};
export function Button({ variant = 'secondary', size = 'md', busy, children, className = '', disabled, ...rest }: BtnProps) {
  const v = variant === 'secondary' ? '' : `btn-${variant}`;
  return (
    <button {...rest} className={`btn ${v} ${size === 'sm' ? 'btn-sm' : ''} ${className}`} disabled={disabled || busy}
      aria-busy={busy || undefined}>
      {busy ? 'Working…' : children}
    </button>
  );
}

/* ---------------- cards ---------------- */

export function Card({ title, meta, actions, children, flush, className = '', id }: {
  title?: React.ReactNode; meta?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode;
  flush?: boolean; className?: string; id?: string;
}) {
  return (
    <section className={`card ${className}`} id={id} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || actions || meta) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {meta && <span className="meta">{meta}</span>}
          {actions && <div style={{ marginLeft: meta ? 8 : 'auto' }} className="row">{actions}</div>}
        </div>
      )}
      <div className={`card-body ${flush ? 'flush' : ''}`}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

/* ---------------- states ---------------- */

export function Skeleton({ rows = 3, height = 14 }: { rows?: number; height?: number }) {
  return (
    <div role="status" aria-busy="true" aria-label="Loading" className="stack" style={{ gap: 8 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height, width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children && <div className="small">{children}</div>}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-state" role="alert">
      <strong>Could not load.</strong> {message}
      {onRetry && <> <Button size="sm" onClick={onRetry} style={{ marginLeft: 8 }}>Retry</Button></>}
    </div>
  );
}

/* ---------------- tabs ---------------- */

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: string; count?: number }[]; value: string; onChange: (id: string) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const j = (i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    onChange(tabs[j].id);
    refs.current[j]?.focus();
  };
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t, i) => (
        <button key={t.id} ref={(el) => { refs.current[i] = el; }} role="tab" aria-selected={value === t.id}
          tabIndex={value === t.id ? 0 : -1} onClick={() => onChange(t.id)} onKeyDown={(e) => onKey(e, i)}>
          {t.label}{t.count !== undefined && <span className="faint"> {t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ---------------- dialog ---------------- */

export function Dialog({ open, title, onClose, children, footer }: {
  open: boolean; title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>('input, select, textarea, button');
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Tab' && ref.current) {
        const els = Array.from(ref.current.querySelectorAll<HTMLElement>('input, select, textarea, button, a[href]'))
          .filter((x) => !x.hasAttribute('disabled'));
        if (!els.length) return;
        const [a, b] = [els[0], els[els.length - 1]];
        if (e.shiftKey && document.activeElement === a) { e.preventDefault(); b.focus(); }
        else if (!e.shiftKey && document.activeElement === b) { e.preventDefault(); a.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); prev?.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
        <div className="dialog-head"><h2 id={titleId}>{title}</h2>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close" style={{ marginLeft: 'auto' }}>✕</Button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------------- form fields ---------------- */

export function Field({ label, help, error, children, htmlFor }: {
  label: React.ReactNode; help?: string; error?: string | null; children: React.ReactNode; htmlFor: string;
}) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {help && <span className="help" id={`${htmlFor}-help`}>{help}</span>}
      {error && <span className="err" role="alert">{error}</span>}
    </div>
  );
}

/* ---------------- toasts ---------------- */

type Toast = { id: number; text: string; kind: 'ok' | 'error' };
const ToastCtx = createContext<(text: string, kind?: 'ok' | 'error') => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: 'ok' | 'error' = 'ok') => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, kind }]);
    window.setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === 'error' ? 8000 : 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite" role="status">
        {items.map((t) => <div key={t.id} className={`toast ${t.kind === 'error' ? 'error' : ''}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------------- pagination ---------------- */

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  return (
    <nav className="pager" aria-label="Pagination">
      <span>{from}–{Math.min(total, page * pageSize)} of {total}</span>
      <span className="spacer" />
      <Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
      <span>Page {page} of {pages}</span>
      <Button size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
    </nav>
  );
}

/* ---------------- expandable section (content rendered only while open) ---------------- */

export function Disclosure({ title, summary, children, defaultOpen = false, id }: {
  title: string; summary?: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean; id?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details className="disclosure" id={id} open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary><span className="disc-title">{title}</span>{summary && <span className="disc-sum">{summary}</span>}</summary>
      {open && <div className="disc-body">{children}</div>}
    </details>
  );
}

/* ---------------- numbered step heading ---------------- */

export function StepHead({ n, title, sub }: { n: number | string; title: string; sub?: React.ReactNode }) {
  return (
    <div className="step-head">
      <span className="step-num" aria-hidden="true">{n}</span>
      <div><h2>{title}</h2>{sub && <div className="small muted">{sub}</div>}</div>
    </div>
  );
}

export function PageHead({ title, sub, actions }: { title: string; sub?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="cc-page-head">
      <div><h1>{title}</h1>{sub && <div className="sub">{sub}</div>}</div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}
