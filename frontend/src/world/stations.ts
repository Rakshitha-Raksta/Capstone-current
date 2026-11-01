// The ClearClaim spatial model: one continuous 3D environment with eight stations. A station is a
// camera pose plus which districts are emphasised; every station is a place in the same world, so
// moving between them is a camera flight, not a page change. Kept free of Three.js (plain numbers)
// so the 2D shell can import it without pulling the 3D bundle.

export type ViewId = 'overview' | 'investigate' | 'departments' | 'policy' | 'history' | 'lab' | 'correct' | 'audit';
export type V3 = [number, number, number];

export interface Station {
  id: ViewId; n: number; title: string; short: string; question: string;
  /** district the camera flies to (world coordinates) */
  pos: V3; target: V3;
  /** stage labels on the central workflow deck are readable from this station */
  deckLabels: boolean;
}

/** District anchors. The workflow deck is the hub at the origin; the other districts surround it. */
export const DISTRICT: Record<'deck' | 'policy' | 'history' | 'lab' | 'audit', V3> = {
  deck: [0, 0, 0],
  policy: [-44, 0, -2],
  history: [44, 0, -2],
  lab: [0, 0, -38],
  audit: [0, 0, 30],
};

const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

export const STATIONS: Station[] = [
  { id: 'overview', n: 1, title: 'Operations overview', short: 'Overview', question: 'What needs attention right now?',
    pos: [-4, 78, 80], target: [0, 0, -4], deckLabels: false },
  { id: 'investigate', n: 2, title: 'Encounter & blocker investigation', short: 'Investigate', question: 'Why is this claim blocked, and what does it affect?',
    pos: [-3, 18, 21], target: [0, 0, 0.5], deckLabels: true },
  { id: 'departments', n: 3, title: 'Department workflow', short: 'Departments', question: 'Which desk owns each step, and where is the work waiting?',
    pos: [0, 38, 22], target: [0, 0, 1], deckLabels: true },
  { id: 'policy', n: 4, title: 'Policy intelligence', short: 'Policy', question: 'Which cited rules applied to this encounter, and with what outcome?',
    pos: add(DISTRICT.policy, [7, 15, 21]), target: add(DISTRICT.policy, [0, 2.5, -3]), deckLabels: false },
  { id: 'history', n: 5, title: 'Historical-case intelligence', short: 'History', question: 'How does this encounter compare with its historical peers?',
    pos: add(DISTRICT.history, [-6, 15, 24]), target: add(DISTRICT.history, [0, 3, 0]), deckLabels: false },
  { id: 'lab', n: 6, title: 'Execution laboratory', short: 'Lab', question: 'How do sequential and dependency-aware execution compare when measured?',
    pos: add(DISTRICT.lab, [0, 24, 26]), target: add(DISTRICT.lab, [0, 0, -1]), deckLabels: false },
  { id: 'correct', n: 7, title: 'Correction preview & verification', short: 'Correct', question: 'What will a correction re-run, and what actually changed?',
    pos: [4, 17, 20], target: [0, 0, 0.5], deckLabels: true },
  { id: 'audit', n: 8, title: 'Audit history & research results', short: 'Audit', question: 'Is the record intact, and what do the experiments show?',
    pos: add(DISTRICT.audit, [0, 13, 20]), target: add(DISTRICT.audit, [0, 1, 0]), deckLabels: false },
];

export const STATION: Record<ViewId, Station> = Object.fromEntries(STATIONS.map((s) => [s.id, s])) as Record<ViewId, Station>;
export const isView = (v: string | undefined): v is ViewId => !!v && v in STATION;

/**
 * Departments group the stages by the `owner` the backend's DAG declares for each stage
 * (app/clearclaim/dag.py). The grouping is a presentation of those owners, not an organisational model.
 */
export interface Department { id: string; title: string; owners: string[]; color: number; css: string; note: string }
export const DEPARTMENTS: Department[] = [
  { id: 'clinical', title: 'Clinical records & discharge readiness', owners: ['Data intake', 'Medical records / coding', 'Discharge desk'],
    color: 0x38bdf8, css: '#38bdf8', note: 'Administrative readiness only; ClearClaim does not assess clinical fitness for discharge.' },
  { id: 'billing', title: 'Billing & financial clearance', owners: ['Billing', 'Revenue integrity'], color: 0xf59e0b, css: '#f59e0b',
    note: 'Bill lines are checked against the synthetic rate card and the documented stay.' },
  { id: 'insurance', title: 'Insurance eligibility & policy checks', owners: ['Insurance desk'], color: 0xa78bfa, css: '#a78bfa',
    note: 'Cited rule checks and a rule-based estimate. Not insurer adjudication.' },
  { id: 'analytics', title: 'Historical comparison (advisory)', owners: ['Analytics (advisory)'], color: 0x2dd4bf, css: '#2dd4bf',
    note: 'Peer-group outlier screen; advisory only.' },
  { id: 'review', title: 'Issue resolution & human review', owners: ['ClearClaim'], color: 0xf472b6, css: '#f472b6',
    note: 'Root-blocker analysis and routing to the owning desk; people decide.' },
  { id: 'claims', title: 'Documentation & claim preparation', owners: ['Claims desk'], color: 0x34d399, css: '#34d399',
    note: 'Prepares a FHIR-shaped packet. Nothing is submitted to any insurer.' },
];
export const departmentOf = (owner: string | undefined) => DEPARTMENTS.find((d) => owner && d.owners.includes(owner)) || null;
