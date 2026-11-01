import { Link, Route, Routes } from 'react-router-dom';
import AppShell from './components/AppShell';
import { EmptyState } from './components/ui';
import Home from './pages/cc/Home';
import Workspace from './pages/one/Workspace';
import Demo from './pages/cc/Demo';
import AuditTrail from './pages/cc/AuditTrail';
import Studio from './pages/cc/Studio';
import ControlRoom from './pages/cc/ControlRoom';
import Present from './pages/cc/Present';
import Overview from './pages/cc/Overview';
import Claims from './pages/cc/Claims';
import Investigation from './pages/cc/Investigation';
import Workflow from './pages/cc/Workflow';
import ReviewQueue from './pages/cc/ReviewQueue';
import PolicyLibrary from './pages/cc/PolicyLibrary';
import DataLab from './pages/cc/DataLab';
import Experiments from './pages/cc/Experiments';
import Settings from './pages/cc/Settings';
// Existing (legacy) validator pages, preserved and mounted inside the Policy Library / legacy area.
import { PolicyList, PolicyDetail } from './pages/PolicyBrowser';
import PolicySourceViewer from './pages/PolicySourceViewer';
import LegacyDashboard from './pages/Dashboard';
import NewClaim from './pages/NewClaim';
import ClaimsHistory from './pages/ClaimsHistory';
import ClaimDetail from './pages/ClaimDetail';
import RuleEvidence from './pages/RuleEvidence';
import Documentation from './pages/Documentation';

const Legacy = ({ children }: { children: React.ReactNode }) => (
  <div className="legacy-scope">
    <p className="small"><Link to="/policies">← Policy library</Link></p>
    {children}
  </div>
);

export default function ClassicApp() {
  return (
      <AppShell>
        <Routes>
          <Route path="/one" element={<Workspace />} />
          <Route path="/about" element={<Home />} />
          <Route path="/demo" element={<Demo />} />
          <Route path="/audit" element={<AuditTrail />} />
          <Route path="/studio" element={<Studio />} />
          <Route path="/control-room" element={<ControlRoom />} />
          <Route path="/present" element={<Present />} />
          <Route path="/operations" element={<Overview />} />
          <Route path="/claims" element={<Claims />} />
          <Route path="/claims/:caseId" element={<Investigation />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/workflow" element={<Workflow />} />
          <Route path="/policies" element={<PolicyLibrary />} />
          <Route path="/policies/list" element={<Legacy><PolicyList /></Legacy>} />
          <Route path="/policies/:policyId" element={<Legacy><PolicyDetail /></Legacy>} />
          <Route path="/policy-source-viewer" element={<Legacy><PolicySourceViewer /></Legacy>} />
          <Route path="/data" element={<DataLab />} />
          <Route path="/experiments" element={<Experiments />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/legacy" element={<Legacy><LegacyDashboard /></Legacy>} />
          <Route path="/legacy/new" element={<Legacy><NewClaim /></Legacy>} />
          <Route path="/legacy/claims" element={<Legacy><ClaimsHistory /></Legacy>} />
          <Route path="/legacy/claims/:claimId" element={<Legacy><ClaimDetail /></Legacy>} />
          <Route path="/legacy/claims/:claimId/rules/:ruleId" element={<Legacy><RuleEvidence /></Legacy>} />
          <Route path="/legacy/documentation" element={<Legacy><Documentation /></Legacy>} />
          <Route path="*" element={<EmptyState title="Page not found"><Link to="/">Go to the home page</Link></EmptyState>} />
        </Routes>
      </AppShell>
  );
}
