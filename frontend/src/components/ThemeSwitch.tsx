import { useEffect, useState } from 'react';

type Choice = 'system' | 'light' | 'dark';
const KEY = 'clearclaim.theme';

function read(): Choice {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch { return 'system'; }
}

/** Light / Dark / System. Stored per browser only; "System" follows the OS setting. */
export default function ThemeSwitch() {
  const [choice, setChoice] = useState<Choice>(read);
  useEffect(() => {
    const el = document.documentElement;
    if (choice === 'system') delete el.dataset.theme; else el.dataset.theme = choice;
    try { if (choice === 'system') localStorage.removeItem(KEY); else localStorage.setItem(KEY, choice); } catch { /* storage unavailable */ }
  }, [choice]);
  return (
    <div className="theme-switch" role="group" aria-label="Colour theme">
      {(['light', 'dark', 'system'] as Choice[]).map((c) => (
        <button key={c} type="button" aria-pressed={choice === c} onClick={() => setChoice(c)}>
          {c === 'light' ? 'Light' : c === 'dark' ? 'Dark' : 'System'}
        </button>
      ))}
    </div>
  );
}
