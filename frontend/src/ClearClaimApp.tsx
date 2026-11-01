import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import { ToastProvider } from './components/ui';
import CommandCenter from './pages/space/CommandCenter';

// The classic pages (and the legacy validator UI) load on demand, so the 3D command center starts lean.
const ClassicApp = lazy(() => import('./ClassicApp'));

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        {/* the 3D command center is full-bleed; both paths render the same component, so the 3D world persists */}
        <Route path="/" element={<CommandCenter />} />
        <Route path="/space/:view" element={<CommandCenter />} />
        <Route path="*" element={<Suspense fallback={<div className="page-loading" role="status">Loading…</div>}><ClassicApp /></Suspense>} />
      </Routes>
    </ToastProvider>
  );
}

